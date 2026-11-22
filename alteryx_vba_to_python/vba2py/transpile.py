"""Parsed VBA -> Python source that runs on xl_runtime (an openpyxl-backed stand-in for Excel).

The translation is syntactic: every statement becomes the same statement in Python against objects
that carry Excel's names. A statement that uses anything outside the supported list becomes
`raise xl.NotConverted(...)` with its source text, and is counted. Standard library only.
"""
import keyword
import re

from . import vbaparse as vp

CONSTANTS = {
    "xlUp": -4162, "xlDown": -4121, "xlToLeft": -4159, "xlToRight": -4161, "xlPasteValues": -4163,
    "xlPasteAll": -4104, "xlPasteFormats": -4122, "xlPasteFormulas": -4123, "xlNone": -4142, "xlExpression": 2,
    "xlCellValue": 1, "xlAutomatic": -4105, "xlThemeColorDark1": 1, "xlThemeColorLight1": 2, "xlThemeColorDark2": 3,
    "xlThemeColorLight2": 4, "xlThemeColorAccent1": 5, "xlThemeColorAccent2": 6, "xlThemeColorAccent3": 7,
    "xlThemeColorAccent4": 8, "xlThemeColorAccent5": 9, "xlThemeColorAccent6": 10, "xlBetween": 1, "xlNotBetween": 2,
    "xlEqual": 3, "xlNotEqual": 4, "xlGreater": 5, "xlLess": 6, "xlGreaterEqual": 7, "xlLessEqual": 8,
    "xlCalculationManual": -4135, "xlCalculationAutomatic": -4105, "xlShiftUp": -4162, "xlSolid": 1,
    "vbCrLf": "\r\n", "vbNewLine": "\r\n", "vbLf": "\n", "vbCr": "\r", "vbTab": "\t", "vbNullString": "",
    "vbOKOnly": 0, "vbCritical": 16, "vbExclamation": 48, "vbInformation": 64, "vbYesNo": 4, "vbYes": 6, "vbNo": 7,
    "vbTextCompare": 1, "vbBinaryCompare": 0,
}
XL_FUNCS = ("Len Left Right Mid Trim LTrim RTrim UCase LCase InStr InStrRev Replace CStr CInt CLng CDbl Val Int Fix Abs "
            "Round IIf IsEmpty IsNumeric IsError Chr Asc Space Split Join Array UBound LBound DateValue DateSerial Year "
            "Month Day Format").split()
APP_NAMES = ("Application ThisWorkbook ActiveWorkbook ActiveSheet ActiveCell Selection Workbooks Sheets Worksheets "
             "Range Cells Rows Columns MsgBox Evaluate Dir").split()
OUTSIDE = {"createobject": "starts another program (Outlook, Word, a file-system object, ...): no deterministic equivalent",
           "getobject": "attaches to another program: no deterministic equivalent",
           "inputbox": "asks the person at the keyboard", "shell": "runs another program", "sendkeys": "types into another window",
           "kill": "deletes files", "filecopy": "copies files", "mkdir": "creates a folder"}
APP_CALLS = {"now": "app.Now()", "date": "app.Date()", "err": "app.Err"}
MEMBERS = ("Value Value2 Text Formula FormulaR1C1 ClearContents Clear Delete Copy PasteSpecial Select Activate AutoFit "
           "NumberFormat Row Column Count Worksheet Parent Columns Rows EntireRow EntireColumn FormatConditions Interior "
           "Font Address Cells Offset Resize End Range UsedRange AutoFilterMode Paste Name FullName Path ActiveSheet "
           "Sheets Worksheets Save SaveAs Close Open Add Item SetFirstPriority StopIfTrue Priority Color ThemeColor "
           "TintAndShade PatternColorIndex Pattern ColorIndex Bold Italic Description Number DisplayAlerts ScreenUpdating "
           "EnableEvents Calculation CutCopyMode StatusBar Application ThisWorkbook ActiveWorkbook ActiveCell Selection "
           "Workbooks").split()
_LOW = lambda names: {n.lower(): n for n in names}
L_CONST, L_FUNCS, L_APP, L_MEMBERS = _LOW(CONSTANTS), _LOW(XL_FUNCS), _LOW(APP_NAMES), _LOW(MEMBERS)
RANGE_CALLS = {"range", "cells", "offset", "resize", "end", "item", "columns", "rows"}
RANGE_NAMES = {"selection", "activecell"}
BINOPS = {"=": "xl.eq", "<>": "xl.ne", "<": "xl.lt", "<=": "xl.le", ">": "xl.gt", ">=": "xl.ge", "&": "xl.cat", "+": "xl.add",
          "-": "xl.sub", "*": "xl.mul", "/": "xl.div", "\\": "xl.idiv", "mod": "xl.mod", "^": "xl.pow_", "like": "xl.like"}
