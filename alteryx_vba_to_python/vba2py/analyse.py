"""Logic specification of VBA macros: for each Sub, what it does to which workbook, sheet and range,
in order, with the Select / Activate / Selection / With chains resolved into explicit targets.

It follows the active workbook, sheet and selection through straight-line code. Inside If and loop
bodies the same tracking continues; where the state after a branch cannot be known it says so.
Nothing is dropped: a statement it has no wording for is printed as `vba:` with its source.
"""
import re
import textwrap

from . import vbaparse as vp

T = vp.to_text


def _bgr(n):
    try:
        n = int(float(n))
    except ValueError:
        return n
    return "#%02X%02X%02X" % (n & 255, (n >> 8) & 255, (n >> 16) & 255)


class Sub:
    def __init__(self, proc, procs):
        self.p, self.procs = proc, procs
        self.wb, self.sh, self.sel, self.clip = "ThisWorkbook", "(sheet active at the start)", None, None
        self.alias, self.withs, self.lines, self.calls, self.opened = {}, [], [], [], []
        self.stack = []

    # ---- describing expressions
    def lit(self, n):
        """Text of an address or name: "A2:AQ" & n -> A2:AQ<n>"""
        if n[0] == "str":
            return n[1]
        if n[0] == "bin" and n[1] == "&":
            return self.lit(n[2]) + self.lit(n[3])
        if n[0] == "paren":
            return self.lit(n[1])
        return "<%s>" % T(n)

    def sheet(self):
        return "[%s]%s" % (self.wb, self.sh)

    def obj(self, n):
        """-> (kind, text): kind in wb / sh / rng / other"""
        k = n[0]
        if k == "br":
            return "rng", "%s!%s" % (self.sheet(), n[1])
        if k == "with":
            return self.withs[-1] if self.withs else ("other", "?")
        if k == "name":
            low = n[1].lower()
            if low in self.alias:
                return self.alias[low]
            if low == "thisworkbook":
                return "wb", "ThisWorkbook"
            if low == "activeworkbook":
                return "wb", self.wb
            if low == "activesheet":
                return "sh", self.sheet()
            if low in ("selection", "activecell"):
                return "rng", self.sel or "(current selection)"
            if low in ("cells", "rows", "columns"):
                return "rng", "%s!(all %s)" % (self.sheet(), low)
            return "other", n[1]
        if k == "call":
            f, a = n[1], [x for x in n[2] if x]
            name = (f[1] if f[0] == "name" else f[2]).lower()
            owner = self.obj(f[1]) if f[0] == "mem" else None
            if name in ("sheets", "worksheets") and a:
                wb = owner[1] if owner and owner[0] == "wb" else self.wb
                return "sh", "[%s]%s" % (wb, self.lit(a[0][1]))
            if name in ("range", "cells", "rows", "columns") and a:
                where = owner[1] if owner and owner[0] == "sh" else self.sheet()
                if name == "cells" and len(a) == 2 and T(a[0][1]).lower().endswith("rows.count"):
                    return "rng", "%s!(bottom of column %s)" % (where, self.lit(a[1][1]).upper())
                if owner and owner[0] == "rng":
                    where = owner[1].split("!")[0]
                addr = ":".join(self.lit(x[1]) for x in a) if name == "range" else \
                    "%s %s" % (name, ", ".join(self.lit(x[1]) if x[1][0] == "str" else T(x[1]) for x in a))
                return "rng", "%s!%s" % (where, addr)
            if name == "workbooks" and a:
                return "wb", self.lit(a[0][1])
            if name == "end" and owner:
                how = {"xlup": "going up", "xldown": "going down", "xltoleft": "going left", "xltoright": "going right"}
                return "rng", "last filled cell %s from %s" % (how.get(T(a[0][1]).lower(), T(a[0][1])), owner[1])
            if name in ("offset", "resize") and owner:
                return "rng", "%s.%s(%s)" % (owner[1], name.capitalize(), vp.args_text(n[2]))
            if name in ("formatconditions", "item") and owner:
                return "other", "conditional format %s of %s" % (vp.args_text(n[2]), owner[1])
        if k == "mem":
            owner, low = self.obj(n[1]), n[2].lower()
            if low in ("columns", "rows", "cells", "entirecolumn", "entirerow") and owner[0] == "rng":
                return "rng", owner[1] + ("" if low in ("columns", "rows", "cells") else " (entire %s)" % low[6:])
            if low in ("cells", "usedrange") and owner[0] == "sh":
                return "rng", "%s!(%s)" % (owner[1], "all cells" if low == "cells" else "used range")
            if low == "activesheet" and owner[0] == "wb":
                return "sh", "[%s](its active sheet)" % owner[1]
            if low in ("interior", "font", "formatconditions"):
                return "other", "%s of %s" % (low, owner[1])
            return "other", "%s.%s" % (owner[1], n[2])
        return "other", T(n)

    def value(self, n):
        """Readable text of a value expression, with ranges resolved."""
        if n[0] in ("br", "call", "mem") and n[0] != "str":
            kind, text = self.obj(n)
            if kind == "rng":
                return text
            if n[0] == "mem" and n[2].lower() in ("row", "column", "value", "value2", "text"):
                kind, text = self.obj(n[1])
                if kind == "rng":
                    return text if n[2].lower().startswith(("value", "text")) else "%s of %s" % (n[2].lower(), text)
        if n[0] == "bin":
            op = n[1]
            return "%s %s %s" % (self.value(n[2]), op.capitalize() if op.isalpha() else op, self.value(n[3]))
        if n[0] == "un":
            return ("Not " if n[1] == "not" else "-") + self.value(n[2])
        return T(n)

    def add(self, verb, text, depth, tag=""):
        self.lines.append((depth, "%-8s %s%s" % (verb, text, "   [%s]" % tag if tag else "")))

    # ---- statements
    def run(self, stmts, depth=0):
        for s in stmts:
            k = s["k"]
            if k == "comment":
                self.add("'", s["text"], depth)
            elif k == "assign":
                self.assign(s, depth)
            elif k == "call":
                self.call(s, depth)
            elif k == "if":
                for i, (c, body) in enumerate(s["branches"]):
                    self.add("if" if i == 0 else "elif", self.value(c) + ":", depth)
                    self.branch(body, depth + 1)
                if s["orelse"]:
                    self.add("else:", "", depth)
                    self.branch(s["orelse"], depth + 1)
            elif k == "with":
                self.withs.append(self.obj(s["target"]))
                if self.withs[-1][0] == "other" and not self.withs[-1][1].startswith(("interior", "font", "conditional")):
                    self.add("with", self.withs[-1][1] + ":", depth)
                self.run(s["body"], depth)
                self.withs.pop()
            elif k in ("for", "foreach", "do", "select"):
                verb, head = {
                    "for": lambda: ("for", "%s = %s to %s%s:" % (s["var"], self.value(s["start"]), self.value(s["stop"]),
                                                               " step " + T(s["step"]) if s["step"] else "")),
                    "foreach": lambda: ("for each", "%s in %s:" % (s["var"], self.obj(s["coll"])[1])),
                    "do": lambda: ("loop", "%s%s:" % ("%s %s" % (s["pre"][0], self.value(s["pre"][1])) if s["pre"] else "",
                                                      " (repeat %s %s)" % (s["post"][0], self.value(s["post"][1])) if s["post"] else "")),
                    "select": lambda: ("select", "on %s:" % self.value(s["subject"]))}[k]()
                self.add(verb, head, depth)
                if k == "select":
                    for tests, body in s["cases"]:
                        label = "otherwise" if tests is None else ", ".join(
                            T(t[1]) if t[0] == "eq" else "%s to %s" % (T(t[1]), T(t[2])) if t[0] == "to" else "%s %s" % (t[1], T(t[2]))
                            for t in tests)
                        self.add("case", label + ":", depth + 1)
                        self.branch(body, depth + 2)
                else:
                    self.branch(s["body"], depth + 1)
            elif k == "onerror":
                self.add("on error", {"goto": "jump to %s" % s["label"], "resume": "carry on with the next statement",
                                      "off": "stop (default handling)"}[s["mode"]], depth)
            elif k == "label":
                self.add("label", "%s:" % s["name"], max(depth - 1, 0))
                self.wb = self.sh = "(as at the point of the error)"
            elif k == "exit":
                self.add("exit", s["what"], depth)
            elif k == "dim":
                pass
            elif k == "const":
                self.add("const", "%s = %s" % (s["name"], T(s["value"])), depth)
            else:
                self.add("!! vba:", s["src"], depth, "not understood")

    def branch(self, body, depth):
        before = (self.wb, self.sh, self.sel)
        self.run(body, depth)
        if (self.wb, self.sh) != before[:2]:
            self.add("note", "after this branch the active workbook/sheet depends on whether it ran", depth)
            self.wb, self.sh, self.sel = "(depends on the branch above)", "(depends on the branch above)", None

    def assign(self, s, depth):
        t, v = s["target"], s["value"]
        if t[0] == "name":
            kind, text = self.obj(v) if (s["set"] or v[0] in ("br",)) or (v[0] == "call" and self.obj(v)[0] == "rng") else ("val", "")
            if s["set"]:
                self.alias[t[1].lower()] = (kind, text)
                if kind == "other":
                    low = T(v).lower()
                    self.add("!! set" if "createobject" in low or "getobject" in low else "set",
                             "%s = %s" % (t[1], T(v)), depth, "another program: not convertible" if "createobject" in low else "")
                    self.alias[t[1].lower()] = ("other", t[1])
                return
            if kind == "rng":
                self.add("read", "%s → %s" % (text, t[1]), depth)
            else:
                self.add("let", "%s = %s" % (t[1], self.value(v)), depth)
            return
        prop = t[2].lower() if t[0] == "mem" else ""
        target = self.obj(t[1] if prop in ("value", "value2", "formula", "formular1c1", "numberformat") else t)
        if target[0] == "rng":
            if prop in ("formula", "formular1c1"):
                self.add("formula", "%s = %s%s" % (target[1], self.value(v), "   (R1C1 notation)" if prop.endswith("c1") else ""), depth)
            elif v[0] == "mem" and v[2].lower() in ("value", "value2") and self.obj(v[1]) == target:
                self.add("to values", "%s   (formulas replaced by their results)" % target[1], depth)
            elif prop == "numberformat":
                self.add("format", "%s number format %s" % (target[1], T(v)), depth)
            else:
                status = target[1].startswith("[ThisWorkbook]") and (v[0] == "str" or "err.description" in T(v).lower())
                self.add("write", "%s = %s" % (target[1], self.value(v)), depth, "status" if status else "")
            return
        if t[0] == "mem":
            owner = self.obj(t[1])[1]
            val = T(v)
            if prop == "color":
                val = "%s (colour %s)" % (val, _bgr(val))
            m = re.match(r"(?:(interior|font) of )?conditional format (\S+) of ", owner)
            if owner.lower().startswith("application") or t[1] == ("name", "Application"):
                self.add("excel", "%s = %s" % (t[2], val), depth)
            elif m:
                self.add("", "… rule %s %s%s = %s" % (m.group(2), {"interior": "fill ", "font": "font "}.get(m.group(1), ""), t[2], val), depth)
            else:
                self.add("set", "%s of %s = %s" % (t[2], owner, val), depth)
            return
        self.add("vba:", s["src"], depth)

    def call(self, s, depth):
        t, args = s["target"], s["args"]
        if t[0] == "call" and args is None:
            t, args = t[1], t[2]
        named = {a[0].lower(): a[1] for a in (args or []) if a and a[0]}
        plain = [a[1] for a in (args or []) if a and not a[0]]
        if t[0] == "name":
            low = t[1].lower()
            if low in self.procs:
                self.calls.append(self.procs[low])
                self.add("call", "%s(%s)" % (self.procs[low], vp.args_text(args)), depth)
            elif low == "msgbox":
                self.add("message", vp.args_text(args), depth, "shown to the user")
            else:
                self.add("vba:", s["src"], depth)
            return
        if t[0] != "mem":
            self.add("vba:", s["src"], depth)
            return
        m, owner = t[2].lower(), self.obj(t[1])
        if m in ("select", "activate"):
            if owner[0] == "rng":
                self.sel = owner[1]
            elif owner[0] == "sh":
                self.wb, self.sh = re.match(r"\[(.*?)\](.*)$", owner[1]).groups()
                self.sel = None
            elif owner[0] == "wb":
                self.wb, self.sh, self.sel = owner[1], "(its active sheet)", None
            else:
                self.add("vba:", s["src"], depth)
        elif m == "open" and T(t[1]).lower() == "workbooks":
            f = named.get("filename") or (plain[0] if plain else ("str", "?"))
            self.stack.append((self.wb, self.sh))
            self.wb, self.sh, self.sel = self.lit(f).strip("<>") if f[0] != "str" else f[1], "(its active sheet)", None
            self.wb = "<%s>" % self.wb if f[0] != "str" else self.wb
            self.opened.append(self.wb)
            self.add("open", "workbook %s" % self.wb, depth)
        elif m in ("save", "saveas") and owner[0] == "wb":
            self.add("save", "workbook %s%s" % (owner[1], " as " + vp.args_text(args) if m == "saveas" else ""), depth)
        elif m == "close" and owner[0] == "wb":
            self.add("close", "workbook %s%s" % (owner[1], "  (%s)" % vp.args_text(args) if args else ""), depth)
            if owner[1] == self.wb and self.stack:
                self.wb, self.sh = self.stack.pop()
                self.sel = None
        elif m in ("clearcontents", "clear", "delete") and owner[0] == "rng":
            self.add({"clearcontents": "clear", "clear": "clear all", "delete": "delete"}[m], owner[1], depth)
        elif m == "copy" and owner[0] == "rng":
            dest = named.get("destination") or (plain[0] if plain else None)
            self.clip = owner[1]
            self.add("copy", owner[1] + (" → %s" % self.obj(dest)[1] if dest else ""), depth)
        elif m == "pastespecial" and owner[0] == "rng":
            what = T(named.get("paste") or (plain[0] if plain else ("name", "xlPasteAll"))).replace("xlPaste", "").lower()
            self.add("paste", "%s → %s   (from %s)" % (what, owner[1], self.clip or "the clipboard"), depth)
        elif m == "paste" and owner[0] == "sh":
            dest = named.get("destination") or (plain[0] if plain else None)
            self.add("paste", "all → %s   (from %s)" % (self.obj(dest)[1] if dest else self.sel or "the selection",
                                                         self.clip or "the clipboard"), depth)
        elif m == "autofit" and owner[0] == "rng":
            self.add("autofit", "columns of %s" % owner[1], depth)
        elif m == "add" and owner[1].startswith("formatconditions"):
            f = named.get("formula1")
            self.add("cond.fmt", "%s: when %s%s" % (owner[1][20:], T(f)[1:-1].replace('""', '"') if f else vp.args_text(args),
                                                    "" if T(named.get("type", ("name", "xlExpression"))) == "xlExpression"
                                                    else "  (%s)" % vp.args_text(args)), depth)
        elif m == "setfirstpriority":
            self.add("", "… that rule moved to first priority", depth)
        else:
            self.add("vba:", s["src"], depth)


