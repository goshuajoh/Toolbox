"""One-line pandas forms of Alteryx expressions and tools, for a person migrating by hand.

These are sketches, not the converter's output: they show the pandas idiom for each step. Where
Alteryx differs from plain pandas they use two small helpers, explained in the guide's legend:
    eq(a, b)    Alteryx "=": text compared ignoring case; null = null is true
    empty(a)    Alteryx IsEmpty: null or ""
When an expression has no honest one-line form, the sketch names the exact function in the
generated module instead. Standard library only.
"""
from . import expr


class NoSketch(Exception):
    pass


def _lit(n):
    return n[0] in ("str", "num", "bool", "null") or (n[0] == "neg" and n[1][0] == "num")


def _regex_repl(r):
    out, i = [], 0
    while i < len(r):
        if r[i] == "$" and i + 1 < len(r) and r[i + 1].isdigit():
            out.append("\\" + r[i + 1])
            i += 2
        else:
            out.append(r[i])
            i += 1
    return "".join(out)


LONG = 8        # an and/or or arithmetic chain longer than this becomes one call over a list: a flat line


def _bool_chain(n, col, st):
    """a | b | c ... ; [X] = "a" OR [X] = "b" OR ... as one isin (Alteryx: text ignoring case, null matches nothing);
    [X] != "a" AND [X] != "b" ... as ~isin."""
    k, items = n[0], list(n[1:])
    want = "eq" if k == "or" else "ne"
    groups, rest = {}, []
    for x in items:
        if x[0] == "cmp" and x[1] == want and _lit(x[3]) and x[3][0] in ("str", "num") and not _lit(x[2]):
            groups.setdefault((expr.canon(x[2]), x[3][0]), []).append(x)
        else:
            rest.append(x)
    parts = []
    for (_, kind), xs in groups.items():
        if len(xs) < 3:
            rest += xs
            continue
        subject = (yield st.vec(xs[0][2], col))
        vals = list(dict.fromkeys(x[3][1].casefold() if kind == "str" else x[3][1] for x in xs))
        test = ("%s.str.casefold().isin([%s])" % (subject, ", ".join(repr(v) for v in vals)) if kind == "str" else
                "%s.isin([%s])" % (subject, ", ".join(vals)))
        parts.append(test if k == "or" else "~" + test)
    V = lambda x, c=col: st.vec(x, c)
    keep = {id(x) for x in rest}
    for x in items:
        if id(x) in keep:
            parts.append((yield V(x, col)))
    if len(parts) == 1:
        return parts[0]
    if len(parts) > LONG:
        return "functools.reduce(operator.%s, [%s])" % ("or_" if k == "or" else "and_", ", ".join(parts))
    return "(%s)" % (" %s " % ("&" if k == "and" else "|")).join(parts)