ZERO = {"integer": "0", "long": "0", "double": "0", "single": "0", "currency": "0", "byte": "0", "string": '""',
        "boolean": "False"}


class Unsupported(Exception):
    pass


def _walk(stmts):
    for s in stmts:
        yield s
        for key in ("body", "orelse"):
            yield from _walk(s.get(key) or [])
        for _, body in s.get("branches") or []:
            yield from _walk(body)
        for _, body in s.get("cases") or []:
            yield from _walk(body)


class Proc:
    def __init__(self, proc, module, project):
        self.p, self.m, self.project = proc, module, project
        self.names, self.types, self.objects = {}, {}, set()
        for prm in proc["params"]:
            self.declare(prm["name"], prm["type"])
        for s in _walk(proc["body"]):
            if s["k"] == "dim":
                for name, typ in s["names"]:
                    self.declare(name.replace("()", ""), typ)
            elif s["k"] == "assign" and s["target"][0] == "name":
                n = s["target"][1]
                if n.lower() != proc["name"].lower() and n.lower() not in project["globals"]:
                    self.declare(n, None)
                if s["set"]:
                    self.objects.add(n.lower())
            elif s["k"] in ("for", "foreach"):
                self.declare(s["var"], None)
                if s["k"] == "foreach":
                    self.objects.add(s["var"].lower())
        self.withs, self.resume, self.problems, self.count, self.tmp = [], False, [], 0, 0

    def declare(self, name, typ):
        low = name.lower()
        if low not in self.names:
            py = name + "_" if keyword.iskeyword(name) or name in ("app", "xl", "_ret") else name
            self.names[low] = py
        if typ:
            self.types[low] = typ.lower()
            if typ.lower() in ("range", "worksheet", "workbook", "object"):
                self.objects.add(low)

    # ---- expressions
    def range_like(self, n):
        k = n[0]
        if k == "br":
            return True
        if k == "name":
            return n[1].lower() in RANGE_NAMES or self.types.get(n[1].lower()) == "range"
        if k == "call":
            f = n[1]
            return (f[0] == "name" and f[1].lower() in ("range", "cells")) or (f[0] == "mem" and f[2].lower() in RANGE_CALLS)
        if k == "mem":
            return n[2].lower() in ("entirerow", "entirecolumn", "usedrange")
        return False

    def V(self, n):
        """Expression used for its value (a range gives its .Value)."""
        s = self.E(n)
        return "xl.val(%s)" % s if self.range_like(n) else s

    def args(self, args, value=True):
        out = []
        for i, a in enumerate(args or []):
            if a is None:
                if any(x is not None for x in args[i:]):
                    raise Unsupported("an omitted argument in the middle of a call")
                continue
            name, node = a
            s = self.V(node) if value else self.E(node)
            out.append("%s=%s" % (name, s) if name else s)
        return ", ".join(out)

    def E(self, n):
        k = n[0]
        if k == "num":
            t = n[1].rstrip("%&!#@")
            return str(int(t[2:].rstrip("&"), 16)) if t[:2].lower() == "&h" else t
        if k == "str":
            return repr(n[1])
        if k == "bool":
            return "True" if n[1] else "False"
        if k in ("nothing", "empty", "null"):
            return "None"
        if k == "br":
            return "app.Evaluate(%r)" % n[1]
        if k == "paren":
            return "(%s)" % self.V(n[1])
        if k == "with":
            if not self.withs:
                raise Unsupported("a leading dot outside a With block")
            return self.withs[-1]
        if k == "name":
            low = n[1].lower()
            if low in OUTSIDE and low not in self.names:
                raise Unsupported("%s %s" % (n[1], OUTSIDE[low]))
            if low == self.p["name"].lower() and self.p["kind"] == "function":
                return "_ret"
            if low in self.names:
                return self.names[low]
            if low in self.project["globals"]:
                return self.project["globals"][low]
            if low in self.project["procs"]:
                return "%s(app)" % self.project["procs"][low]
            if low in L_CONST:
                self.project["used"].add(L_CONST[low])
                return L_CONST[low]
            if low in APP_CALLS:
                return APP_CALLS[low]
            if low in L_APP:
                return "app." + L_APP[low]
            raise Unsupported("the name %s is not a variable, a Sub, or a supported Excel/VBA name" % n[1])
        if k == "mem":
            low = n[2].lower()
            if low not in L_MEMBERS:
                raise Unsupported("the member .%s is not supported" % n[2])
            if n[1][0] == "name" and n[1][1].lower() == "workbooks" and low == "add":
                raise Unsupported("Workbooks.Add is not supported")
            return "%s.%s" % (self.E(n[1]), L_MEMBERS[low])
        if k == "call":
            f, args = n[1], n[2]
            if f[0] == "name":
                low = f[1].lower()
                if low in self.project["procs"] and low not in self.names:
                    rest = self.args(args)
                    return "%s(app%s)" % (self.project["procs"][low], ", " + rest if rest else "")
                if low in L_FUNCS and low not in self.names:
                    return "xl.%s(%s)" % (L_FUNCS[low], self.args(args))
                if low in self.names and low not in self.objects:
                    return "%s[int(%s)]" % (self.names[low], self.args(args))
            return "%s(%s)" % (self.E(f), self.args(args, value=False))
        if k == "un":
            return "xl.neg(%s)" % self.V(n[2]) if n[1] == "-" else "(not xl.truth(%s))" % self.V(n[2])
        if k == "bin":
            op = n[1]
            if op == "is":
                return "(%s is %s)" % (self.E(n[2]), self.E(n[3]))
            if op in ("and", "or"):
                return "(xl.truth(%s) %s xl.truth(%s))" % (self.V(n[2]), op, self.V(n[3]))
            if op not in BINOPS:
                raise Unsupported("the operator %s is not supported" % op)
            if op == "&":                                # flatten a & b & c
                parts, node = [], n
                while node[0] == "bin" and node[1] == "&":
                    parts.insert(0, self.V(node[3]))
                    node = node[2]
                return "xl.cat(%s)" % ", ".join([self.V(node)] + parts)
            return "%s(%s, %s)" % (BINOPS[op], self.V(n[2]), self.V(n[3]))
        raise Unsupported("%s literal is not supported" % k)

    def cond(self, n):
        s = self.V(n)
        return s if n[0] == "bin" and n[1] in ("=", "<>", "<", "<=", ">", ">=", "and", "or", "is", "like") or \
            (n[0] == "un" and n[1] == "not") else "xl.truth(%s)" % s

    # ---- statements
    def simple(self, s):
        k = s["k"]
        if s.get("bad"):
            raise Unsupported("could not read: " + s["bad"])
        if k == "assign":
            t, v = s["target"], s["value"]
            if s["set"]:
                return ["%s = %s" % (self.E(t), self.E(v))]
            if t[0] == "name" and (t[1].lower() in self.names or t[1].lower() in self.project["globals"]
                                   or t[1].lower() == self.p["name"].lower()) and t[1].lower() not in self.objects:
                return ["%s = %s" % (self.E(t), self.V(v))]
            if self.range_like(t) or (t[0] == "name" and t[1].lower() in self.objects):
                return ["%s.Value = %s" % (self.E(t), self.V(v))]
            if t[0] == "mem":
                return ["%s = %s" % (self.E(t), self.V(v))]
            if t[0] == "call" and t[1][0] == "name" and t[1][1].lower() in self.names:
                return ["%s = %s" % (self.E(t), self.V(v))]
            raise Unsupported("assignment target not understood")
        if k == "call":
            t, args = s["target"], s["args"]
            if args is None and t[0] == "call":
                return [self.E(t)]
            if t[0] == "name":
                low = t[1].lower()
                if low in self.project["procs"]:
                    rest = self.args(args)
                    return ["%s(app%s)" % (self.project["procs"][low], ", " + rest if rest else "")]
                if low in L_APP:
                    return ["app.%s(%s)" % (L_APP[low], self.args(args))]
                if low == "doevents":
                    return ["pass"]
                raise Unsupported("call of %s, which is not a Sub in this project or a supported statement" % t[1])
            return ["%s(%s)" % (self.E(t), self.args(args, value=False))]
        if k == "exit":
            if s["what"] in ("sub", "function"):
                return ["return _ret" if self.p["kind"] == "function" else "return"]
            return ["break"]
        if k == "dim":
            return []
        if k == "const":
            self.declare(s["name"], None)
            return ["%s = %s" % (self.names[s["name"].lower()], self.V(s["value"]))]
        if k == "label":
            return ["# label %s:" % s["name"]]
        if k == "comment":
            return ["# " + s["text"]]
        if k == "onerror":
            if s["mode"] == "resume":
                self.resume = True
                return ["# On Error Resume Next: each following statement is skipped if it fails"]
            if s["mode"] == "off":
                self.resume = False
                return ["# On Error GoTo 0"]
            raise Unsupported("On Error GoTo %s is only supported once, at the top level of a Sub, with the label at "
                              "the top level after an Exit Sub" % s["label"])
        if k == "goto":
            raise Unsupported("GoTo is not supported")
        raise Unsupported(s.get("why") or "statement not understood")

    def block(self, stmts, ind):
        out = []
        for s in stmts:
            pad = "    " * ind
            self.count += s["k"] not in ("comment", "dim", "label")
            try:
                lines = self.compound(s, ind)
                if lines is None:
                    lines = self.simple(s)
                    if self.resume and s["k"] in ("assign", "call"):
                        lines = ["with app.ResumeNext():"] + ["    " + x for x in lines]
                    lines = [pad + x for x in lines]
                    if lines and s["k"] in ("assign", "call"):
                        lines[0] = "%-78s # L%d" % (lines[0], s["line"]) if len(lines[0]) < 78 else lines[0] + "  # L%d" % s["line"]
            except Unsupported as e:
                self.problems.append((s["line"], s["src"], str(e)))
                lines = [pad + "raise xl.NotConverted(%r)" % ("%s line %d: %s  [%s]" % (self.m["name"], s["line"], s["src"], e))]
            out += lines
        return out or ["    " * ind + "pass"]

    def compound(self, s, ind):
        k, pad = s["k"], "    " * ind
        if k not in ("if", "with", "for", "foreach", "do", "select"):
            return None
        if s.get("bad"):
            raise Unsupported("could not read: " + s["bad"])
        if k == "if":
            out = []
            for n, (c, body) in enumerate(s["branches"]):
                out.append("%s%s %s:" % (pad, "if" if n == 0 else "elif", self.cond(c)))
                out += self.block(body, ind + 1)
            if s["orelse"]:
                out += [pad + "else:"] + self.block(s["orelse"], ind + 1)
            return out
        if k == "with":
            self.tmp += 1
            var = "_w%d" % self.tmp
            head = "%s%s = %s" % (pad, var, self.E(s["target"]))
            self.withs.append(var)
            try:
                return [head] + self.block(s["body"], ind)
            finally:
                self.withs.pop()
        if k == "for":
            step = ", " + self.V(s["step"]) if s["step"] else ""
            head = "%sfor %s in xl.vba_range(%s, %s%s):" % (pad, self.names[s["var"].lower()], self.V(s["start"]), self.V(s["stop"]), step)
            return [head] + self.block(s["body"], ind + 1)
        if k == "foreach":
            return ["%sfor %s in xl.each(%s):" % (pad, self.names[s["var"].lower()], self.E(s["coll"]))] + self.block(s["body"], ind + 1)
        if k == "do":
            test = lambda kc: self.cond(kc[1]) if kc[0] == "while" else "not %s" % self.cond(kc[1])
            head = "%swhile %s:" % (pad, test(s["pre"]) if s["pre"] else "True")
            body = self.block(s["body"], ind + 1)
            if s["post"]:
                body += ["%s    if not (%s):" % (pad, test(s["post"])), pad + "        break"]
            return [head] + body
        if k == "select":
            self.tmp += 1
            var = "_s%d" % self.tmp
            out, first = ["%s%s = %s" % (pad, var, self.V(s["subject"]))], True
            for tests, body in s["cases"]:
                if tests is None:
                    out.append(pad + ("else:" if not first else "if True:"))
                else:
                    parts = []
                    for t in tests:
                        if t[0] == "eq":
                            parts.append("xl.eq(%s, %s)" % (var, self.V(t[1])))
                        elif t[0] == "to":
                            parts.append("(xl.ge(%s, %s) and xl.le(%s, %s))" % (var, self.V(t[1]), var, self.V(t[2])))
                        else:
                            parts.append("%s(%s, %s)" % (BINOPS[t[1]], var, self.V(t[2])))
                    out.append("%s%s %s:" % (pad, "if" if first else "elif", " or ".join(parts)))
                out += self.block(body, ind + 1)
                first = False
            return out

    def emit(self):
        p = self.p
        params = "".join(", %s%s" % (self.names[q["name"].lower()], "=None" if q["default"] is not None else "") for q in p["params"])
        out = ["def %s(app%s):" % (self.project["procs"][p["name"].lower()], params),
               '    """%s %s   (%s, lines %d-%d)"""' % (p["kind"].capitalize(), p["name"], self.m["name"], p["line"], p["end"])]
        assigned = {s["target"][1].lower() for s in _walk(p["body"]) if s["k"] == "assign" and s["target"][0] == "name"}
        shared = sorted(self.project["globals"][g] for g in assigned if g in self.project["globals"] and g not in self.names)
        if shared:
            out.append("    global " + ", ".join(shared))
        if self.m["compare_text"] or self.project["any_compare_text"]:
            out.append("    xl.COMPARE_TEXT[0] = %s" % self.m["compare_text"])
        inits = [(py, ZERO.get(self.types.get(low, ""), "None")) for low, py in self.names.items()
                 if low not in {q["name"].lower() for q in p["params"]}]
        for value in sorted({v for _, v in inits}):
            names = [py for py, v in inits if v == value]
            out.append("    %s = %s" % (" = ".join(names), value))
        if p["kind"] == "function":
            out.append("    _ret = None")
        body = p["body"]
        on = next((i for i, s in enumerate(body) if s["k"] == "onerror" and s["mode"] == "goto"), None)
        label = next((i for i, s in enumerate(body) if on is not None and s["k"] == "label"
                      and s["name"].lower() == (body[on]["label"] or "").lower()), None)
        if on is not None and label is not None and label > on:
            pre = self.block(body[:on], 1)
            out += [] if pre == ["    pass"] else pre
            out.append("    try:                                                     # On Error GoTo %s" % body[on]["label"])
            self.count += 1
            out += self.block(body[on + 1:label], 2)
            out += ["    except xl.NotConverted:", "        raise", "    except Exception as _err:",
                    "        app.Err = xl.ErrObject(_err)"]
            out += self.block(body[label + 1:], 2)
        else:
            out += self.block(body, 1)
        if p["kind"] == "function":
            out.append("    return _ret")
        return out


