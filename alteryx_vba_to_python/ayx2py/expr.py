"""Alteryx expression language -> Python source.

A real tokenizer and recursive-descent parser; no regex substitution on the expression text.
Standard library only.

    ast = parse("IF [A]='x' THEN 1 ELSE 2 ENDIF")
    canon(ast)                 canonical text (used to recognise identical rules)
    fields(ast)                field names the expression reads
    to_python(ast)             a Python expression using `r[...]` for fields and `rt.` helpers
    to_function(ast, "f")      lines of a `def f(r):` (top-level IF becomes if/elif/else)

Anything the translator does not know raises ExprError; the caller turns the tool into a stub.
"""
import re
import sys

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


def _join(kind, node, item):
    """a OR b OR c is one ("or", a, b, c) node, so long chains do not nest once per term."""
    if node[0] == kind:
        node.append(item) if isinstance(node, list) else None
        return node if isinstance(node, list) else [kind, *node[1:], item]
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
        node = self.or_()
        if self.peek()[0] != "END":
            self.fail("unexpected text")
        return node

    def or_(self):
        if AND_BINDS_TIGHTER:
            node = self.and_()
            while self.kw("OR") or self.op("||"):
                self.take()
                node = _join("or", node, self.and_())
            return node
        node = self.eq()
        while True:
            if self.kw("OR") or self.op("||"):
                kind = "or"
            elif self.kw("AND") or self.op("&&"):
                kind = "and"
            else:
                return node
            self.take()
            node = _join(kind, node, self.eq())

    def and_(self):
        node = self.eq()
        while self.kw("AND") or self.op("&&"):
            self.take()
            node = _join("and", node, self.eq())
        return node

    def eq(self):
        node = self.rel()
        while self.op("=", "==", "!=", "<>"):
            o = self.take()[1]
            node = ("cmp", CMP[o], node, self.rel())
        return node

    def _not_in(self):
        return self.kw("NOT") and self.toks[self.i + 1][0] == "ID" and self.toks[self.i + 1][1].upper() == "IN"

    def rel(self):
        if (self.kw("NOT") and not self._not_in()) or self.op("!"):
            self.take()
            return ("not", self.rel())
        node = self.add()
        while True:
            if self.op("<", "<=", ">", ">="):
                o = self.take()[1]
                node = ("cmp", CMP[o], node, self.add())
            elif self.kw("IN") or self._not_in():
                neg = self.kw("NOT")
                self.i += 2 if neg else 1
                self.need_op("(")
                items = [self.or_()]
                while self.op(","):
                    self.take()
                    items.append(self.or_())
                self.need_op(")")
                node = ("in", node, tuple(items), neg)
            else:
                return node

    def add(self):
        node = self.mul()
        while self.op("+", "-"):
            o = self.take()[1]
            node = _join_arith(ARITH[o], node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.op("*", "/", "%"):
            o = self.take()[1]
            node = _join_arith(ARITH[o], node, self.unary())
        return node

    def unary(self):
        if self.op("-"):
            self.take()
            return ("neg", self.unary())
        if self.op("+"):
            self.take()
            return self.unary()
        if self.op("!"):                       # "-!x" / "a + !b": rare, but legal
            self.take()
            return ("not", self.unary())
        return self.primary()

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
            node = self.or_()
            self.need_op(")")
            return ("paren", node)
        if k == "ID":
            up = v.upper()
            if up == "IF":
                return self.if_()
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
                args.append(self.or_())
                while self.op(","):
                    self.take()
                    args.append(self.or_())
            self.need_op(")")
            name = v.lower()
            if name not in FUNCS:
                raise ExprError("function %s() is not supported by the translator" % v)
            lo, hi = FUNCS[name][1], FUNCS[name][2]
            if not lo <= len(args) <= hi:
                raise ExprError("%s() takes %d to %d arguments, got %d" % (v, lo, hi, len(args)))
            return ("call", name, tuple(args))
        self.fail("unexpected token")

    def if_(self):
        self.need_kw("IF")
        branches = []
        cond = self.or_()
        self.need_kw("THEN")
        branches.append((cond, self.or_()))
        while self.kw("ELSEIF"):
            self.take()
            cond = self.or_()
            self.need_kw("THEN")
            branches.append((cond, self.or_()))
        self.need_kw("ELSE")
        other = self.or_()
        self.need_kw("ENDIF")
        return ("if", tuple(branches), other)


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
            x = _strip(x)
            items += list(x[1:]) if x[0] == k else [x]
        return (k, *items)
    if k == "arith":
        items = [_strip(x) for x in node[2:]]
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
            branches += [(_strip(c), _strip(v)) for c, v in other[1]]
            other = other[2]
        return ("if", tuple(branches), _strip(other))
    return tuple([_strip(x) if _is_node(x) else _strip_any(x) for x in node])


def _strip_any(x):
    if _is_node(x):
        return _strip(x)
    if isinstance(x, (tuple, list)):
        return tuple([_strip_any(y) for y in x])
    return x


_KINDS = {"num", "str", "field", "bool", "null", "or", "and", "not", "neg", "cmp", "arith", "in", "call", "if",
          "paren"}


def _raw(text):
    if text is None or not text.strip():
        raise ExprError("empty expression")
    return _Parser(text).parse()


DEEP_LIMIT = 20000          # Python frames allowed while reading a deeply nested rule (see __main__ for the stack)


def parse(text):
    if sys.getrecursionlimit() < DEEP_LIMIT:
        sys.setrecursionlimit(DEEP_LIMIT)
    return _strip(_raw(text))


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


def depth(node):
    """How deeply the rule nests (an and/or or arithmetic chain counts once, however long)."""
    best, todo = 0, [(node, 1)]
    while todo:
        n, d = todo.pop()
        best = max(best, d)
        todo.extend((c, d + 1) for c in _children(n))
    return best


def precedence_notes(text):
    """Places where the documented Alteryx precedence gives a different reading from the
    conventional one. Each note names the construct; the rule should be checked in Designer."""
    notes = []

    def visit(n):
        if not _is_node(n):
            for x in n if isinstance(n, (tuple, list)) else ():
                visit(x)
            return
        if n[0] in ("and", "or") and n[1][0] in ("and", "or") and n[1][0] != n[0]:
            notes.append("AND and OR are mixed without parentheses: read left to right, "
                         "so 'a OR b AND c' means '(a OR b) AND c'")
        if n[0] == "cmp" and n[1] in ("eq", "ne") and "not" in (n[2][0], n[3][0]):
            notes.append("NOT/! is applied before = : '!a = b' means '(!a) = b'")
        for x in n[1:]:
            visit(x)

    try:
        visit(_raw(text))
    except ExprError:
        pass
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
    """Every node, parents before children, without recursion (expressions can be thousands of terms long)."""
    todo = [node]
    while todo:
        n = todo.pop()
        yield n
        todo.extend(reversed(_children(n)))


def fields(node):
    """Field names read by the expression, in first-use order (engine constants included)."""
    return list(dict.fromkeys(n[1] for n in walk(node) if n[0] == "field"))


def functions(node):
    return sorted({n[1] for n in walk(node) if n[0] == "call"})


def is_bool(node):
    k = node[0]
    return k in ("or", "and", "not", "cmp", "in", "bool") or (k == "call" and FUNCS[node[1]][3])


_SYM = {"eq": "=", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=",
        "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}


def canon(node):
    """Canonical one-line text: same rule -> same text whatever the spacing or keyword case."""
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
    if k in ("or", "and"):
        return "(%s)" % (" %s " % k.upper()).join(canon(x) for x in node[1:])
    if k == "not":
        return "!%s" % canon(node[1])
    if k == "neg":
        return "-%s" % canon(node[1])
    if k in ("cmp", "arith"):
        return "(%s)" % (" %s " % _SYM[node[1]]).join(canon(x) for x in node[2:])
    if k == "in":
        return "(%s %sIN (%s))" % (canon(node[1]), "NOT " if node[3] else "", ", ".join(map(canon, node[2])))
    if k == "call":
        return "%s(%s)" % (node[1], ", ".join(map(canon, node[2])))
    if k == "if":
        parts = ["IF %s THEN %s" % (canon(node[1][0][0]), canon(node[1][0][1]))]
        parts += ["ELSEIF %s THEN %s" % (canon(c), canon(v)) for c, v in node[1][1:]]
        return " ".join(parts) + " ELSE %s ENDIF" % canon(node[2])
    raise ExprError("unknown node " + k)


# ----------------------------------------------------------------------------- emitter
CURRENT = {"_currentfield_": "cur", "_currentfieldname_": "cur_name", "_currentfieldtype_": "cur_type"}


def _truth(node, hoist=None):
    s = to_python(node, hoist)
    return s if is_bool(node) else "rt.truth(%s)" % s


HOIST_AT = 40               # nesting (in brackets) beyond which a part of a rule becomes a variable of its own


def _nesting(text):
    best = level = 0
    for ch in text:
        if ch in "([{":
            level += 1
            best = max(best, level)
        elif ch in ")]}":
            level -= 1
    return best


def to_python(node, hoist=None):
    """Python text of an expression. hoist: a list to receive "name = value" lines for parts nested too deeply
    to stay inline (Python refuses more than 200 nested brackets); only parts that are always evaluated move."""
    text = _to_python(node, hoist)
    if hoist is not None and node[0] in ("call", "cmp", "arith", "neg", "in") and _nesting(text) > HOIST_AT:
        name = "_v%d" % (len(hoist) + 1)
        hoist.append("%s = %s" % (name, text))
        return name
    return text


def _to_python(node, hoist=None):
    k = node[0]
    if k == "num":
        v = node[1]
        return str(int(v)) if re.fullmatch(r"\d+", v) else repr(float(v))
    if k == "str":
        return repr(node[1])
    if k == "bool":
        return "True" if node[1] else "False"
    if k == "null":
        return "None"
    if k == "field":
        name = node[1]
        if name.lower() in CURRENT:
            return CURRENT[name.lower()]
        if re.match(r"(?i)(engine|user|question)\.", name):
            return "rt.constant(%r)" % name
        return "r[%r]" % name
    if k in ("or", "and"):                                 # Python's or/and is one flat node however long
        return "(%s)" % (" %s " % k).join(_truth(x, hoist if n == 0 else None) for n, x in enumerate(node[1:]))
    if k == "not":
        return "(not %s)" % _truth(node[1], hoist)
    if k == "neg":
        return "rt.neg(%s)" % to_python(node[1], hoist)
    if k == "arith" and len(node) > 4:                     # a + b + c ...: one call, not one per term
        return "rt.chain(%r, %s)" % (node[1], ", ".join(to_python(x, hoist) for x in node[2:]))
    if k in ("cmp", "arith"):
        return "rt.%s(%s, %s)" % (node[1], to_python(node[2], hoist), to_python(node[3], hoist))
    if k == "in":
        s = "rt.in_(%s, (%s,))" % (to_python(node[1], hoist), ", ".join(to_python(x, hoist) for x in node[2]))
        return "(not %s)" % s if node[3] else s
    if k == "call":
        name, args = node[1], node[2]
        if name == "iif":                                  # lazy, like IF
            return "(%s if %s else %s)" % (to_python(args[1]), _truth(args[0], hoist), to_python(args[2]))
        return "rt.%s(%s)" % (FUNCS[name][0], ", ".join(to_python(x, hoist) for x in args))
    if k == "if":
        s = to_python(node[2])
        for cond, val in reversed(node[1]):
            s = "(%s if %s else %s)" % (to_python(val), _truth(cond), s)
        return s
    raise ExprError("unknown node " + k)


def to_function(node, name, args="r"):
    """Lines of a Python function returning the expression's value."""
    lines = ["def %s(%s):" % (name, args)]

    def put(indent, fn, n):
        hoist = []
        text = fn(n, hoist)
        lines.extend(indent + h for h in hoist)
        return text

    if node[0] == "if":
        flat = all(_nesting(_truth(c)) <= HOIST_AT and _nesting(to_python(v)) <= HOIST_AT for c, v in node[1])
        for n, (cond, val) in enumerate(node[1]):
            if flat:
                lines.append("    %s %s:" % ("if" if n == 0 else "elif", _truth(cond)))
                lines.append("        return %s" % to_python(val))
            else:                       # each test after the variables it needs; a return ends each branch
                test = put("    ", _truth, cond)
                lines.append("    if %s:" % test)
                lines.append("        return %s" % put("        ", to_python, val))
        lines.append("    return %s" % put("    ", to_python, node[2]))
    else:
        lines.append("    return %s" % put("    ", to_python, node))
    return lines


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
    s = readable(node)
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


def chain_parts(node):
    """-> (word, [item texts]) for an and/or chain, with repeated tests on one field grouped:
    [X] = "a" or [X] = "b"  -> [X] is one of "a", "b";  contains-any; is-none-of; between."""
    kind = node[0]
    items = _chain(node, kind)
    out, used = [], set()
    for i, it in enumerate(items):
        if i in used:
            continue
        group = None
        if kind == "or" and it[0] == "cmp" and it[1] == "eq" and _lit(it[3]):
            group = [j for j, x in enumerate(items) if x[0] == "cmp" and x[1] == "eq" and _lit(x[3])
                     and canon(x[2]) == canon(it[2])]
            word = "is one of"
        elif kind == "or" and it[0] == "call" and it[1] == "contains" and len(it[2]) == 2 and _lit(it[2][1]):
            group = [j for j, x in enumerate(items) if x[0] == "call" and x[1] == "contains" and len(x[2]) == 2
                     and _lit(x[2][1]) and canon(x[2][0]) == canon(it[2][0])]
            word = "contains any of"
        elif kind == "and" and it[0] == "cmp" and it[1] == "ne" and _lit(it[3]):
            group = [j for j, x in enumerate(items) if x[0] == "cmp" and x[1] == "ne" and _lit(x[3])
                     and canon(x[2]) == canon(it[2])]
            word = "is none of"
        if group and len(group) > 1:
            used.update(group)
            subject = it[2] if it[0] == "cmp" else it[2][0]
            vals = [items[j][3] if it[0] == "cmp" else items[j][2][1] for j in group]
            out.append("%s %s %s" % (_wrap(subject, 2), word, ", ".join(readable(v) for v in vals)))
            continue
        if kind == "and" and it[0] == "cmp" and it[1] in ("ge", "le"):
            want = "le" if it[1] == "ge" else "ge"
            mate = next((j for j, x in enumerate(items) if j > i and j not in used and x[0] == "cmp" and x[1] == want
                         and canon(x[2]) == canon(it[2])), None)
            if mate is not None:
                used.add(mate)
                lo, hi = (it[3], items[mate][3]) if it[1] == "ge" else (items[mate][3], it[3])
                out.append("%s between %s and %s" % (_wrap(it[2], 2), _wrap(lo, 2), _wrap(hi, 2)))
                continue
        other = "and" if kind == "or" else "or"
        out.append("(%s)" % readable(it) if it[0] == other else readable(it))
    return kind, out


def readable(node):
    """One-line readable text of an expression."""
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
    if k in ("or", "and"):
        word, parts = chain_parts(node)
        return (" %s " % word).join(parts)
    if k == "not":
        x = node[1]
        if x[0] == "call" and x[1] in ("isempty", "isnull"):
            return "%s is not %s" % (_wrap(x[2][0], 2), "empty" if x[1] == "isempty" else "null")
        if x[0] == "call" and x[1] == "contains" and len(x[2]) == 2:
            return "%s does not contain %s" % (_wrap(x[2][0], 2), readable(x[2][1]))
        if x[0] == "cmp":
            return "%s %s %s" % (_wrap(x[2], 2), _RSYM[_NEG[x[1]]], _wrap(x[3], 2))
        if x[0] == "in":
            return readable(("in", x[1], x[2], not x[3]))
        return "not (%s)" % readable(x)
    if k == "neg":
        return "-" + _wrap(node[1], 4)
    if k == "cmp":
        return "%s %s %s" % (_wrap(node[2], 2), _RSYM[node[1]], _wrap(node[3], 2))
    if k == "arith":
        lv = _level(node)
        rest = [readable(x) if _level(x) > lv else "(%s)" % readable(x) for x in node[3:]]
        return (" %s " % _RSYM[node[1]]).join([_wrap(node[2], lv - 1)] + rest)
    if k == "in":
        return "%s %s %s" % (_wrap(node[1], 2), "is none of" if node[3] else "is one of",
                             ", ".join(readable(x) for x in node[2]))
    if k == "call":
        name, args = node[1], node[2]
        if name in ("isempty", "isnull"):
            return "%s is %s" % (_wrap(args[0], 2), "empty" if name == "isempty" else "null")
        if name in ("contains", "startswith", "endswith"):
            word = {"contains": "contains", "startswith": "starts with", "endswith": "ends with"}[name]
            exact = len(args) > 2 and args[2] == ("num", "0")
            return "%s %s %s%s" % (_wrap(args[0], 2), word, readable(args[1]), " (matching case)" if exact else "")
        if name == "iif":
            return "(when %s → %s, otherwise %s)" % (readable(args[0]), readable(args[1]), readable(args[2]))
        return "%s(%s)" % (PROPER.get(name, name), ", ".join(readable(a) for a in args))
    if k == "if":
        parts = ["when %s → %s" % (readable(c), readable(v)) for c, v in node[1]]
        return "(%s; otherwise %s)" % ("; ".join(parts), readable(node[2]))
    raise ExprError("unknown node " + k)


def _cond_lines(node, width):
    """A condition as one line, or as one line per and/or item when it is too long."""
    one = readable(node)
    if len(one) <= width or node[0] not in ("and", "or"):
        return [one]
    word, parts = chain_parts(node)
    return [parts[0]] + ["%s %s" % (word, p) for p in parts[1:]]


def decision(node, width=100):
    """Lines that state an expression's value. A top-level IF becomes a list:
        when [A] = "x" → 1
        otherwise      → 2"""
    if node[0] != "if":
        return _cond_lines(node, width) if node[0] in ("and", "or") else [readable(node)]
    if len(node[1]) == 1:                        # one test and a default: one line when it fits
        one = "%s when %s, otherwise %s" % (readable(node[1][0][1]), readable(node[1][0][0]), readable(node[2]))
        if len(one) <= width - 20:
            return [one]
    rows = [("when " + c, readable(v), _cond_lines(cond, width - 5)) for cond, v in node[1]
            for c in [readable(cond)]]
    rows.append(("otherwise", readable(node[2]), None))
    flat = all(len(h) + len(v) + 3 <= width for h, v, _ in rows)
    if flat:
        pad = max(len(h) for h, _, _ in rows)
        return ["%s → %s" % (h.ljust(pad), v) for h, v, _ in rows]
    out = []
    for head, value, lines in rows:
        if lines and len(head) + len(value) + 3 > width:
            out.append("when " + lines[0])
            out += ["     " + x for x in lines[1:]]
            out.append("     → " + value)
        else:
            out.append("%s → %s" % (head, value))
    return out