def _vec(n, col, st):
    """Generator (driven by expr.run): vectorised pandas text of an expression over a DataFrame named df."""
    V = lambda x, c=col: st.vec(x, c)
    S = lambda x, c=col: st.series(x, c)
    k = n[0]
    if k in ("and", "or", "arith") and len(expr.spine(n)[1]) > SPINE_AT:
        return (yield st.spine(n, col))
    if k == "num":
        return n[1]
    if k == "str":
        return repr(n[1])
    if k == "bool":
        return "True" if n[1] else "False"
    if k == "null":
        return "None"
    if k == "field":
        if n[1].lower() == "_currentfield_":
            if not col:
                raise NoSketch("current field outside Multi-Field Formula")
            return col
        if n[1].lower().startswith(("engine.", "user.", "question.")):
            raise NoSketch("workflow constant")
        return 'df["%s"]' % n[1]
    if k in ("and", "or"):
        return (yield _bool_chain(n, col, st))
    if k == "not":
        return "~(%s)" % (yield V(n[1], col))
    if k == "neg":
        return "-" + (yield V(n[1], col))
    if k == "cmp":
        a, b = (yield V(n[2], col)), (yield V(n[3], col))
        if n[1] in ("eq", "ne"):
            if n[3][0] == "num" or n[2][0] == "num":
                return "(%s %s %s)" % (a, "==" if n[1] == "eq" else "!=", b)
            return ("eq(%s, %s)" if n[1] == "eq" else "~eq(%s, %s)") % (a, b)
        return "(%s %s %s)" % (a, {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}[n[1]], b)
    if k == "arith":
        op = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}[n[1]]
        items = []
        for x in n[2:]:
            items.append((yield V(x, col)))
        if len(items) > LONG:
            return "functools.reduce(operator.%s, [%s])" % ({"add": "add", "sub": "sub", "mul": "mul", "div": "truediv",
                                                              "mod": "mod"}[n[1]], ", ".join(items))
        return "(%s)" % (" %s " % op).join(items)
    if k == "in":
        if not all(_lit(x) for x in n[2]):
            raise NoSketch("IN with non-literal values")
        vals = []
        for x in n[2]:
            vals.append((yield V(x)))
        s = "%s.isin([%s])" % ((yield S(n[1], col)), ", ".join(vals))
        return "~" + s if n[3] else s
    if k == "if":
        conds, vals = [], []
        for c, v in n[1]:
            conds.append((yield V(c, col)))
            vals.append((yield V(v, col)))
        conds, vals = ", ".join(conds), ", ".join(vals)
        return "np.select([%s], [%s], default=%s)" % (conds, vals, (yield V(n[2], col)))
    if k == "call":
        name, a = n[1], n[2]
        if name == "iif":
            return "np.where(%s, %s, %s)" % ((yield V(a[0], col)), (yield V(a[1], col)), (yield V(a[2], col)))
        if name == "isempty":
            return "empty(%s)" % (yield V(a[0], col))
        if name == "isnull":
            return "%s.isna()" % (yield S(a[0], col))
        if name in ("contains", "startswith", "endswith"):
            if not _lit(a[1]):
                raise NoSketch("search text taken from a column")
            exact = len(a) > 2 and a[2] == ("num", "0")
            if name == "contains":
                return "%s.str.contains(%s, case=%s, regex=False, na=False)" % ((yield S(a[0], col)), (yield V(a[1])), exact)
            method = name.replace("with", "swith")
            s = (yield S(a[0], col))
            return "%s.str.%s(%s, na=False)" % (s if exact else s + ".str.lower()", method,
                                               (yield V(a[1])) if exact else repr(str(a[1][1]).lower()))
        if name == "left" and a[1][0] == "num":
            return "%s.str[:%s]" % ((yield S(a[0], col)), a[1][1])
        if name == "right" and a[1][0] == "num":
            return "%s.str[-%s:]" % ((yield S(a[0], col)), a[1][1])
        if name == "substring" and all(x[0] == "num" for x in a[1:]):
            start = int(a[1][1])
            return "%s.str[%d:%s]" % ((yield S(a[0], col)), start, start + int(a[2][1]) if len(a) > 2 else "")
        if name in ("length", "uppercase", "lowercase", "trim", "trimleft", "trimright", "titlecase") and len(a) == 1:
            method = {"length": "len", "uppercase": "upper", "lowercase": "lower", "trim": "strip", "trimleft": "lstrip",
                      "trimright": "rstrip", "titlecase": "title"}[name]
            return "%s.str.%s()" % ((yield S(a[0], col)), method)
        if name == "replace" and _lit(a[1]) and _lit(a[2]):
            return "%s.str.replace(%s, %s, regex=False)" % ((yield S(a[0], col)), (yield V(a[1])), (yield V(a[2])))
        if name.startswith("regex_") and len(a) > 1 and a[1][0] == "str" and "\\G" in a[1][1]:
            raise NoSketch("\\G in a regular expression has no Python equivalent")
        if name == "regex_replace" and a[1][0] == "str" and a[2][0] == "str":
            case = len(a) > 3 and a[3] == ("num", "0")
            return "%s.str.replace(%r, %r, regex=True, case=%s)" % ((yield S(a[0], col)), a[1][1], _regex_repl(a[2][1]), case)
        if name == "regex_match" and a[1][0] == "str":
            return "%s.str.fullmatch(%r, case=%s)" % ((yield S(a[0], col)), a[1][1], len(a) > 2 and a[2] == ("num", "0"))
        if name == "padleft" and a[1][0] == "num" and a[2][0] == "str":
            return "%s.str.pad(%s, side=\"left\", fillchar=%r)" % ((yield S(a[0], col)), a[1][1], a[2][1][:1])
        if name == "tonumber" and len(a) == 1:
            return "pd.to_numeric(%s, errors=\"coerce\").fillna(0)" % (yield V(a[0], col))
        if name == "tostring" and len(a) == 1:
            return "%s.astype(str)" % (yield S(a[0], col))
        if name == "tostring" and len(a) == 2 and a[1][0] == "num":
            return '%s.map(lambda v: f"{v:.%sf}")' % ((yield S(a[0], col)), a[1][1])
        if name in ("datetimetoday", "datetimenow") and not a:
            return "pd.Timestamp.today().normalize()" if name == "datetimetoday" else "pd.Timestamp.now()"
        if name in ("todate", "todatetime"):
            inner = a[0]
            if inner[0] in ("arith", "num", "call") and not (inner[0] == "call" and inner[1] in ("tostring", "left", "right")):
                s = "pd.to_datetime(%s, unit=\"D\", origin=\"1899-12-30\")" % (yield V(inner, col))
            else:
                s = "pd.to_datetime(%s, errors=\"coerce\")" % (yield V(inner, col))
            return s + (".dt.normalize()" if name == "todate" else "")
        if name == "datetimediff" and a[2][0] == "str" and a[2][1].lower().rstrip("s") == "day":
            return "(pd.to_datetime(%s, errors=\"coerce\") - pd.to_datetime(%s, errors=\"coerce\")).dt.days" % ((yield V(a[0], col)), (yield V(a[1], col)))
        if name == "abs":
            return "%s.abs()" % (yield V(a[0], col)) if not _lit(a[0]) else "abs(%s)" % (yield V(a[0]))
        if name == "round" and a[1][0] == "num":
            m = a[1][1]
            return "%s.round()" % (yield V(a[0], col)) if m in ("1", "1.0") else "((%s / %s).round() * %s)" % ((yield V(a[0], col)), m, m)
        raise NoSketch("no one-line pandas form for %s()" % expr.PROPER.get(name, name))
    raise NoSketch(k)