def generate(modules, source=""):
    """modules: parsed modules. -> (python lines, report dict)"""
    project = {"procs": {}, "globals": {}, "used": set(), "any_compare_text": any(m["compare_text"] for m in modules)}
    for m in modules:
        for p in m["procs"]:
            name = p["name"] + ("_" if keyword.iskeyword(p["name"]) else "")
            project["procs"].setdefault(p["name"].lower(), name)
        for d in m["decls"]:
            for name in ([n.replace("()", "") for n, _ in d["names"]] if d["k"] == "dim" else [d["name"]] if d["k"] == "const" else []):
                project["globals"][name.lower()] = name
    body, report = [], {"procs": {}, "problems": [], "statements": 0}
    for m in modules:
        for d in m["decls"]:
            if d["k"] == "const":
                dummy = Proc({"name": "", "kind": "sub", "params": [], "body": []}, m, project)
                try:
                    body.append("%s = %s" % (d["name"], dummy.V(d["value"])))
                except Unsupported as e:
                    body.append("%s = None  # NOT CONVERTED: %s" % (d["name"], e))
            elif d["k"] == "dim":
                body += ["%s = None" % n.replace("()", "") for n, _ in d["names"]]
            elif d["k"] == "raw":
                report["problems"].append((m["name"], d["line"], d["src"], d.get("why", "module-level statement not supported")))
        for p in m["procs"]:
            tr = Proc(p, m, project)
            lines = tr.emit()
            body += ["", ""] + lines
            report["procs"][p["name"]] = {"statements": tr.count, "problems": tr.problems, "module": m["name"]}
            report["statements"] += tr.count
            report["problems"] += [(m["name"],) + pr for pr in tr.problems]
    head = ['"""Generated by vba2py%s.' % (" from " + source if source else ""), "",
            "Each Sub is a function taking `app`, a stand-in for Excel (see xl_runtime.py):",
            "    import xl_runtime as xl",
            "    app = xl.App('path/to/the macro workbook.xlsm', save=False)     # save=False: no file is written",
            "    FormatAll(app); print(app.log)",
            "A statement that could not be converted raises xl.NotConverted when it is reached.",
            '"""', "import xl_runtime as xl", ""]
    head += ["%s = %r" % (c, CONSTANTS[c]) for c in sorted(project["used"])]
    tail = ["", "", 'if __name__ == "__main__":', "    import sys",
            "    _app = xl.App(sys.argv[1], save='--save' in sys.argv)",
            "    globals()[sys.argv[2]](_app)", "    print('\\n'.join(_app.log))", ""]
    return head + body + tail, report
