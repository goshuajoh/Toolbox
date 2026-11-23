"""The logic of VBA macros, written for a person: what each Sub reads, opens and reports, then its steps.

Select / Activate / Selection / With chains are followed so every step names the workbook, sheet and
range it acts on. Recorded-macro noise is folded away: a Copy and its Paste become one step,
conditional-format property settings become one line per rule, Excel settings move to the header.
A statement it has no wording for is shown as VBA, never dropped. Standard library only.
"""
import re
from collections import defaultdict

from . import vbaparse as vp

T = vp.to_text
THEME = {"xlthemecolordark1": "Background 1 (white)", "xlthemecolorlight1": "Text 1 (black)",
         "xlthemecolordark2": "Background 2", "xlthemecolorlight2": "Text 2", "1": "Background 1 (white)",
         "2": "Text 1 (black)"}
THEME.update({"xlthemecoloraccent%d" % n: "Accent %d" % n for n in range(1, 7)})
UP = {"xlup": "up", "xldown": "down", "xltoleft": "left", "xltoright": "right"}
SETTINGS = {"displayalerts", "screenupdating", "enableevents", "calculation", "cutcopymode", "statusbar"}


def _bgr(n):
    try:
        n = int(float(n))
    except ValueError:
        return str(n)
    return "#%02X%02X%02X" % (n & 255, (n >> 8) & 255, (n >> 16) & 255)


def _col(letters):
    n = 0
    for c in letters.upper():
        n = n * 26 + ord(c) - 64
    return n


