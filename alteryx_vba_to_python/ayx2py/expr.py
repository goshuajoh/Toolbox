"""Alteryx expression language -> Python source.

A real tokenizer and recursive-descent parser; no regex substitution on the expression text. Nothing recurses on the
call stack: recursive steps are generators driven by run(), and long chains are flat, so any length or depth works.
Standard library only.

    ast = parse("IF [A]='x' THEN 1 ELSE 2 ENDIF")
    canon(ast)                 canonical text (used to recognise identical rules)
    fields(ast)                field names the expression reads
    to_python(ast)             a Python expression using `r[...]` for fields and `rt.` helpers
    to_function(ast, "f")      lines of a `def f(r):` (top-level IF becomes if/elif/else)

Anything the translator does not know raises ExprError; the caller turns the tool into a stub.
"""
import re
import textwrap

KEYWORDS = {"IF", "THEN", "ELSEIF", "ELSE", "ENDIF", "AND", "OR", "NOT", "IN", "TRUE", "FALSE", "NULL"}

# Alteryx function (lower case) -> (runtime helper, min args, max args, returns bool)
FUNCS = {
    # conditional / tests
    "iif": ("iif", 3, 3, False), "switch": ("switch", 2, 99, False),
    "isnull": ("is_null", 1, 1, True), "isempty": ("is_empty", 1, 1, True),
    "isnumber": ("is_number", 1, 1, True), "isstring": ("is_string", 1, 1, True),
    "isinteger": ("is_integer", 1, 1, True),
    # conversion
    "tonumber": ("to_number", 1, 4, False), "tostring": ("to_string", 1, 4, False),
    "tointeger": ("to_integer", 1, 1, False),
    "chartoint": ("char_to_int", 1, 1, False), "charfromint": ("char_from_int", 1, 1, False),
    # math
    "abs": ("abs_", 1, 1, False), "ceil": ("ceil", 1, 2, False), "floor": ("floor", 1, 2, False),
    "round": ("round_", 2, 2, False), "pow": ("pow_", 2, 2, False), "sqrt": ("sqrt", 1, 1, False),
    "exp": ("exp", 1, 1, False), "log": ("log", 1, 1, False), "log10": ("log10", 1, 1, False),
    "mod": ("mod", 2, 2, False), "min": ("min_", 1, 99, False), "max": ("max_", 1, 99, False),
    # string
    "contains": ("contains", 2, 3, True), "startswith": ("starts_with", 2, 3, True),
    "endswith": ("ends_with", 2, 3, True), "findstring": ("find_string", 2, 2, False),
    "left": ("left", 2, 2, False), "right": ("right", 2, 2, False),
    "substring": ("substring", 2, 3, False), "length": ("length", 1, 1, False),
    "lowercase": ("lowercase", 1, 1, False), "uppercase": ("uppercase", 1, 1, False),
    "titlecase": ("titlecase", 1, 1, False), "trim": ("trim", 1, 2, False),
    "trimleft": ("trim_left", 1, 2, False), "trimright": ("trim_right", 1, 2, False),
    "padleft": ("pad_left", 3, 3, False), "padright": ("pad_right", 3, 3, False),
    "replace": ("replace", 3, 3, False), "replacechar": ("replace_char", 3, 3, False),
    "replacefirst": ("replace_first", 3, 3, False), "reversestring": ("reverse_string", 1, 1, False),
    "getword": ("get_word", 2, 2, False), "countwords": ("count_words", 1, 1, False),
    "stripquotes": ("strip_quotes", 1, 1, False),
    "regex_replace": ("regex_replace", 3, 4, False), "regex_match": ("regex_match", 2, 3, True),
    "regex_countmatches": ("regex_count_matches", 2, 3, False),
    # date-time
    "todate": ("to_date", 1, 1, False), "todatetime": ("to_datetime", 1, 1, False),
    "datetimediff": ("datetime_diff", 3, 3, False), "datetimeadd": ("datetime_add", 3, 3, False),
    "datetimeformat": ("datetime_format", 2, 3, False), "datetimeparse": ("datetime_parse", 2, 3, False),
    "datetimetrim": ("datetime_trim", 2, 2, False), "datetimenow": ("datetime_now", 0, 0, False),
    "datetimetoday": ("datetime_today", 0, 0, False), "datetimeyear": ("datetime_part_year", 1, 1, False),
    "datetimemonth": ("datetime_part_month", 1, 1, False), "datetimeday": ("datetime_part_day", 1, 1, False),
    "datetimehour": ("datetime_part_hour", 1, 1, False),
    "datetimeminutes": ("datetime_part_minute", 1, 1, False),
    "datetimeseconds": ("datetime_part_second", 1, 1, False),
    "datetimefirstofmonth": ("datetime_first_of_month", 0, 0, False),
    "datetimelastofmonth": ("datetime_last_of_month", 0, 0, False),
    # file
    "filegetfilename": ("file_get_filename", 1, 1, False), "filegetext": ("file_get_ext", 1, 1, False),
    "filegetdir": ("file_get_dir", 1, 1, False), "fileexists": ("file_exists", 1, 1, True),
}
# Functions whose result depends on the clock: translated, but reported as a caveat.
VOLATILE = {"datetimenow", "datetimetoday", "datetimefirstofmonth", "datetimelastofmonth"}

