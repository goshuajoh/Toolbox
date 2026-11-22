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
                node = ("or", node, self.and_())
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
            node = (kind, node, self.eq())

    def and_(self):
        node = self.eq()
        while self.kw("AND") or self.op("&&"):
            self.take()
            node = ("and", node, self.eq())
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
            node = ("arith", ARITH[o], node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.op("*", "/", "%"):
            o = self.take()[1]
            node = ("arith", ARITH[o], node, self.unary())
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


def _strip(node):
    """Remove the ("paren", x) markers left by the parser."""
    if node[0] == "paren":
        return _strip(node[1])
    return tuple(_strip(x) if isinstance(x, tuple) and x and isinstance(x[0], str) and x[0] in _KINDS
                 else tuple(_strip_any(y) for y in x) if isinstance(x, tuple) else x for x in node)


def _strip_any(x):
    if isinstance(x, tuple) and x and isinstance(x[0], str) and x[0] in _KINDS:
        return _strip(x)
    if isinstance(x, tuple):
        return tuple(_strip_any(y) for y in x)
    return x


_KINDS = {"num", "str", "field", "bool", "null", "or", "and", "not", "neg", "cmp", "arith", "in", "call", "if",
          "paren"}


def _raw(text):
    if text is None or not text.strip():
        raise ExprError("empty expression")
    return _Parser(text).parse()


def parse(text):
    return _strip(_raw(text))


def precedence_notes(text):
    """Places where the documented Alteryx precedence gives a different reading from the
    conventional one. Each note names the construct; the rule should be checked in Designer."""
    notes = []

    def visit(n):
        if not (isinstance(n, tuple) and n and isinstance(n[0], str) and n[0] in _KINDS):
            for x in n if isinstance(n, tuple) else ():
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
    yield node
    for c in _children(node):
        yield from walk(c)


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
        return "(%s %s %s)" % (canon(node[1]), k.upper(), canon(node[2]))
    if k == "not":
        return "!%s" % canon(node[1])
    if k == "neg":
        return "-%s" % canon(node[1])
    if k in ("cmp", "arith"):
        return "(%s %s %s)" % (canon(node[2]), _SYM[node[1]], canon(node[3]))
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


def _truth(node):
    s = to_python(node)
    return s if is_bool(node) else "rt.truth(%s)" % s


def to_python(node):
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
    if k == "or":
        return "(%s or %s)" % (_truth(node[1]), _truth(node[2]))
    if k == "and":
        return "(%s and %s)" % (_truth(node[1]), _truth(node[2]))
    if k == "not":
        return "(not %s)" % _truth(node[1])
    if k == "neg":
        return "rt.neg(%s)" % to_python(node[1])
    if k in ("cmp", "arith"):
        return "rt.%s(%s, %s)" % (node[1], to_python(node[2]), to_python(node[3]))
    if k == "in":
        s = "rt.in_(%s, (%s,))" % (to_python(node[1]), ", ".join(map(to_python, node[2])))
        return "(not %s)" % s if node[3] else s
    if k == "call":
        name, args = node[1], node[2]
        if name == "iif":                                  # lazy, like IF
            return "(%s if %s else %s)" % (to_python(args[1]), _truth(args[0]), to_python(args[2]))
        return "rt.%s(%s)" % (FUNCS[name][0], ", ".join(map(to_python, args)))
    if k == "if":
        s = to_python(node[2])
        for cond, val in reversed(node[1]):
            s = "(%s if %s else %s)" % (to_python(val), _truth(cond), s)
        return s
    raise ExprError("unknown node " + k)


def to_function(node, name, args="r"):
    """Lines of a Python function returning the expression's value."""
    lines = ["def %s(%s):" % (name, args)]
    if node[0] == "if":
        for n, (cond, val) in enumerate(node[1]):
            lines.append("    %s %s:" % ("if" if n == 0 else "elif", _truth(cond)))
            lines.append("        return %s" % to_python(val))
        lines.append("    return %s" % to_python(node[2]))
    else:
        lines.append("    return %s" % to_python(node))
    return lines