def _similar(a, b):
    """Same steps apart from a few literals? -> list of 'old→new' pairs, else None."""
    if len(a) != len(b) or a == b:
        return [] if a == b else None
    pairs = []
    for (da, la), (db, lb) in zip(a, b):
        ta, tb = re.findall(r"[A-Za-z_]\w*|\d+|\S", la), re.findall(r"[A-Za-z_]\w*|\d+|\S", lb)
        if da != db or len(ta) != len(tb):
            return None
        for x, y in zip(ta, tb):
            if x != y and (x, y) not in pairs:
                pairs.append((x, y))
    return ["%s→%s" % p for p in pairs] if len(pairs) <= 8 else None


def build(modules, report, source="", width=150):
    procs = {p["name"].lower(): p["name"] for m in modules for p in m["procs"]}
    out = ["VBA LOGIC SPECIFICATION   source: %s" % source,
           "modules: %s" % ", ".join("%s (%d procedures)" % (m["name"], len(m["procs"])) for m in modules)]
    total, bad = report["statements"], len(report["problems"])
    out.append("statements: %d, converted to Python: %d, not converted: %d" % (total, total - bad, bad))
    subs = []
    for m in modules:
        for p in m["procs"]:
            sub = Sub(p, procs)
            sub.run(p["body"])
            subs.append(sub)
    callers = {}
    for s in subs:
        for c in s.calls:
            callers.setdefault(c, []).append(s.p["name"])
    roots = [s for s in subs if s.p["name"] not in callers]
    out += ["", "CALL TREE (a Sub that nothing else calls is an entry point):"]

    def tree(sub, depth, seen):
        out.append("  " * depth + "  " + sub.p["name"] + ("  (see above)" if sub.p["name"] in seen and sub.calls else ""))
        if sub.p["name"] in seen:
            return
        seen.add(sub.p["name"])
        for c in dict.fromkeys(sub.calls):
            tree(next(x for x in subs if x.p["name"] == c), depth + 1, seen)
    seen = set()
    for r in roots or subs[:1]:
        tree(r, 0, seen)
    opened = list(dict.fromkeys(o for s in subs for o in s.opened))
    if opened:
        out += ["", "WORKBOOKS OPENED: " + ", ".join(opened)]
    if report["problems"]:
        out += ["", "NOT CONVERTED (the generated Python raises at these statements):"]
        for mod, line, src, why in report["problems"]:
            out += textwrap.wrap("%s line %d: %s   [%s]" % (mod, line, src, why), width, initial_indent="  ",
                                 subsequent_indent="      ")
    done = []
    for s in subs:
        p = s.p
        info = report["procs"].get(p["name"], {"statements": 0, "problems": []})
        out += ["", "%s %s(%s)   [%s, lines %d-%d]   %d statements, %d not converted" % (
            p["kind"].capitalize(), p["name"], ", ".join(q["name"] for q in p["params"]), p["module"], p["line"], p["end"],
            info["statements"], len(info["problems"]))]
        same = next(((d, _similar(d.lines, s.lines)) for d in done if _similar(d.lines, s.lines) is not None and len(s.lines) > 3), None)
        if same:
            out += textwrap.wrap("same steps as %s%s" % (same[0].p["name"], ", with: " + ", ".join(same[1]) if same[1] else ""),
                                 width, initial_indent="  ", subsequent_indent="      ")
            continue
        done.append(s)
        for depth, line in s.lines:
            out += textwrap.wrap(line, width, initial_indent="  " + "    " * depth, subsequent_indent="  " + "    " * depth + "         ",
                                 break_long_words=False, break_on_hyphens=False) or [""]
    return out
