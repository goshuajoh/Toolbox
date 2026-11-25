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


def _series(n, col):
    """The node must be a column-valued expression to call .str on it."""
    if _lit(n):
        raise NoSketch("text function on a literal")
    return vec(n, col)


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


def vec(n, col=None):
    """Vectorised pandas text of an expression over a DataFrame named df."""
    k = n[0]
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
        return "(%s %s %s)" % (vec(n[1], col), "&" if k == "and" else "|", vec(n[2], col))
    if k == "not":
        return "~(%s)" % vec(n[1], col)
    if k == "neg":
        return "-" + vec(n[1], col)
    if k == "cmp":
        a, b = vec(n[2], col), vec(n[3], col)
        if n[1] in ("eq", "ne"):
            if n[3][0] == "num" or n[2][0] == "num":
                return "(%s %s %s)" % (a, "==" if n[1] == "eq" else "!=", b)
            return ("eq(%s, %s)" if n[1] == "eq" else "~eq(%s, %s)") % (a, b)
        return "(%s %s %s)" % (a, {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}[n[1]], b)
    if k == "arith":
        op = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}[n[1]]
        return "(%s %s %s)" % (vec(n[2], col), op, vec(n[3], col))
    if k == "in":
        if not all(_lit(x) for x in n[2]):
            raise NoSketch("IN with non-literal values")
        s = "%s.isin([%s])" % (_series(n[1], col), ", ".join(vec(x) for x in n[2]))
        return "~" + s if n[3] else s
    if k == "if":
        conds = ", ".join(vec(c, col) for c, _ in n[1])
        vals = ", ".join(vec(v, col) for _, v in n[1])
        return "np.select([%s], [%s], default=%s)" % (conds, vals, vec(n[2], col))
    if k == "call":
        name, a = n[1], n[2]
        if name == "iif":
            return "np.where(%s, %s, %s)" % (vec(a[0], col), vec(a[1], col), vec(a[2], col))
        if name == "isempty":
            return "empty(%s)" % vec(a[0], col)
        if name == "isnull":
            return "%s.isna()" % _series(a[0], col)
        if name in ("contains", "startswith", "endswith"):
            if not _lit(a[1]):
                raise NoSketch("search text taken from a column")
            exact = len(a) > 2 and a[2] == ("num", "0")
            if name == "contains":
                return "%s.str.contains(%s, case=%s, regex=False)" % (_series(a[0], col), vec(a[1]), exact)
            method = name.replace("with", "swith")
            s = _series(a[0], col)
            return "%s.str.%s(%s)" % (s if exact else s + ".str.lower()", method,
                                      vec(a[1]) if exact else repr(str(a[1][1]).lower()))
        if name == "left" and a[1][0] == "num":
            return "%s.str[:%s]" % (_series(a[0], col), a[1][1])
        if name == "right" and a[1][0] == "num":
            return "%s.str[-%s:]" % (_series(a[0], col), a[1][1])
        if name == "substring" and all(x[0] == "num" for x in a[1:]):
            start = int(a[1][1])
            return "%s.str[%d:%s]" % (_series(a[0], col), start, start + int(a[2][1]) if len(a) > 2 else "")
        if name in ("length", "uppercase", "lowercase", "trim", "trimleft", "trimright", "titlecase") and len(a) == 1:
            method = {"length": "len", "uppercase": "upper", "lowercase": "lower", "trim": "strip", "trimleft": "lstrip",
                      "trimright": "rstrip", "titlecase": "title"}[name]
            return "%s.str.%s()" % (_series(a[0], col), method)
        if name == "replace" and _lit(a[1]) and _lit(a[2]):
            return "%s.str.replace(%s, %s, regex=False)" % (_series(a[0], col), vec(a[1]), vec(a[2]))
        if name.startswith("regex_") and len(a) > 1 and a[1][0] == "str" and "\\G" in a[1][1]:
            raise NoSketch("\\G in a regular expression has no Python equivalent")
        if name == "regex_replace" and a[1][0] == "str" and a[2][0] == "str":
            case = len(a) > 3 and a[3] == ("num", "0")
            return "%s.str.replace(%r, %r, regex=True, case=%s)" % (_series(a[0], col), a[1][1], _regex_repl(a[2][1]), case)
        if name == "regex_match" and a[1][0] == "str":
            return "%s.str.fullmatch(%r, case=%s)" % (_series(a[0], col), a[1][1], len(a) > 2 and a[2] == ("num", "0"))
        if name == "padleft" and a[1][0] == "num" and a[2][0] == "str":
            return "%s.str.pad(%s, side=\"left\", fillchar=%r)" % (_series(a[0], col), a[1][1], a[2][1][:1])
        if name == "tonumber" and len(a) == 1:
            return "pd.to_numeric(%s, errors=\"coerce\").fillna(0)" % vec(a[0], col)
        if name == "tostring" and len(a) == 1:
            return "%s.astype(str)" % _series(a[0], col)
        if name == "tostring" and len(a) == 2 and a[1][0] == "num":
            return '%s.map(lambda v: f"{v:.%sf}")' % (_series(a[0], col), a[1][1])
        if name in ("datetimetoday", "datetimenow") and not a:
            return "pd.Timestamp.today().normalize()" if name == "datetimetoday" else "pd.Timestamp.now()"
        if name in ("todate", "todatetime"):
            inner = a[0]
            if inner[0] in ("arith", "num", "call") and not (inner[0] == "call" and inner[1] in ("tostring", "left", "right")):
                s = "pd.to_datetime(%s, unit=\"D\", origin=\"1899-12-30\")" % vec(inner, col)
            else:
                s = "pd.to_datetime(%s, errors=\"coerce\")" % vec(inner, col)
            return s + (".dt.normalize()" if name == "todate" else "")
        if name == "datetimediff" and a[2][0] == "str" and a[2][1].lower().rstrip("s") == "day":
            return "(pd.to_datetime(%s) - pd.to_datetime(%s)).dt.days" % (vec(a[0], col), vec(a[1], col))
        if name == "abs":
            return "%s.abs()" % vec(a[0], col) if not _lit(a[0]) else "abs(%s)" % vec(a[0])
        if name == "round" and a[1][0] == "num":
            m = a[1][1]
            return "%s.round()" % vec(a[0], col) if m in ("1", "1.0") else "((%s / %s).round() * %s)" % (vec(a[0], col), m, m)
        raise NoSketch("no one-line pandas form for %s()" % expr.PROPER.get(name, name))
    raise NoSketch(k)