SPINE_AT = 8        # an alternating chain with more levels than this becomes one line per term
HOIST_AT = 50       # nesting beyond which a part becomes a variable of its own (t1 = ...)


def _nest(text):
    """How deeply a line nests: brackets, and method calls chained one after another."""
    if len(text) < 150:
        return 0
    return expr._nesting(text) + text.count(").")


class _State:
    """The extra lines a long or deep rule needs before its final pandas expression."""

    def __init__(self):
        self.lines, self.count = [], 0

    def temp(self, text):
        self.count += 1
        name = "t%d" % self.count
        self.lines.append("%s = %s" % (name, text))
        return name

    def vec(self, n, col):
        text = yield _vec(n, col, self)
        return self.temp(text) if _nest(text) > HOIST_AT else text

    def series(self, n, col):
        if _lit(n):
            raise NoSketch("text function on a literal")
        return (yield self.vec(n, col))

    def spine(self, n, col):
        """t = a / t = t | b / t = t & c ...: Alteryx's left-to-right reading, one line per term."""
        base, levels = expr.spine(n)
        first = yield self.vec(base, col)
        self.count += 1
        name = "t%d" % self.count
        self.lines.append("%s = %s" % (name, first))
        ops = {"or": "|", "and": "&", "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}
        for lv in levels:
            op = ops[lv[0] if lv[0] in ("or", "and") else lv[1]]
            for x in expr.items_of(lv)[1:]:
                self.lines.append("%s = %s %s %s" % (name, name, op, (yield self.vec(x, col))))
        return name


def vec_lines(n, col=None):
    """-> (lines, text): pandas lines to run first (none for most rules), then the expression."""
    st = _State()
    text = expr.run(st.vec(n, col))
    return st.lines, text


def vec(n, col=None):
    """Vectorised pandas text of an expression over a DataFrame named df, as one expression."""
    lines, text = vec_lines(n, col)
    if lines:
        raise NoSketch("needs %d lines before it: use vec_lines" % len(lines))
    return text