def _letters(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def r1c1_to_a1(formula, cell):
    """'=RC[-10]&" "&RC[-4]' written in AT3 -> '=AJ3&" "&AP3' (text in quotes left alone)."""
    m = re.match(r"\$?([A-Za-z]{1,3})\$?(\d+)", cell or "")
    if not m:
        return None
    row, col = int(m.group(2)), _col(m.group(1))

    def ref(x):
        r = row + int(x.group(1)) if x.group(1) else int(x.group(2)) if x.group(2) else row
        c = col + int(x.group(3)) if x.group(3) else int(x.group(4)) if x.group(4) else col
        return ("$" if x.group(4) else "") + _letters(c) + ("$" if x.group(2) else "") + str(r)
    parts = formula.split('"')
    return '"'.join(p if k % 2 else re.sub(r"\bR(?:\[(-?\d+)\]|(\d+))?C(?:\[(-?\d+)\]|(\d+))?(?![A-Za-z0-9_(])", ref, p)
                    for k, p in enumerate(parts))


class Loc:
    """A place in Excel: workbook, sheet, address (any may be text with {variables})."""

    def __init__(self, wb, sheet, addr=""):
        self.wb, self.sheet, self.addr = wb, sheet, addr

    def text(self):
        unknown_wb = self.wb.startswith("(")
        sheet = self.sheet
        if sheet.startswith("("):
            where = "the active sheet" if sheet == "(active sheet)" else sheet[1:-1]
        else:
            where = sheet if self.wb == "ThisWorkbook" or unknown_wb else "[%s]%s" % (self.wb, sheet)
        if sheet.startswith("(") and self.wb not in ("ThisWorkbook",) and not unknown_wb:
            where += " of [%s]" % self.wb
        if not self.addr:
            text = where
        elif self.addr.startswith(("rows ", "columns ", "(")) or sheet.startswith("("):
            text = "%s of %s" % (self.addr.strip("()"), where)
        else:
            text = "%s!%s" % (where, self.addr)
        return text + (" (in the workbook %s)" % self.wb[1:-1] if unknown_wb else "")

    def __eq__(self, other):
        return isinstance(other, Loc) and (self.wb, self.sheet, self.addr) == (other.wb, other.sheet, other.addr)


class Sub:
    def __init__(self, proc, procs):
        self.p, self.procs = proc, procs
        self.wb, self.sh, self.sel, self.clip = "ThisWorkbook", "(active sheet)", None, None
        self.alias, self.withs, self.calls, self.opened = {}, [], [], []
        self.reads, self.status, self.settings, self.problems = [], [], [], []
        self.cf = defaultdict(list)                   # (wb, sheet) -> rules, first = priority 1
        self.comment = None
        self.main, self.handler = [], []
        self.stack = []
        self.writes, self.in_handler = [], False

    # ---- naming things
    def lit(self, n):
        """Text of an address or name: "A2:AQ" & n -> A2:AQ{n}"""
        if n[0] == "str":
            return n[1]
        if n[0] == "bin" and n[1] == "&":
            return self.lit(n[2]) + self.lit(n[3])
        if n[0] == "paren":
            return self.lit(n[1])
        if n[0] == "num":
            return n[1]
        return "{%s}" % T(n)

    def here(self, addr=""):
        return Loc(self.wb, self.sh, addr)

    def obj(self, n):
        """-> (kind, Loc or text): kind in wb / sh / rng / cf / other"""
        k = n[0]
        if k == "br":
            return "rng", self.here(n[1])
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
                return "sh", self.here()
            if low in ("selection", "activecell"):
                return "rng", self.sel or self.here("(selected cells)")
            if low in ("cells", "rows", "columns"):
                return "rng", self.here("(all %s)" % low)
            return "other", n[1]
        if k == "call":
            f, a = n[1], [x for x in n[2] if x]
            name = (f[1] if f[0] == "name" else f[2]).lower()
            owner = self.obj(f[1]) if f[0] == "mem" else None
            if name in ("sheets", "worksheets") and a:
                wb = owner[1] if owner and owner[0] == "wb" else self.wb
                return "sh", Loc(wb, self.lit(a[0][1]))
            if name in ("range", "cells", "rows", "columns") and a:
                base = owner[1] if owner and owner[0] in ("sh", "rng") else self.here()
                base = Loc(base.wb, base.sheet)
                if name == "range":
                    base.addr = ":".join(self.lit(x[1]) for x in a)
                elif name == "cells" and len(a) == 2:
                    col = self.lit(a[1][1])
                    col = col.upper() if col.isalpha() else _letters(int(col)) if col.isdigit() else "{%s}" % T(a[1][1])
                    row = T(a[0][1])
                    base.addr = "%s(last row)" % col if row.lower().endswith("rows.count") else \
                        "%s%s" % (col, row if row.isdigit() else "{%s}" % row)
                else:
                    span = self.lit(a[0][1])
                    base.addr = ("rows " if name == "rows" else "columns ") + span
                return "rng", base
            if name == "workbooks" and a:
                return "wb", self.lit(a[0][1])
            if name == "end" and owner and owner[0] == "rng":
                d = UP.get(T(a[0][1]).lower(), T(a[0][1]))
                return "rng", Loc(owner[1].wb, owner[1].sheet, "(last filled cell going %s from %s)" % (d, owner[1].addr))
            if name in ("offset", "resize") and owner and owner[0] == "rng":
                return "rng", Loc(owner[1].wb, owner[1].sheet, "%s.%s(%s)" % (owner[1].addr, name.capitalize(),
                                                                            vp.args_text(n[2])))
            if name in ("formatconditions", "item") and owner and owner[0] in ("rng", "cfs"):
                rng = owner[1]
                return "cf", (rng, vp.args_text(n[2]))
        if k == "mem":
            owner, low = self.obj(n[1]), n[2].lower()
            if owner[0] == "rng" and low in ("columns", "rows", "cells"):
                return owner
            if owner[0] == "rng" and low in ("entirerow", "entirecolumn"):
                return "rng", Loc(owner[1].wb, owner[1].sheet, "%s (entire %s)" % (owner[1].addr, low[6:]))
            if owner[0] == "sh" and low in ("cells", "usedrange"):
                return "rng", Loc(owner[1].wb, owner[1].sheet, "(all cells)" if low == "cells" else "(used range)")
            if owner[0] == "wb" and low == "activesheet":
                return "sh", Loc(owner[1], "(its active sheet)")
            if owner[0] == "rng" and low == "formatconditions":
                return "cfs", owner[1]
            if owner[0] == "cf" and low in ("interior", "font"):
                return "cfp", (owner[1], low)
            if owner[0] == "rng" and low in ("interior", "font"):
                return "fmt", (owner[1], low)
            return "other", "%s.%s" % (owner[1].text() if isinstance(owner[1], Loc) else owner[1], n[2])
        return "other", T(n)

    def value(self, n):
        """Readable text of a value: ranges resolved, Err.Description named."""
        k = n[0]
        if k == "mem" and T(n).lower() == "err.description":
            return "the error message"
        if k == "mem" and n[2].lower() == "autofiltermode":
            kind, what = self.obj(n[1])
            return "%s has an AutoFilter" % (what.text() if isinstance(what, Loc) else what)
        if k in ("br", "call", "mem"):
            kind, what = self.obj(n)
            if kind == "rng":
                return what.text()
            if k == "mem" and n[2].lower() in ("row", "column", "value", "value2", "text", "count"):
                kind, what = self.obj(n[1])
                if kind == "rng":
                    if n[2].lower() == "row" and what.addr.startswith("(last filled cell going up"):
                        m = re.search(r"from \$?([A-Z]+)", what.addr)
                        return "the last filled row of column %s" % m.group(1) if m else "row of " + what.text()
                    return what.text() if n[2].lower() in ("value", "value2", "text") else \
                        "%s of %s" % (n[2].lower(), what.text())
        if k == "bin":
            op = n[1]
            return "%s %s %s" % (self.value(n[2]), op.capitalize() if op.isalpha() else op, self.value(n[3]))
        if k == "un":
            return ("Not " if n[1] == "not" else "-") + self.value(n[2])
        if k == "paren":
            return "(%s)" % self.value(n[1])
        return T(n)

    def cond(self, n, negate=False):
        if negate:
            if n[0] == "bin" and n[1] in ("=", "<>", "<", ">", "<=", ">="):
                flip = {"=": "<>", "<>": "=", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}[n[1]]
                return self.value(("bin", flip, n[2], n[3]))
            if n[0] == "un" and n[1] == "not":
                return self.value(n[2])
            return "Not (%s)" % self.value(n)
        return self.value(n)

    # ---- steps
    def add(self, out, text, pre=None, children=None, kind=""):
        if self.comment:
            text = "‘%s’ — %s" % (self.comment, text)
            self.comment = None
        item = {"text": text, "pre": pre or [], "children": children or [], "kind": kind}
        out.append(item)
        return item

    def run(self, stmts, out):
        for s in stmts:
            k = s["k"]
            if k == "comment":
                self.comment = s["text"] if not self.comment else self.comment + " " + s["text"]
            elif k == "assign":
                self.assign(s, out)
            elif k == "call":
                self.call(s, out)
            elif k == "if":
                (c0, b0), rest = s["branches"][0], s["branches"][1:]
                if not b0 and not rest and s["orelse"]:
                    item = self.add(out, "If %s:" % self.cond(c0, negate=True))
                    self.branch(s["orelse"], item["children"])
                    continue
                for n, (c, body) in enumerate(s["branches"]):
                    item = self.add(out, ("If %s:" if n == 0 else "Otherwise, if %s:") % self.cond(c))
                    self.branch(body, item["children"])
                    if not item["children"]:
                        item["children"].append({"text": "nothing"})
                if s["orelse"]:
                    item = self.add(out, "Otherwise:")
                    self.branch(s["orelse"], item["children"])
            elif k == "with":
                self.withs.append(self.obj(s["target"]))
                self.run(s["body"], out)
                self.withs.pop()
            elif k == "for":
                step = " step " + T(s["step"]) if s["step"] else ""
                item = self.add(out, "For **%s** = %s to %s%s:" % (s["var"], self.value(s["start"]), self.value(s["stop"]), step))
                self.branch(s["body"], item["children"])
            elif k == "foreach":
                kind, what = self.obj(s["coll"])
                item = self.add(out, "For each **%s** in %s:" % (s["var"], what.text() if isinstance(what, Loc) else what))
                self.branch(s["body"], item["children"])
            elif k == "do":
                if s["pre"]:
                    head = "Repeat %s %s:" % (s["pre"][0], self.value(s["pre"][1]))
                else:
                    head = "Repeat (until %s):" % ("the loop is left" if not s["post"] else
                                                   ("%s is false" if s["post"][0] == "while" else "%s")
                                                   % self.value(s["post"][1]))
                item = self.add(out, head)
                self.branch(s["body"], item["children"])
            elif k == "select":
                item = self.add(out, "Depending on %s:" % self.value(s["subject"]))
                for tests, body in s["cases"]:
                    label = "anything else" if tests is None else " or ".join(
                        T(t[1]) if t[0] == "eq" else "%s to %s" % (T(t[1]), T(t[2])) if t[0] == "to" else
                        "%s %s" % (t[1], T(t[2])) for t in tests)
                    sub = {"text": "when %s:" % label, "pre": [], "children": []}
                    self.branch(body, sub["children"])
                    if not sub["children"]:
                        sub["text"] = "when %s: nothing" % label
                    item["children"].append(sub)
            elif k == "onerror":
                if s["mode"] == "resume":
                    self.add(out, "From here on, a step that fails is skipped (On Error Resume Next)")
                elif s["mode"] == "off":
                    self.add(out, "From here on, an error stops the macro (On Error GoTo 0)")
            elif k == "label":
                pass
            elif k == "exit":
                self.add(out, "Stop here" if s["what"] in ("sub", "function") else "Leave the loop", kind="exit")
            elif k in ("dim",):
                pass
            elif k == "const":
                self.add(out, "Set the constant **%s** = %s" % (s["name"], T(s["value"])))
            else:
                self.add(out, "VBA not described: `%s`" % s["src"], kind="raw")

    def branch(self, body, out):
        before = (self.wb, self.sh, self.sel)
        self.run(body, out)
        if (self.wb, self.sh) != before[:2]:
            self.wb, self.sh, self.sel = "(depends on the step above)", "(depends on the step above)", None

    def assign(self, s, out):
        t, v = s["target"], s["value"]
        if t[0] == "name":
            if s["set"]:
                kind, what = self.obj(v)
                low = T(v).lower()
                if "createobject" in low or "getobject" in low:
                    m = re.search(r'"([^"]+)"', T(v))
                    self.alias[t[1].lower()] = ("other", t[1])
                    self.add(out, "Start **%s** as %s — another program; not converted to Python" %
                             (m.group(1) if m else T(v), t[1]), kind="outside")
                    return
                self.alias[t[1].lower()] = (kind, what) if kind != "other" else ("other", t[1])
                if kind == "other":
                    self.add(out, "Let **%s** stand for %s" % (t[1], T(v)))
                return
            kind, what = self.obj(v) if v[0] in ("br", "call", "mem") else ("val", None)
            if kind == "rng" and v[0] != "mem":
                self.reads.append((t[1], what.text()))
                self.add(out, "Read **%s** from %s" % (t[1], what.text()))
            elif v[0] == "mem" and v[2].lower() in ("value", "value2", "text") and self.obj(v[1])[0] == "rng":
                where = self.obj(v[1])[1].text()
                self.reads.append((t[1], where))
                self.add(out, "Read **%s** from %s" % (t[1], where))
            elif t[1].lower() == self.p["name"].lower():
                self.add(out, "Return %s" % self.value(v))
            else:
                self.add(out, "Set **%s** = %s" % (t[1], self.value(v)))
            return
        prop = t[2].lower() if t[0] == "mem" else ""
        target = self.obj(t[1] if prop in ("value", "value2", "formula", "formular1c1", "numberformat") else t)
        if target[0] == "rng":
            where = target[1]
            if prop in ("formula", "formular1c1"):
                f = self.lit(v) if v[0] in ("str", "bin") else self.value(v)
                pre = [f]
                if prop == "formular1c1" and v[0] == "str":
                    a1 = r1c1_to_a1(v[1], where.addr.split(":")[0])
                    if a1:
                        pre = ["%s   (in R1C1 notation)" % v[1], "%s   (the same in A1 notation, in %s)" % (a1, where.addr.split(":")[0])]
                elif v[0] == "str":
                    pre = [v[1]]
                self.add(out, "Put this formula in %s:" % where.text(), pre=pre, kind="formula")
            elif v[0] == "mem" and v[2].lower() in ("value", "value2") and self.obj(v[1]) == target:
                self.add(out, "Replace the formulas in %s by their results" % where.text())
            elif prop == "numberformat":
                self.add(out, "Format %s as %s" % (where.text(), T(v)))
            else:
                text = self.value(v)
                self.writes.append((where.text(), text, out is self.handler or self.in_handler))
                self.add(out, "Put %s in %s" % (text if text != '""' else "nothing (empty)", where.text()))
            return
        if target[0] == "other" or t[0] != "mem":
            owner = self.obj(t[1]) if t[0] == "mem" else ("other", "")
            name = t[2] if t[0] == "mem" else T(t)
            if owner[0] == "cfp":
                (rng, idx), part = owner[1]
                rule = self.rule_at(rng, idx)
                if rule is not None:
                    val = T(v)
                    if part == "interior" and prop == "color":
                        rule["fill"] = _bgr(val)
                    elif part == "interior" and prop == "themecolor":
                        rule["fill"] = THEME.get(val.lower(), "theme colour " + val)
                    elif part == "interior" and prop == "tintandshade" and float(eval_num(val) or 0) and rule.get("fill"):
                        tint = eval_num(val)
                        rule["fill"] += ", %d%% %s" % (round(abs(tint) * 100), "darker" if tint < 0 else "lighter")
                    elif part == "font":
                        rule.setdefault("font", []).append("%s %s" % (name.lower(), _bgr(val) if prop == "color" else val))
                    return
            if owner[0] == "cf":
                rng, idx = owner[1]
                rule = self.rule_at(rng, idx)
                if rule is not None and prop == "stopiftrue":
                    rule["stop"] = T(v).lower() == "true"
                    return
            if (owner[0] == "other" and owner[1].lower() == "application") or (t[0] == "mem" and t[1] == ("name", "Application")):
                self.settings.append("%s = %s" % (name, T(v)))
                return
            if owner[0] == "sh" and prop == "autofiltermode" and T(v).lower() == "false":
                self.add(out, "Remove the AutoFilter from %s" % owner[1].text())
                return
            if owner[0] == "fmt":
                rng, part = owner[1]
                self.add(out, "Set the %s %s of %s to %s" % (part, name.lower(), rng.text(),
                                                             _bgr(T(v)) if prop == "color" else T(v)))
                return
            what = owner[1].text() if isinstance(owner[1], Loc) else owner[1]
            self.add(out, "Set %s of %s to %s" % (name, what, T(v)) if what else "VBA: `%s`" % s["src"])
            return
        self.add(out, "VBA not described: `%s`" % s["src"], kind="raw")

    def rule_at(self, rng, idx):
        rules = self.cf[(rng.wb, rng.sheet)]
        if not rules:
            return None
        if idx.lower().endswith(".count"):
            return rules[-1] if self._last_added is None else self._last_added
        try:
            n = int(idx)
        except ValueError:
            return None
        return rules[n - 1] if 1 <= n <= len(rules) else None

    _last_added = None

    def call(self, s, out):
        t, args = s["target"], s["args"]
        if t[0] == "call" and args is None:
            t, args = t[1], t[2]
        named = {a[0].lower(): a[1] for a in (args or []) if a and a[0]}
        plain = [a[1] for a in (args or []) if a and not a[0]]
        if t[0] == "name":
            low = t[1].lower()
            if low in self.procs:
                self.calls.append(self.procs[low])
                text = "Run [[%s|%s]]" % (anchor(self.procs[low]), self.procs[low])
                self.add(out, text + (" with %s" % vp.args_text(args) if args else ""))
            elif low == "msgbox":
                self.add(out, "Show the message %s" % self.value(plain[0]) if plain else "Show a message")
            elif low == "doevents":
                pass
            else:
                self.add(out, "VBA not described: `%s`" % s["src"], kind="raw")
            return
        if t[0] != "mem":
            self.add(out, "VBA not described: `%s`" % s["src"], kind="raw")
            return
        m, owner = t[2].lower(), self.obj(t[1])
        if m in ("select", "activate"):
            if owner[0] == "rng":
                self.sel = owner[1]
            elif owner[0] == "sh":
                self.wb, self.sh, self.sel = owner[1].wb, owner[1].sheet, None
            elif owner[0] == "wb":
                self.wb, self.sh, self.sel = owner[1], "(its active sheet)", None
            else:
                self.add(out, "VBA not described: `%s`" % s["src"], kind="raw")
        elif m == "open" and T(t[1]).lower() == "workbooks":
            f = named.get("filename") or (plain[0] if plain else ("str", "?"))
            self.stack.append((self.wb, self.sh))
            self.wb = f[1] if f[0] == "str" else "{%s}" % T(f)
            self.sh, self.sel = "(its active sheet)", None
            self.opened.append(self.wb)
            self.add(out, "Open the workbook %s" % self.wb)
        elif m in ("save", "saveas") and owner[0] == "wb":
            name = named.get("filename") or (plain[0] if plain else None)
            self.add(out, "Save %s%s" % (wbname(owner[1]), " as " + self.value(name) if m == "saveas" and name else ""))
        elif m == "close" and owner[0] == "wb":
            how = {"true": " (saving changes)", "false": " (without saving)"}.get(
                T(named.get("savechanges") or (plain[0] if plain else ("name", ""))).lower(), "")
            self.add(out, "Close %s%s" % (wbname(owner[1]), how))
            if owner[1] == self.wb and self.stack:
                self.wb, self.sh = self.stack.pop()
                self.sel = None
        elif m in ("clearcontents", "clear") and owner[0] == "rng":
            self.add(out, "Clear %s%s" % ("the contents of " if m == "clearcontents" else "everything in ", owner[1].text()))
        elif m == "delete" and owner[0] == "rng":
            self.add(out, "Delete %s" % owner[1].text())
        elif m == "copy" and owner[0] == "sh" and not args:
            self.add(out, "Copy the sheet %s into a new workbook, which becomes the active workbook" % owner[1].text())
            self.wb, self.sh, self.sel = "(the new workbook)", owner[1].sheet, None
        elif m == "copy" and owner[0] == "rng":
            dest = named.get("destination") or (plain[0] if plain else None)
            if dest is not None:
                self.add(out, "Copy %s to %s (values, formulas and formats)" % (owner[1].text(), self.obj(dest)[1].text()))
            else:
                self.clip = owner[1]
        elif m == "pastespecial" and owner[0] == "rng":
            what = T(named.get("paste") or (plain[0] if plain else ("name", "xlPasteAll"))).lower()
            words = {"xlpastevalues": "values only", "xlpasteformats": "formats only", "xlpasteformulas": "formulas only",
                     "xlpasteall": "values, formulas and formats"}.get(what, what)
            self.add(out, "Copy %s to %s (%s)" % (self.clip.text() if self.clip else "the copied cells", owner[1].text(), words))
        elif m == "paste" and owner[0] == "sh":
            dest = named.get("destination") or (plain[0] if plain else None)
            target = self.obj(dest)[1] if dest is not None else self.sel
            self.add(out, "Copy %s to %s (values, formulas and formats)" % (
                self.clip.text() if self.clip else "the copied cells", target.text() if target else "the selected cells"))
        elif m == "autofit" and owner[0] == "rng":
            self.add(out, "Fit the column widths of %s to their contents" % owner[1].text())
        elif m == "add" and owner[0] == "cfs":
            rng = owner[1]
            f = named.get("formula1") or (plain[1] if len(plain) > 1 else None)
            typ = T(named.get("type") or (plain[0] if plain else ("name", "xlExpression"))).lower()
            text = f[1] if f is not None and f[0] == "str" else (self.value(f) if f is not None else "?")
            rule = {"range": rng.text(), "when": text if typ in ("xlexpression", "2") else
                    "cell value %s %s" % (T(named.get("operator", ("name", ""))), text),
                    "fill": None, "stop": None, "note": self.comment}
            self.comment = None
            self.cf[(rng.wb, rng.sheet)].append(rule)
            self._last_added = rule
            out.append({"text": "", "pre": [], "children": [], "kind": "cf", "rule": rule})
        elif m == "setfirstpriority" and owner[0] == "cf":
            rng, idx = owner[1]
            rule = self.rule_at(rng, idx)
            rules = self.cf[(rng.wb, rng.sheet)]
            if rule is not None:
                rules.remove(rule)
                rules.insert(0, rule)
        elif m == "delete" and owner[0] in ("cfs", "cf"):
            self.add(out, "Delete the conditional formatting of %s" % (owner[1] if owner[0] == "cfs" else owner[1][0]).text())
        else:
            self.add(out, "VBA not described: `%s`" % s["src"], kind="raw")

    # ---- whole Sub
    def analyse(self):
        body = self.p["body"]
        on = next((i for i, s in enumerate(body) if s["k"] == "onerror" and s["mode"] == "goto"), None)
        label = next((i for i, s in enumerate(body) if on is not None and s["k"] == "label"
                      and s["name"].lower() == (body[on]["label"] or "").lower()), None)
        if on is not None and label is not None and label > on:
            self.run(body[:on] + body[on + 1:label], self.main)
            while self.main and self.main[-1].get("kind") == "exit":
                self.main.pop()
            self.wb, self.sh, self.sel = "(active when the error happened)", "(active sheet)", None
            self.in_handler = True
            self.run(body[label + 1:], self.handler)
            # a status cell is a cell the error handler writes; its other values come from the normal path
            cells = [w for w, _, h in self.writes if h]
            self.status = [(w, v) for w, v, h in sorted(self.writes, key=lambda x: x[2]) if w in cells]
        else:
            self.run(body, self.main)
        for steps in (self.main, self.handler):
            fold_rules(steps, self.cf)
        return self


def eval_num(text):
    try:
        return float(text.replace(" ", ""))
    except ValueError:
        return None


def fold_rules(steps, cf):
    """Consecutive conditional-format steps -> one step with one line per rule."""
    i = 0
    while i < len(steps):
        st = steps[i]
        if st.get("children"):
            fold_rules(st["children"], cf)
        if st.get("kind") != "cf":
            i += 1
            continue
        j = i
        while j < len(steps) and steps[j].get("kind") == "cf":
            j += 1
        rules = [s["rule"] for s in steps[i:j]]
        order = {id(r): n for rs in cf.values() for n, r in enumerate(rs, 1)}
        kids = []
        for r in rules:
            fmt = "fill " + r["fill"] if r.get("fill") else "format"
            if r.get("font"):
                fmt += ", font " + ", ".join(r["font"])
            extra = [] if r.get("stop") in (None, False) else ["stop if true"]
            kids.append({"text": "%s: when `%s` → %s (priority %d at the end%s)%s" % (
                r["range"], r["when"], fmt, order.get(id(r), 0), ", " + ", ".join(extra) if extra else "",
                " — ‘%s’" % r["note"] if r.get("note") else ""), "pre": [], "children": []})
        steps[i:j] = [{"text": "Add %d conditional format rule%s:" % (len(rules), "" if len(rules) == 1 else "s"),
                       "pre": [], "children": kids, "kind": "cfgroup"}]
        i += 1


def wbname(wb):
    return wb[1:-1] if wb.startswith("(") else "this workbook" if wb == "ThisWorkbook" else "the workbook " + wb


def anchor(name):
    return "sub-" + re.sub(r"\W+", "-", name.lower())


def _flat(items, depth=0):
    for it in items:
        yield depth, it["text"] + ("\n" + "\n".join(it["pre"]) if it.get("pre") else "")
        yield from _flat(it.get("children", []), depth + 1)


def _similar(a, b):
    """Same steps apart from a few words? -> list of 'old→new' pairs, else None."""
    a, b = list(_flat(a)), list(_flat(b))
    if len(a) != len(b):
        return None
    pairs = []
    for (da, la), (db, lb) in zip(a, b):
        ta, tb = re.findall(r"[A-Za-z_]\w*|\d+|\S", la), re.findall(r"[A-Za-z_]\w*|\d+|\S", lb)
        if da != db or len(ta) != len(tb):
            return None
        for x, y in zip(ta, tb):
            if x != y and (x, y) not in pairs:
                pairs.append((x, y))
    return ["%s → %s" % p for p in pairs] if len(pairs) <= 8 else None


def build(modules, report, source=""):
    """-> document blocks (see logicdoc)."""
    procs = {p["name"].lower(): p["name"] for m in modules for p in m["procs"]}
    subs = [Sub(p, procs).analyse() for m in modules for p in m["procs"]]
    callers = defaultdict(list)
    for s in subs:
        for c in s.calls:
            callers[c].append(s.p["name"])
    total, bad = report["statements"], len(report["problems"])
    blocks = [("h", 1, "Logic of the VBA in %s" % source, "top"),
              ("p", "Generated by vba2py. **How to read it.** Each Sub starts with what it reads, opens and reports, then its "
                    "steps in order. Places are written the way Excel writes them: `Sheet!A1`, or `[workbook]Sheet!A1` for "
                    "another workbook; a name in {braces} is a value the macro worked out while running (a file path read "
                    "from a cell, a last row). Selecting and activating are not listed as steps: every step names the "
                    "cells it acts on."),
              ("h", 2, "Summary", "summary"),
              ("steps", [{"text": t} for t in [
                  "**Modules:** " + ", ".join("%s (%d procedures)" % (m["name"], len(m["procs"])) for m in modules),
                  "**Entry points** (not called by another Sub): " + ", ".join(
                      "[[%s|%s]]" % (anchor(s.p["name"]), s.p["name"]) for s in subs if s.p["name"] not in callers),
                  "**Python:** %d of %d statements converted; %d not converted (listed at the end)." % (total - bad, total, bad)]],
               False)]

    def tree(sub, seen):
        item = {"text": "[[%s|%s]]" % (anchor(sub.p["name"]), sub.p["name"]), "children": []}
        if sub.p["name"] not in seen:
            seen.add(sub.p["name"])
            for c in dict.fromkeys(sub.calls):
                item["children"].append(tree(next(x for x in subs if x.p["name"] == c), seen))
        return item
    seen = set()
    blocks += [("h", 2, "What calls what", "calls"),
               ("steps", [tree(s, seen) for s in subs if s.p["name"] not in callers] or [{"text": "—"}], False)]
    opened = list(dict.fromkeys(o for s in subs for o in s.opened))
    status = list(dict.fromkeys(w for s in subs for w, _ in s.status))
    files = []
    if opened:
        files.append({"text": "**Workbooks opened:** " + ", ".join(opened)})
    reads = list(dict.fromkeys("%s (%s)" % (w, v) for s in subs for v, w in s.reads if not w.startswith("[")))
    if reads:
        files.append({"text": "**Cells of this workbook read as settings:** " + "; ".join(reads)})
    if status:
        files.append({"text": "**Status cells written:** " + ", ".join(status)})
    if files:
        blocks += [("h", 2, "Files and settings", "files"), ("steps", files, False)]

    blocks.append(("h", 2, "Procedures", "procs"))
    done = []
    for s in subs:
        p = s.p
        info = report["procs"].get(p["name"], {"statements": 0, "problems": []})
        head = "%s %s(%s)" % (p["kind"].capitalize(), p["name"], ", ".join(q["name"] for q in p["params"]))
        blocks.append(("h", 3, head, anchor(p["name"])))
        meta = ["module %s, lines %d–%d" % (p["module"], p["line"], p["end"])]
        if callers.get(p["name"]):
            meta.append("called by " + ", ".join("[[%s|%s]]" % (anchor(c), c) for c in dict.fromkeys(callers[p["name"]])))
        if info["problems"]:
            meta.append("**%d statement%s not converted to Python**" % (len(info["problems"]), "" if len(info["problems"]) == 1 else "s"))
        blocks.append(("p", "; ".join(meta) + "."))
        twin = next(((d, _similar(d.main + d.handler, s.main + s.handler)) for d in done
                     if len(s.main) > 3 and _similar(d.main + d.handler, s.main + s.handler) is not None), None)
        if twin:
            blocks.append(("p", "Same steps as [[%s|%s]]%s." % (anchor(twin[0].p["name"]), twin[0].p["name"],
                                                                 ", except: " + "; ".join(twin[1]) if twin[1] else "")))
            continue
        done.append(s)
        facts = []
        if s.reads:
            facts.append({"text": "**Reads:** " + "; ".join("%s from %s" % (v, w) for v, w in s.reads)})
        if s.opened:
            facts.append({"text": "**Opens:** " + ", ".join(s.opened)})
        if s.status:
            by_cell = defaultdict(list)
            for w, v in s.status:
                by_cell[w].append(v)
            facts.append({"text": "**Reports in:** " + "; ".join("%s: %s" % (w, ", or ".join(dict.fromkeys(v)))
                                                                for w, v in by_cell.items())})
        if s.settings:
            facts.append({"text": "**Excel settings:** " + ", ".join(dict.fromkeys(s.settings))})
        if facts:
            blocks.append(("steps", facts, False))
        blocks += [("h", 4, "Steps", None), ("steps", s.main or [{"text": "nothing"}], True)]
        if s.handler:
            blocks += [("h", 4, "If any step fails", None), ("steps", s.handler, True)]
    if report["problems"]:
        blocks += [("h", 2, "Not converted to Python", "problems"),
                   ("p", "The generated Python stops with an error at these statements until they are written by hand."),
                   ("steps", [{"text": "%s line %d: `%s` — %s" % (mod, line, src, why)} for mod, line, src, why in report["problems"]],
                    False)]
    return blocks