CMP = {"=": "eq", "==": "eq", "!=": "ne", "<>": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
ARITH = {"+": "add", "-": "sub", "*": "mul", "/": "div", "%": "mod"}


class ExprError(Exception):
    pass


# ----------------------------------------------------------------------------- tokenizer
def tokenize(text):
    """-> list of (kind, value, position); kind in NUM STR FIELD ID OP END."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise ExprError("unterminated /* comment at %d" % i)
            i = j + 2
        elif c in "'\"":
            j = text.find(c, i + 1)          # Alteryx string literals have no escape sequences
            if j < 0:
                raise ExprError("unterminated string at %d" % i)
            out.append(("STR", text[i + 1:j], i))
            i = j + 1
        elif c == "[":
            j = text.find("]", i + 1)
            if j < 0:
                raise ExprError("unterminated [field] at %d" % i)
            out.append(("FIELD", text[i + 1:j], i))
            i = j + 1
        elif c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = re.compile(r"(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?").match(text, i)
            out.append(("NUM", m.group(0), i))
            i = m.end()
        elif c.isalpha() or c == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(("ID", text[i:j], i))
            i = j
        else:
            for op in ("==", "!=", "<>", "<=", ">=", "&&", "||"):
                if text.startswith(op, i):
                    break
            else:
                op = c
                if c not in "+-*/%()=<>!,":
                    raise ExprError("unexpected character %r at %d" % (c, i))
            out.append(("OP", op, i))
            i += len(op)
    out.append(("END", "", n))
    return out


# ----------------------------------------------------------------------------- parser
# Precedence as documented by Alteryx (Functions > Operators), highest first:
#   unary -   >   * / %   >   + -   >   < <= > >= IN NOT   >   = == !=   >   AND && OR ||
# AND and OR have the SAME rank and group left to right:  a OR b AND c  is  (a OR b) AND c.
# Set AND_BINDS_TIGHTER = True to parse them the conventional way instead (AND before OR).
AND_BINDS_TIGHTER = False


# ----------------------------------------------------------------------------- recursion without the call stack
def run(gen):
    """Drive a recursive function written as a generator: it yields the generator of each sub-call and gets the
    sub-call's result back. The pending calls sit in a list, so a rule nested ten thousand levels deep needs no
    recursion limit and no large stack."""
    stack, value = [gen], None
    while stack:
        try:
            sub = stack[-1].send(value)
        except StopIteration as done:
            stack.pop()
            value = done.value
            continue
        stack.append(sub)
        value = None
    return value


def _join(kind, node, item):
    """a OR b OR c is one ("or", a, b, c) node, so long chains do not nest once per term."""
    if node[0] == kind and isinstance(node, list):
        node.append(item)
        return node
    if node[0] == kind:
        return [kind, *node[1:], item]
    return [kind, node, item]


def _join_arith(op, node, item):
    """a + b + c is one ("arith", "add", a, b, c) node, evaluated left to right."""
    if node[0] == "arith" and node[1] == op:
        if isinstance(node, list):
            node.append(item)
            return node
        return ["arith", op, *node[2:], item]
    return ["arith", op, node, item]


class _Parser:
    """Recursive descent, written as generators driven by run(): nesting depth costs memory, not stack."""

    def __init__(self, text):
        self.toks, self.i = tokenize(text), 0

    def peek(self):
        return self.toks[self.i]

    def kw(self, word):
        k, v, _ = self.toks[self.i]
        return k == "ID" and v.upper() == word

    def op(self, *ops):
        k, v, _ = self.toks[self.i]
        return k == "OP" and v in ops

    def take(self):
        self.i += 1
        return self.toks[self.i - 1]

    def need_kw(self, word):
        if not self.kw(word):
            self.fail("expected " + word)
        self.i += 1

    def need_op(self, op):
        if not self.op(op):
            self.fail("expected " + op)
        self.i += 1

    def fail(self, msg):
        k, v, pos = self.peek()
        raise ExprError("%s at position %d (found %s)" % (msg, pos, repr(v) if k != "END" else "end of expression"))

    def parse(self):
        node = yield self.or_()
        if self.peek()[0] != "END":
            self.fail("unexpected text")
        return node

    def or_(self):
        if AND_BINDS_TIGHTER:
            node = yield self.and_()
            while self.kw("OR") or self.op("||"):
                self.take()
                node = _join("or", node, (yield self.and_()))
            return node
        node = yield self.eq()
        while True:
            if self.kw("OR") or self.op("||"):
                kind = "or"
            elif self.kw("AND") or self.op("&&"):
                kind = "and"
            else:
                return node
            self.take()
            node = _join(kind, node, (yield self.eq()))

    def and_(self):
        node = yield self.eq()
        while self.kw("AND") or self.op("&&"):
            self.take()
            node = _join("and", node, (yield self.eq()))
        return node

    def eq(self):
        node = yield self.rel()
        while self.op("=", "==", "!=", "<>"):
            o = self.take()[1]
            node = ("cmp", CMP[o], node, (yield self.rel()))
        return node

    def _not_in(self):
        return self.kw("NOT") and self.toks[self.i + 1][0] == "ID" and self.toks[self.i + 1][1].upper() == "IN"

    def rel(self):
        if (self.kw("NOT") and not self._not_in()) or self.op("!"):
            self.take()
            return ("not", (yield self.rel()))
        node = yield self.add()
        while True:
            if self.op("<", "<=", ">", ">="):
                o = self.take()[1]
                node = ("cmp", CMP[o], node, (yield self.add()))
            elif self.kw("IN") or self._not_in():
                neg = self.kw("NOT")
                self.i += 2 if neg else 1
                self.need_op("(")
                items = [(yield self.or_())]
                while self.op(","):
                    self.take()
                    items.append((yield self.or_()))
                self.need_op(")")
                node = ("in", node, tuple(items), neg)
            else:
                return node

    def add(self):
        node = yield self.mul()
        while self.op("+", "-"):
            o = self.take()[1]
            node = _join_arith(ARITH[o], node, (yield self.mul()))
        return node

    def mul(self):
        node = yield self.unary()
        while self.op("*", "/", "%"):
            o = self.take()[1]
            node = _join_arith(ARITH[o], node, (yield self.unary()))
        return node

    def unary(self):
        if self.op("-"):
            self.take()
            return ("neg", (yield self.unary()))
        if self.op("+"):
            self.take()
            return (yield self.unary())
        if self.op("!"):                       # "-!x" / "a + !b": rare, but legal
            self.take()
            return ("not", (yield self.unary()))
        return (yield self.primary())

    def primary(self):
        k, v, _ = self.peek()
        if k == "NUM":
            self.take()
            return ("num", v)
        if k == "STR":
            self.take()
            return ("str", v)
        if k == "FIELD":
            self.take()
            if re.match(r"(?i)row[+-]\d+:", v):
                raise ExprError("multi-row reference [%s] is not supported" % v)
            return ("field", v)
        if k == "OP" and v == "(":
            self.take()
            node = yield self.or_()
            self.need_op(")")
            return ("paren", node)
        if k == "ID":
            up = v.upper()
            if up == "IF":
                return (yield self.if_())
            if up in ("TRUE", "FALSE"):
                self.take()
                return ("bool", up == "TRUE")
            if up == "NULL":
                self.take()
                if self.op("("):
                    self.take()
                    self.need_op(")")
                return ("null",)
            if up in KEYWORDS:
                self.fail("unexpected keyword " + up)
            self.take()
            if not self.op("("):
                self.fail("function name %s must be followed by (" % v)
            self.take()
            args = []
            if not self.op(")"):
                args.append((yield self.or_()))
                while self.op(","):
                    self.take()
                    args.append((yield self.or_()))
            self.need_op(")")
            name = v.lower()
            if name not in FUNCS:
                raise ExprError("function %s() is not supported by the translator" % v)
            lo, hi = FUNCS[name][1], FUNCS[name][2]
            if not lo <= len(args) <= hi:
                raise ExprError("%s() takes %d to %d arguments, got %d" % (v, lo, hi, len(args)))
            return ("call", name, tuple(args))
        self.fail("unexpected token")
        yield None                                  # (a generator, like every parsing method)

    def if_(self):
        self.need_kw("IF")
        branches = []
        cond = yield self.or_()
        self.need_kw("THEN")
        branches.append((cond, (yield self.or_())))
        while self.kw("ELSEIF"):
            self.take()
            cond = yield self.or_()
            self.need_kw("THEN")
            branches.append((cond, (yield self.or_())))
        self.need_kw("ELSE")
        other = yield self.or_()
        self.need_kw("ENDIF")
        return ("if", tuple(branches), other)


_KINDS = {"num", "str", "field", "bool", "null", "or", "and", "not", "neg", "cmp", "arith", "in", "call", "if",
          "paren"}


def _is_node(x):
    return isinstance(x, (tuple, list)) and x and isinstance(x[0], str) and x[0] in _KINDS


def _strip(node):
    """Remove the ("paren", x) markers left by the parser, make every node a tuple, and flatten what nesting adds
    nothing to: a run of the same AND/OR, a left-nested run of the same arithmetic, and IF ... ELSE IF ... ENDIF ENDIF
    (an ELSE that is itself an IF), which becomes one IF with ELSEIFs."""
    while node[0] == "paren":
        node = node[1]
    k = node[0]
    if k in ("or", "and"):
        items = []
        for x in node[1:]:
            x = yield _strip(x)
            items += list(x[1:]) if x[0] == k else [x]
        return (k, *items)
    if k == "arith":
        items = []
        for x in node[2:]:
            items.append((yield _strip(x)))
        first = items[0]
        if first[0] == "arith" and first[1] == node[1]:          # (a + b) + c  is  a + b + c
            items = list(first[2:]) + items[1:]
        return ("arith", node[1], *items)
    if k == "if":
        branches, other = [], node
        while other[0] in ("if", "paren"):                          # ELSE IF ... ENDIF ENDIF -> ELSEIF
            if other[0] == "paren":
                other = other[1]
                continue
            for c, v in other[1]:
                branches.append(((yield _strip(c)), (yield _strip(v))))
            other = other[2]
        return ("if", tuple(branches), (yield _strip(other)))
    out = []
    for x in node:
        if _is_node(x):
            out.append((yield _strip(x)))
        elif isinstance(x, (tuple, list)):
            sub = []
            for y in x:
                sub.append((yield _strip(y)) if _is_node(y) else y)
            out.append(tuple(sub))
        else:
            out.append(x)
    return tuple(out)


def _raw(text):
    if text is None or not text.strip():
        raise ExprError("empty expression")
    return run(_Parser(text).parse())


DEEP_LIMIT = 20000          # recursion limit the command line sets, as a safety net only (nothing here recurses)


def parse(text):
    return run(_strip(_raw(text)))


def text_nesting(text):
    """Deepest nesting of brackets and IF ... ENDIF in a rule's text (strings and [fields] skipped)."""
    best = level = 0
    for k, v, _ in tokenize(text):
        up = v.upper() if k == "ID" else ""
        if (k == "OP" and v == "(") or up == "IF":
            level += 1
            best = max(best, level)
        elif (k == "OP" and v == ")") or up == "ENDIF":
            level -= 1
    return best


def too_deep(text):
    return "expression nested too deeply (%d levels)" % text_nesting(text)


def precedence_notes(text):
    """Places where the documented Alteryx precedence gives a different reading from the
    conventional one. Each note names the construct; the rule should be checked in Designer."""
    notes = []
    try:
        todo = [_raw(text)]
    except ExprError:
        return []
    while todo:
        n = todo.pop()
        if not _is_node(n):
            todo.extend(x for x in (n if isinstance(n, (tuple, list)) else ()) if isinstance(x, (tuple, list)))
            continue
        if n[0] in ("and", "or") and n[1][0] in ("and", "or") and n[1][0] != n[0]:
            notes.append("AND and OR are mixed without parentheses: read left to right, "
                         "so 'a OR b AND c' means '(a OR b) AND c'")
        if n[0] == "cmp" and n[1] in ("eq", "ne") and "not" in (n[2][0], n[3][0]):
            notes.append("NOT/! is applied before = : '!a = b' means '(!a) = b'")
        todo.extend(x for x in n[1:] if isinstance(x, (tuple, list)))
    return list(dict.fromkeys(notes))


# ----------------------------------------------------------------------------- walkers
def _children(node):
    k = node[0]
    if k in ("or", "and"):
        return node[1:]
    if k in ("not", "neg"):
        return node[1:]
    if k in ("cmp", "arith"):
        return node[2:]
    if k == "in":
        return (node[1],) + node[2]
    if k == "call":
        return node[2]
    if k == "if":
        return tuple(x for b in node[1] for x in b) + (node[2],)
    return ()


def walk(node):
    """Every node, parents before children, without recursion."""
    todo = [node]
    while todo:
        n = todo.pop()
        yield n
        todo.extend(reversed(_children(n)))


def depth(node):
    """How deeply the rule nests (an and/or or arithmetic chain counts once, however long)."""
    best, todo = 0, [(node, 1)]
    while todo:
        n, d = todo.pop()
        best = max(best, d)
        todo.extend((c, d + 1) for c in _children(n))
    return best


def fields(node):
    """Field names read by the expression, in first-use order (engine constants included)."""
    return list(dict.fromkeys(n[1] for n in walk(node) if n[0] == "field"))


def functions(node):
    return sorted({n[1] for n in walk(node) if n[0] == "call"})


def is_bool(node):
    k = node[0]
    return k in ("or", "and", "not", "cmp", "in", "bool") or (k == "call" and FUNCS[node[1]][3])


# A "spine": a left-deep run of binary operators of one family whose kind alternates, such as
# a AND b OR c AND d ... (Alteryx reads it left to right: ((a AND b) OR c) AND d). Same-kind runs are already one
# n-ary node; a spine is handled level by level in a loop, so its length costs nothing in depth.
_FAMILY = {"or": "bool", "and": "bool", "arith": "arith", "cmp": "cmp"}


def items_of(node):
    return list(node[1:]) if node[0] in ("or", "and") else list(node[2:])


def spine(node):
    """-> (base, levels): levels bottom-up; each level's first item is the level below (or the base)."""
    fam = _FAMILY.get(node[0])
    levels, cur = [], node
    while fam and _FAMILY.get(cur[0]) == fam:
        levels.append(cur)
        cur = items_of(cur)[0]
    return cur, levels[::-1]


def _word(level):
    return level[0] if level[0] in ("or", "and") else level[1]


_SYM = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=",
        "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}


def _canon(node):
    k = node[0]
    if k == "num":
        return node[1]
    if k == "str":
        return ('"%s"' if '"' not in node[1] else "'%s'") % node[1]
    if k == "field":
        return "[%s]" % node[1]
    if k == "bool":
        return "True" if node[1] else "False"
    if k == "null":
        return "Null()"
    if k in _FAMILY:
        base, levels = spine(node)
        out = ["(" * len(levels), (yield _canon(base))]
        for lv in levels:
            sym = " %s " % (lv[0].upper() if lv[0] in ("or", "and") else _SYM[lv[1]])
            for x in items_of(lv)[1:]:
                out.append(sym + (yield _canon(x)))
            out.append(")")
        return "".join(out)
    if k == "not":
        return "!" + (yield _canon(node[1]))
    if k == "neg":
        return "-" + (yield _canon(node[1]))
    if k == "in":
        items = []
        for x in node[2]:
            items.append((yield _canon(x)))
        return "(%s %sIN (%s))" % ((yield _canon(node[1])), "NOT " if node[3] else "", ", ".join(items))
    if k == "call":
        args = []
        for x in node[2]:
            args.append((yield _canon(x)))
        return "%s(%s)" % (node[1], ", ".join(args))
    if k == "if":
        parts = []
        for n, (c, v) in enumerate(node[1]):
            parts.append("%s %s THEN %s" % ("IF" if n == 0 else "ELSEIF", (yield _canon(c)), (yield _canon(v))))
        return " ".join(parts) + " ELSE %s ENDIF" % (yield _canon(node[2]))
    raise ExprError("unknown node " + k)


def canon(node):
    """Canonical one-line text: same rule -> same text whatever the spacing or keyword case."""
    return run(_canon(node))


# ----------------------------------------------------------------------------- emitter
CURRENT = {"_currentfield_": "cur", "_currentfieldname_": "cur_name", "_currentfieldtype_": "cur_type"}
HOIST_AT = 50               # nesting beyond which a part of a rule moves into a helper function of its own
SPINE_AT = 8                # an alternating chain with more levels than this is computed statement by statement
BRANCHES_AT = 5             # an IF inside an expression with more branches than this becomes a helper with if/return


def _nesting(text):
    best = level = 0
    for ch in text:
        if ch in "([{":
            level += 1
            best = max(best, level)
        elif ch in ")]}":
            level -= 1
    return best


class _Emitter:
    """Python text of an expression, built without recursion. Parts nested deeper than HOIST_AT, long alternating
    chains and many-branch IFs become helper functions (called at the same place, so evaluation stays lazy where
    Alteryx's is), so the module never nests deeply and Python compiles it."""

    def __init__(self, name, args, hoist=True):
        self.name, self.args, self.hoist = name, args, hoist
        self.helpers, self.count = [], 0
        self.call_args = ", ".join(a.split("=")[0].strip() for a in args.split(","))

    def helper(self, body):
        self.count += 1
        fname = "%s_h%d" % (self.name, self.count)
        self.helpers += ["def %s(%s):" % (fname, self.args)] + ["    " + x for x in body] + ["", ""]
        return "%s(%s)" % (fname, self.call_args), 1

    def truth(self, node):
        text, nest = yield self.py(node)
        return (text, nest) if is_bool(node) else ("rt.truth(%s)" % text, nest + 1)

    def py(self, node):
        text, nest = yield self._py(node)
        if self.hoist and nest > HOIST_AT:
            return self.helper(["return " + text])
        return text, nest

    def _py(self, node):
        k = node[0]
        if k == "num":
            v = node[1]
            return (str(int(v)) if re.fullmatch(r"\d+", v) else repr(float(v))), 0
        if k == "str":
            return repr(node[1]), 0
        if k == "bool":
            return ("True" if node[1] else "False"), 0
        if k == "null":
            return "None", 0
        if k == "field":
            name = node[1]
            if name.lower() in CURRENT:
                return CURRENT[name.lower()], 0
            if re.match(r"(?i)(engine|user|question)\.", name):
                return "rt.constant(%r)" % name, 1
            return "r[%r]" % name, 1
        if k in _FAMILY:
            base, levels = spine(node)
            if not self.hoist and len(levels) > SPINE_AT:           # one flat call: rt.fold(a, "or", lambda: b, ...)
                text, _ = yield (self.truth(base) if k in ("or", "and") else self.py(base))
                steps = []
                for lv in levels:
                    for x in items_of(lv)[1:]:
                        t, _ = yield (self.truth(x) if lv[0] in ("or", "and") else self.py(x))
                        steps.append("%r, lambda: %s" % (_word(lv), t))
                return "rt.fold(%s, %s)" % (text, ", ".join(steps)), 1
            if self.hoist and len(levels) > SPINE_AT:               # v = a / v = v or b / v = v and c ...
                body = []
                text, _ = yield (self.truth(base) if k in ("or", "and") else self.py(base))
                body.append("v = %s" % text)
                for lv in levels:
                    for x in items_of(lv)[1:]:
                        if lv[0] in ("or", "and"):
                            text, _ = yield self.truth(x)
                            body.append("v = v %s %s" % (lv[0], text))
                        else:
                            text, _ = yield self.py(x)
                            body.append("v = rt.%s(v, %s)" % (lv[1], text))
                return self.helper(body + ["return v"])
            if k in ("or", "and"):                                  # Python's or/and is one flat node however long
                parts, nest = [], 0
                for x in node[1:]:
                    t, n = yield self.truth(x)
                    parts.append(t)
                    nest = max(nest, n)
                return "(%s)" % (" %s " % k).join(parts), nest + 1
            parts, nest = [], 0
            for x in node[2:]:
                t, n = yield self.py(x)
                parts.append(t)
                nest = max(nest, n)
            if k == "arith" and len(parts) > 2:                     # a + b + c ...: one call, not one per term
                return "rt.chain(%r, %s)" % (node[1], ", ".join(parts)), nest + 1
            return "rt.%s(%s)" % (node[1], ", ".join(parts)), nest + 1
        if k == "not":
            t, n = yield self.truth(node[1])
            return "(not %s)" % t, n + 1
        if k == "neg":
            t, n = yield self.py(node[1])
            return "rt.neg(%s)" % t, n + 1
        if k == "in":
            subject, nest = yield self.py(node[1])
            items = []
            for x in node[2]:
                t, n = yield self.py(x)
                items.append(t)
                nest = max(nest, n)
            s = "rt.in_(%s, (%s,))" % (subject, ", ".join(items))
            return ("(not %s)" % s if node[3] else s), nest + 2
        if k == "call":
            name, args = node[1], node[2]
            if name == "iif":                                       # lazy, like IF
                c, n1 = yield self.truth(args[0])
                a, n2 = yield self.py(args[1])
                b, n3 = yield self.py(args[2])
                return "(%s if %s else %s)" % (a, c, b), max(n1, n2, n3) + 1
            parts, nest = [], 0
            for x in args:
                t, n = yield self.py(x)
                parts.append(t)
                nest = max(nest, n)
            return "rt.%s(%s)" % (FUNCS[name][0], ", ".join(parts)), nest + 1
        if k == "if":
            if self.hoist and len(node[1]) > BRANCHES_AT:
                return self.helper((yield self.if_lines(node)))
            text, nest = yield self.py(node[2])
            for cond, val in reversed(node[1]):
                c, n1 = yield self.truth(cond)
                v, n2 = yield self.py(val)
                text, nest = "(%s if %s else %s)" % (v, c, text), max(nest, n1, n2) + 1
            return text, nest
        raise ExprError("unknown node " + k)

    def if_lines(self, node):
        """IF as statements: each test in turn, a return in each branch."""
        lines = []
        for n, (cond, val) in enumerate(node[1]):
            c, _ = yield self.truth(cond)
            v, _ = yield self.py(val)
            lines += ["%s %s:" % ("if" if n == 0 else "elif", c), "    return %s" % v]
        other, _ = yield self.py(node[2])
        return lines + ["return %s" % other]


def _truth(node):
    s = to_python(node)
    return s if is_bool(node) else "rt.truth(%s)" % s


def to_python(node):
    """One Python expression (no helper functions; for short rules and for reading)."""
    return run(_Emitter("x", "r", hoist=False).py(node))[0]


def to_function(node, name, args="r"):
    """Lines of a Python function returning the expression's value, preceded by any helper functions it needs.
    However long or nested the rule, no line nests more than about HOIST_AT brackets."""
    em = _Emitter(name, args)
    if node[0] == "if":
        body = run(em.if_lines(node))
    else:
        body = ["return %s" % run(em.py(node))[0]]
    return em.helpers + ["def %s(%s):" % (name, args)] + ["    " + x for x in body]


# ----------------------------------------------------------------------------- readable form
# For people, not for Python: [Field] = "x", and/or in words, IF as a "when ... -> ..." list,
# and a few patterns said the way a person would say them. Grouping is always made explicit,
# because Alteryx reads "a OR b AND c" left to right.
PROPER = {n.lower(): n for n in (
    "IIF Switch IsNull IsEmpty IsNumber IsString IsInteger ToNumber ToString ToInteger CharToInt CharFromInt ABS CEIL "
    "FLOOR Round POW SQRT EXP LOG LOG10 Mod Min Max Contains StartsWith EndsWith FindString Left Right Substring Length "
    "LowerCase Uppercase TitleCase Trim TrimLeft TrimRight PadLeft PadRight Replace ReplaceChar ReplaceFirst ReverseString "
    "GetWord CountWords StripQuotes REGEX_Replace REGEX_Match REGEX_CountMatches ToDate ToDateTime DateTimeDiff "
    "DateTimeAdd DateTimeFormat DateTimeParse DateTimeTrim DateTimeNow DateTimeToday DateTimeYear DateTimeMonth "
    "DateTimeDay DateTimeHour DateTimeMinutes DateTimeSeconds DateTimeFirstOfMonth DateTimeLastOfMonth FileGetFileName "
    "FileGetExt FileGetDir FileExists").split()}
_RSYM = {"eq": "=", "ne": "≠", "lt": "<", "le": "≤", "gt": ">", "ge": "≥", "add": "+", "sub": "-", "mul": "×",
         "div": "÷", "mod": "mod"}
_NEG = {"eq": "ne", "ne": "eq", "lt": "ge", "ge": "lt", "gt": "le", "le": "gt"}
_LEVEL = {"or": 0, "and": 0, "not": 1, "in": 2, "cmp": 2, "arith_add": 3, "arith_sub": 3, "arith_mul": 4,
          "arith_div": 4, "arith_mod": 4, "neg": 5}


def _lit(node):
    return node[0] in ("str", "num", "bool", "null") or (node[0] == "neg" and node[1][0] == "num")


def _level(node):
    if node[0] == "arith":
        return _LEVEL["arith_" + node[1]]
    if node[0] == "call" and node[1] in ("isempty", "isnull", "contains", "startswith", "endswith"):
        return 2                                  # rendered as "[x] is empty", "[x] contains ..."
    return _LEVEL.get(node[0], 9)


def _wrap(node, above):
    s = yield _readable(node)
    return "(%s)" % s if _level(node) <= above and not s.startswith("(") else s


def _chain(node, kind):
    """Flatten a run of the same and/or into its items."""
    out, todo = [], [node]
    while todo:
        n = todo.pop()
        if n[0] == kind:
            todo.extend(reversed(n[1:]))
        else:
            out.append(n)
    return out


def _chain_parts(node, kind=None, items=None):
    """-> (word, [item texts]) for an and/or chain, with repeated tests on one field grouped:
    [X] = "a" or [X] = "b"  -> [X] is one of "a", "b";  contains-any; is-none-of; between."""
    kind = kind or node[0]
    items = _chain(node, kind) if items is None else items
    keys = {}
    for i, x in enumerate(items):
        if x[0] == "cmp":
            keys[i] = canon(x[2])
        elif x[0] == "call" and x[1] == "contains" and len(x[2]) == 2 and _lit(x[2][1]):
            keys[i] = canon(x[2][0])
    by_key = {}
    for i, x in enumerate(items):
        if i in keys:
            by_key.setdefault((x[0], x[1], keys[i]), []).append(i)
    out, used = [], set()
    for i, it in enumerate(items):
        if i in used:
            continue
        group, word = None, ""
        lit = lambda j: _lit(items[j][3])
        if kind == "or" and it[0] == "cmp" and it[1] == "eq" and _lit(it[3]):
            group, word = [j for j in by_key[("cmp", "eq", keys[i])] if lit(j)], "is one of"
        elif kind == "or" and it[0] == "call" and it[1] == "contains" and i in keys:
            group, word = by_key[("call", "contains", keys[i])], "contains any of"
        elif kind == "and" and it[0] == "cmp" and it[1] == "ne" and _lit(it[3]):
            group, word = [j for j in by_key[("cmp", "ne", keys[i])] if lit(j)], "is none of"
        if group and len(group) > 1:
            used.update(group)
            subject = it[2] if it[0] == "cmp" else it[2][0]
            vals = []
            for j in group:
                vals.append((yield _readable(items[j][3] if it[0] == "cmp" else items[j][2][1])))
            out.append("%s %s %s" % ((yield _wrap(subject, 2)), word, ", ".join(vals)))
            continue
        if kind == "and" and it[0] == "cmp" and it[1] in ("ge", "le"):
            want = "le" if it[1] == "ge" else "ge"
            mate = next((j for j in by_key.get(("cmp", want, keys.get(i)), []) if j > i and j not in used), None)
            if mate is not None:
                used.add(mate)
                lo, hi = (it[3], items[mate][3]) if it[1] == "ge" else (items[mate][3], it[3])
                out.append("%s between %s and %s" % ((yield _wrap(it[2], 2)), (yield _wrap(lo, 2)), (yield _wrap(hi, 2))))
                continue
        other = "and" if kind == "or" else "or"
        text = yield _readable(it)
        out.append("(%s)" % text if it[0] == other else text)
    return kind, out


def chain_parts(node):
    return run(_chain_parts(node))


def _spine_text(node):
    """Readable text of a left-deep chain, built in one pass: "(a or b) and c" without one call per level."""
    base, levels = spine(node)
    first = levels[0]
    if first[0] in ("or", "and"):
        word, parts = yield _chain_parts(first)
        out, opens = [(" %s " % word).join(parts)], 0
    else:
        out, opens = [(yield _readable_level(first))], 0
    below = first
    for lv in levels[1:]:
        if lv[0] in ("or", "and"):
            word, parts = yield _chain_parts(None, lv[0], list(lv[2:]))
            opens += 1
            out.append(") %s %s" % (word, (" %s " % word).join(parts)))
        else:
            lvl = _level(lv)
            if lv[0] == "cmp" or _level(below) <= lvl - 1:
                opens += 1
                out.append(")")
            for x in lv[3:]:
                t = yield _readable(x)
                out.append(" %s %s" % (_RSYM[lv[1]], t if _level(x) > lvl else "(%s)" % t))
        below = lv
    return "(" * opens + "".join(out)


def _readable_level(node):
    """One arithmetic or comparison node whose first item is not of its family."""
    if node[0] == "cmp":
        return "%s %s %s" % ((yield _wrap(node[2], 2)), _RSYM[node[1]], (yield _wrap(node[3], 2)))
    lv = _level(node)
    out = [(yield _wrap(node[2], lv - 1))]
    for x in node[3:]:
        t = yield _readable(x)
        out.append(t if _level(x) > lv else "(%s)" % t)
    return (" %s " % _RSYM[node[1]]).join(out)


def _readable(node):
    k = node[0]
    if k == "num":
        return node[1]
    if k == "str":
        return ('"%s"' if '"' not in node[1] else "'%s'") % node[1]
    if k == "field":
        return "[this column]" if node[1].lower() == "_currentfield_" else "[%s]" % node[1]
    if k == "bool":
        return "True" if node[1] else "False"
    if k == "null":
        return "Null"
    if k in _FAMILY:
        if len(spine(node)[1]) > 1:
            return (yield _spine_text(node))
        if k in ("or", "and"):
            word, parts = yield _chain_parts(node)
            return (" %s " % word).join(parts)
        return (yield _readable_level(node))
    if k == "not":
        x = node[1]
        if x[0] == "call" and x[1] in ("isempty", "isnull"):
            return "%s is not %s" % ((yield _wrap(x[2][0], 2)), "empty" if x[1] == "isempty" else "null")
        if x[0] == "call" and x[1] == "contains" and len(x[2]) == 2:
            return "%s does not contain %s" % ((yield _wrap(x[2][0], 2)), (yield _readable(x[2][1])))
        if x[0] == "cmp" and len(spine(x)[1]) == 1:
            return "%s %s %s" % ((yield _wrap(x[2], 2)), _RSYM[_NEG[x[1]]], (yield _wrap(x[3], 2)))
        if x[0] == "in":
            return (yield _readable(("in", x[1], x[2], not x[3])))
        return "not (%s)" % (yield _readable(x))
    if k == "neg":
        return "-" + (yield _wrap(node[1], 4))
    if k == "in":
        vals = []
        for x in node[2]:
            vals.append((yield _readable(x)))
        return "%s %s %s" % ((yield _wrap(node[1], 2)), "is none of" if node[3] else "is one of", ", ".join(vals))
    if k == "call":
        name, args = node[1], node[2]
        if name in ("isempty", "isnull"):
            return "%s is %s" % ((yield _wrap(args[0], 2)), "empty" if name == "isempty" else "null")
        if name in ("contains", "startswith", "endswith"):
            word = {"contains": "contains", "startswith": "starts with", "endswith": "ends with"}[name]
            exact = len(args) > 2 and args[2] == ("num", "0")
            return "%s %s %s%s" % ((yield _wrap(args[0], 2)), word, (yield _readable(args[1])),
                                   " (matching case)" if exact else "")
        if name == "iif":
            return "(when %s → %s, otherwise %s)" % ((yield _readable(args[0])), (yield _readable(args[1])),
                                                     (yield _readable(args[2])))
        texts = []
        for a in args:
            texts.append((yield _readable(a)))
        return "%s(%s)" % (PROPER.get(name, name), ", ".join(texts))
    if k == "if":
        parts = []
        for c, v in node[1]:
            parts.append("when %s → %s" % ((yield _readable(c)), (yield _readable(v))))
        return "(%s; otherwise %s)" % ("; ".join(parts), (yield _readable(node[2])))
    raise ExprError("unknown node " + k)


def readable(node):
    """One-line readable text of an expression."""
    return run(_readable(node))


SPINE_NOTE = "(read top down: each line combines with everything above it)"


def _cond_lines(node, width):
    """A condition as one line, or as one line per and/or item when it is too long. A left-to-right mix of AND and
    OR is listed in reading order, each line joined to everything above it."""
    if node[0] in ("or", "and") and len(spine(node)[1]) > 1 and len(readable(node)) > width:
        base, levels = spine(node)
        word, parts = chain_parts(levels[0])
        lines = [SPINE_NOTE, parts[0]] + ["%s %s" % (word, p) for p in parts[1:]]
        for lv in levels[1:]:
            word, parts = run(_chain_parts(None, lv[0], list(lv[2:])))
            lines += ["%s %s" % (word, p) for p in parts]
        return lines
    one = readable(node)
    if len(one) <= width or node[0] not in ("and", "or"):
        return [one]
    word, parts = chain_parts(node)
    return [parts[0]] + ["%s %s" % (word, p) for p in parts[1:]]


def _fit(lines, width):
    """Wrap lines longer than the page (a list of 5,000 values) under their own indentation."""
    out = []
    for line in lines:
        if len(line) <= width + 20:
            out.append(line)
            continue
        lead = len(line) - len(line.lstrip())
        out += textwrap.wrap(line, width, subsequent_indent=" " * (lead + 4), break_long_words=False,
                             break_on_hyphens=False) or [line]
    return out


def decision(node, width=100):
    """Lines that state an expression's value. A top-level IF becomes a list:
        when [A] = "x" → 1
        otherwise      → 2"""
    if node[0] != "if":
        return _fit(_cond_lines(node, width) if node[0] in ("and", "or") else [readable(node)], width)
    if len(node[1]) == 1:                        # one test and a default: one line when it fits
        one = "%s when %s, otherwise %s" % (readable(node[1][0][1]), readable(node[1][0][0]), readable(node[2]))
        if len(one) <= width - 20:
            return [one]
    rows = [("when " + readable(cond), readable(v), cond) for cond, v in node[1]]
    rows.append(("otherwise", readable(node[2]), None))
    flat = all(len(h) + len(v) + 3 <= width for h, v, _ in rows)
    if flat:
        pad = max(len(h) for h, _, _ in rows)
        return ["%s → %s" % (h.ljust(pad), v) for h, v, _ in rows]
    out = []
    for head, value, cond in rows:
        if cond is not None and len(head) + len(value) + 3 > width:
            lines = _cond_lines(cond, width - 5)
            out.append("when " + lines[0])
            out += ["     " + x for x in lines[1:]]
            out.append("     → " + value)
        else:
            out.append("%s → %s" % (head, value))
    return _fit(out, width)
