"""Migration guide: a workflow explained for an engineer who has never opened Designer.

Top level: the containers, what each reads and writes, and which containers feed which (by a
connection or through a file one writes and another reads). Inside a container: its branches, as
Designer draws them, each labelled by its first tool's annotation or its filter test. Inside a
branch: one card per tool with fixed fields (In, Does, Rule, Reads, Writes, Out, Python). Per
container: a table of the columns it creates or changes and where each is used later. Repeated
sub-chains are described once. Deterministic; standard library only.
"""
import fnmatch
import os
import re
from collections import Counter, defaultdict

from . import codegen, expr, sketch
from .logic import BLOCKERS, CLEANSE_WORDS, CODE_PAGES, STRING, WIDTH, Logic, _file, _names
from .workflow import STATUS, _id_key

ANCHOR = {"True": "True", "False": "False", "Join": "J: matched", "Left": "L: left only", "Right": "R: right only",
          "Unique": "U: first of each", "Duplicates": "D: duplicates"}
MULTI = ("Filter", "Join", "Unique") + BLOCKERS
SOURCES = ("Input", "TextInput", "Directory")
AGG = {"Sum": '"sum"', "Count": '"size"', "CountNonNull": '"count"', "CountDistinct": '"nunique"',
       "CountDistinctNonNull": '"nunique"', "Min": '"min"', "Max": '"max"', "Avg": '"mean"', "Median": '"median"',
       "First": "lambda s: s.iloc[0]", "Last": "lambda s: s.iloc[-1]", "CountNull": "lambda s: s.isna().sum()"}
AGG_WORDS = {"Count": "number of rows", "CountNonNull": "number of non-null [%s]", "CountNull": "number of null [%s]",
             "CountDistinct": "number of different [%s]", "CountDistinctNonNull": "number of different non-null [%s]",
             "Sum": "sum of [%s]", "Avg": "average of [%s]", "Median": "median of [%s]", "Min": "smallest [%s]",
             "Max": "largest [%s]", "First": "[%s] of the first row", "Last": "[%s] of the last row"}
DT_TOKENS = [("yyyy", "%Y"), ("yy", "%y"), ("Month", "%B"), ("MMMM", "%B"), ("Mon", "%b"), ("MMM", "%b"), ("MM", "%m"),
             ("M", "%m"), ("day", "%A"), ("dy", "%a"), ("dd", "%d"), ("d", "%d"), ("HH", "%H"), ("H", "%H"),
             ("hh", "%H"), ("mm", "%M"), ("ss", "%S")]
HELPERS = [
    "import glob, subprocess",
    "import numpy as np",
    "import pandas as pd",
    "",
    "def empty(s):",
    '    """Alteryx IsEmpty: null or the empty string."""',
    '    return s.isna() | (s == "")',
    "",
    "def eq(a, b):",
    '    """Alteryx "=": text is compared ignoring case; null equals null."""',
    "    fold = lambda x: x.str.casefold() if isinstance(x, pd.Series) and x.dtype == object else (",
    "        x.casefold() if isinstance(x, str) else x)",
    "    null = lambda x: x.isna() if isinstance(x, pd.Series) else x is None",
    "    return (fold(a) == fold(b)) | (null(a) & null(b))",
]


def _norm(path):
    p = path.replace("\\", "/").strip().lower()
    return p[2:] if p.startswith("./") else p


def _same_file(a, b):
    a, b = _norm(a), _norm(b)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _strftime(fmt):
    out, i = [], 0
    while i < len(fmt):
        tok = next(((t, s) for t, s in DT_TOKENS if fmt.startswith(t, i)), None)
        if tok:
            out.append(tok[1])
            i += len(tok[0])
        else:
            out.append(fmt[i])
            i += 1
    return "".join(out)


def _q(name):
    return '"%s"' % name.replace('"', '\\"')


def _cols(names):
    return "[%s]" % ", ".join(_q(n) for n in names)


class Guide:
    def __init__(self, wf):
        self.wf, self.t = wf, wf.tools
        self.L = Logic(wf)
        self.fn, _ = codegen.function_names(wf)
        self.rank = wf.rank
        top = lambda i: self.t[i].path[0] if self.t[i].path else ""
        self.top_of = {i: top(i) for i in wf.live}
        live_tops = sorted({top(i) for i in wf.live}, key=lambda k: min(self.rank[i] for i in wf.live if top(i) == k))
        off = [p[0] for p, dis in wf.containers if len(p) == 1 and dis and p[0] not in live_tops]
        self.tops = live_tops + list(dict.fromkeys(off))
        self.cnum = {k: n for n, k in enumerate(self.tops, 1)}
        self.number()
        self.cards = {i: self.card(i) for i in self.order}
        kinds = sorted({self.t[i].kind for i in self.order if self.t[i].status == "caveat" and STATUS.get(self.t[i].kind, ("", ""))[1]})
        self.assume = {k: n for n, k in enumerate(kinds, 1)}

    # ---- numbering: containers k, branches k.m, steps k.n, in reading order
    def number(self):
        L = self.L
        self.sid, self.bid, self.order, self.sections = {}, {}, [], {}
        done = set()
        for key in self.tops:
            k = self.cnum[key]
            stages = [s for s in range(len(L.stages)) if self.top_of[L.stages[s][0]] == key]
            path_of = lambda s: tuple(self.t[L.stages[s][0]].path)
            n = m = 0
            for ss in [stages]:
                for s in ss:
                    if s in done:
                        continue
                    if s in L.copy_of:
                        b, c = L.copy_of[s]
                        group = [x for x in ss if L.copy_of.get(x) == (b, c) and path_of(x) == path_of(s)]
                        twin = dict(zip(L.blocks[b - 1][c], L.blocks[b - 1][0]))
                        place = lambda i: (L.stage_of[twin[i]], L.stages[L.stage_of[twin[i]]].index(twin[i]))
                        for x in sorted(group, key=lambda x: place(L.stages[x][0])):
                            m += 1
                            self.bid[x] = "%d.%d" % (k, m)
                        for i in sorted((i for x in group for i in L.stages[x]), key=place):
                            n += 1
                            self.sid[i] = "%d.%d" % (k, n)
                            self.order.append(i)
                        done.update(group)
                        continue
                    m += 1
                    self.bid[s] = "%d.%d" % (k, m)
                    for i in L.stages[s]:
                        n += 1
                        self.sid[i] = "%d.%d" % (k, n)
                        self.order.append(i)
                    done.add(s)
            runs = []                                      # consecutive branches in the same nested container
            for s in sorted(stages, key=lambda s: _bkey(self.bid[s])):
                if runs and runs[-1][0] == path_of(s):
                    runs[-1][1].append(s)
                else:
                    runs.append((path_of(s), [s]))
            self.sections[key] = runs
        self.pos = {i: n for n, i in enumerate(self.order)}

    # ---- references
    def ref(self, i):
        return "[[s%s|%s]]" % (self.sid[i].replace(".", "-"), self.sid[i])

    def bref(self, s):
        return "[[b%s|branch %s]]" % (self.bid[s].replace(".", "-"), self.bid[s])

    def src(self, e):
        text = self.ref(e[0])
        kind = self.t[e[0]].kind
        if kind in ("Filter", "Join", "Unique"):
            text += " (%s)" % ANCHOR.get(e[1], e[1])
        elif kind in BLOCKERS:
            text += " (%s)" % re.sub(r"(\D+)(\d+)", r"\1 \2", e[1]).lower()
        return text

    def var(self, i, anchor=None):
        v = "s" + self.sid[i].replace(".", "_")
        if anchor and self.t[i].kind in MULTI:
            v += "_" + anchor.lower()
        return v

    def ins_text(self, i):
        ins, kind = self.wf.ins.get(i, []), self.t[i].kind
        if not ins:
            return "—" if kind in SOURCES else "nothing connected"
        if kind in ("Join", "FindReplace", "AppendFields") or kind in BLOCKERS:
            return "; ".join("%s ← %s" % (e[3], self.src(e)) for e in ins)
        return ", ".join(self.src(e) for e in ins)

    def outs(self, i):
        groups = defaultdict(list)
        for e in self.wf.outs.get(i, []):
            groups[e[1]].append(e[2])
        return groups

    def outs_text(self, i):
        t, groups = self.t[i], self.outs(i)
        if t.kind in ("Filter", "Join", "Unique"):
            names = {"Filter": ("True", "False"), "Join": ("Join", "Left", "Right"), "Unique": ("Unique", "Duplicates")}[t.kind]
            parts = []
            for a in names:
                dst = groups.get(a)
                parts.append("%s → %s" % (ANCHOR[a], ", ".join(self.ref(d) for d in dst)) if dst else
                             "%s: not used (these rows are dropped)" % ANCHOR[a])
            return "; ".join(parts)
        if len(groups) > 1:
            return "; ".join("%s → %s" % (re.sub(r"(\D+)(\d+)", r"\1 \2", a).lower(), ", ".join(self.ref(d) for d in v))
                             for a, v in sorted(groups.items()))
        dst = [d for v in groups.values() for d in v]
        if dst:
            return ", ".join(self.ref(d) for d in dst)
        return "—" if t.kind in ("Output", "RunCommand", "Message") else "not used (the stream ends here)"

    def input_columns(self, i):
        """Column names arriving at the tool's main input, when the workflow recorded them."""
        e = self.L.primary(i) or (self.wf.ins.get(i) or [None])[0]
        if e is None:
            return None
        meta = self.t[e[0]].meta
        cols = meta.get(e[1]) or (meta.get("Output") if len(meta) == 1 else None)
        return {n.lower() for n, _, _ in cols} if cols else None

    def out_type(self, i, name):
        for fields in self.t[i].meta.values():
            for n, typ, size in fields:
                if n == name:
                    return typ + ("(%s)" % size if size and typ in STRING else "")
        return None

    # ---- one card
    def card(self, i):
        t, ir, k = self.t[i], self.t[i].ir, self.t[i].kind
        c = {"does": "", "rule": [], "reads": [], "writes": [], "py": [], "notes": []}
        used = set(self.outs(i))
        ins = self.wf.ins.get(i, [])
        other = lambda letter: [e for e in ins if e[3][:1].upper() == letter]
        fx = "x" + self.fn[i][1:]

        def write(name, typ=None, how=""):
            c["writes"].append((name, typ or self.out_type(i, name), how))

        def rule_lines(label, e):
            if e["ast"] is None:
                return ["%s = %s   (could not be read)" % (label, e["text"])]
            lines = expr.decision(e["ast"], WIDTH)
            if len(lines) == 1 and len(label) + len(lines[0]) < WIDTH:
                return ["%s = %s" % (label, lines[0])]
            return ["%s =" % label] + ["    " + x for x in lines]

        def vec(e, fallback, col=None):
            try:
                if e["ast"] is None:
                    raise sketch.NoSketch("unreadable")
                return sketch.vec(e["ast"], col)
            except sketch.NoSketch:
                return fallback

        if k == "Input":
            path, sheet = _file(ir["file"])
            opts = ir["options"]
            c["does"] = "Reads %s`%s`." % ("sheet **%s** of " % sheet if sheet else "", path)
            words = []
            if opts.get("CodePage") in CODE_PAGES:
                words.append(CODE_PAGES[opts["CodePage"]])
            if opts.get("HeaderRow") == "False":
                words.append("no header row")
            if opts.get("FieldLen"):
                words.append("text cut at %s characters" % opts["FieldLen"])
            if words:
                c["notes"].append("Read as: " + ", ".join(words) + ".")
            for name, typ in ir.get("types", {}).items():
                write(name, typ, "read from the file")
            if path.lower().endswith((".csv", ".txt", ".tsv", ".dat")):
                enc = {"28591": ', encoding="latin-1"', "65001": ', encoding="utf-8-sig"', "1252": ', encoding="cp1252"'}.get(
                    opts.get("CodePage", ""), "")
                sep = opts.get("Delimeter", opts.get("Delimiter", ","))
                c["py"] = ['df = pd.read_csv(r"%s", sep=%r%s, dtype=str, keep_default_na=False).replace("", None)' % (
                    path, {"\\t": "\t"}.get(sep, sep or ","), enc)]
            else:
                c["py"] = ['df = pd.read_excel(r"%s"%s)' % (path, ', sheet_name="%s"' % sheet if sheet else "")]
        elif k == "DynamicInput":
            tpl = _file(ir["file"])[0]
            c["does"] = "Reads every file named in [%s] (each like `%s`) and stacks their rows." % (ir["field"], tpl)
            c["reads"] = [ir["field"]]
            reader = "pd.read_excel(p)" if tpl.lower().endswith((".xlsx", ".xlsm")) else "pd.read_csv(p, dtype=str)"
            c["py"] = ['df = pd.concat([%s for p in df["%s"]], ignore_index=True)' % (reader, ir["field"])]
        elif k == "Directory":
            c["does"] = "Lists the files in `%s` matching `%s`: one row per file." % (ir["dir"], ir["spec"])
            for name in ("FullPath", "FileName", "CreationTime", "LastWriteTime"):
                write(name, None, "file details")
            c["py"] = ['df = pd.DataFrame({"FullPath": sorted(glob.glob(r"%s\\%s"))})  # Alteryx also gives FileName, '
                       'CreationTime, LastWriteTime, Size' % (ir["dir"], ir["spec"])]
        elif k == "TextInput":
            rows = [["" if x is None else x for x in r] for r in ir["rows"]]
            widths = [max([len(col)] + [len(r[j]) if j < len(r) else 0 for r in rows]) for j, col in enumerate(ir["columns"])]
            line = lambda cells: "  ".join(x.ljust(w) for x, w in zip(cells, widths)).rstrip()
            c["does"] = "Starts from a table typed into the workflow (%d rows)." % len(rows)
            c["rule"] = [line(ir["columns"]), line(["-" * w for w in widths])] + [line(r + [""] * (len(widths) - len(r))) for r in rows]
            for name in ir["columns"]:
                write(name, ir.get("types", {}).get(name), "typed in the workflow")
            c["py"] = ["df = pd.DataFrame(%r, columns=%s)" % (rows, _cols(ir["columns"]))] if len(rows) <= 5 else \
                ["df = pd.DataFrame(rows, columns=%s)  # rows: the table above" % _cols(ir["columns"])]
        elif k == "Select":
            real = [f for f in ir["fields"] if f[0] != "*Unknown"]
            ren = {f: r for f, sel, r, _, _ in real if sel and r and r != f}
            drop = [f for f, sel, *_ in real if not sel]
            retype = {(r or f): (typ, size) for f, sel, r, typ, size in real if sel and typ}
            keep = [r or f for f, sel, r, _, _ in real if sel]
            parts = (["renames %d" % len(ren)] if ren else []) + (["drops %d" % len(drop)] if drop and ir["unknown"] else []) + \
                (["changes the type of %d" % len(retype)] if retype else []) + (["keeps only %d, in order" % len(keep)] if not ir["unknown"] else [])
            c["does"] = "Chooses columns: %s." % (", ".join(parts) or "no change")
            c["rule"] = self.L.select_words(ir["fields"], ir["unknown"])
            c["reads"] = list(ren) + [f for f in retype if f not in ren.values()]
            for old, new in ren.items():
                write(new, retype.get(new, (None,))[0], "renamed from [%s]" % old)
            for name, (typ, size) in retype.items():
                if name not in ren.values():
                    write(name, typ + ("(%s)" % size if size and typ in STRING else ""), "type changed")
            if ren:
                c["py"].append("df = df.rename(columns={%s})" % ", ".join("%s: %s" % (_q(a), _q(b)) for a, b in ren.items()))
            if drop and ir["unknown"]:
                c["py"].append("df = df.drop(columns=%s)" % _cols(drop))
            if not ir["unknown"]:
                c["py"].append("df = df[%s]" % _cols(keep))
            if retype:
                pyt = lambda t: "float" if t in ("Double", "Float", "FixedDecimal") else "Int64" if t.startswith(("Int", "Byte")) \
                    else "str" if t in STRING else repr(t)
                c["py"].append("df = df.astype({%s})" % ", ".join("%s: %s" % (_q(n), pyt(tp)) for n, (tp, _) in retype.items()))
        elif k == "Formula":
            have = self.input_columns(i)
            names = [f[0] for f in ir["fields"]]
            c["does"] = "Calculates %s." % ", ".join("**%s**" % n for n in names)
            for n, (name, typ, _, e) in enumerate(ir["fields"], 1):
                c["rule"] += rule_lines(name, e)
                c["reads"] += expr.fields(e["ast"]) if e["ast"] is not None else []
                how = expr.readable(e["ast"]) if e["ast"] is not None else e["text"]
                mode = "" if have is None else (" (changed)" if name.lower() in have else " (new)")
                write(name, typ, how)
                c["writes"][-1] = c["writes"][-1][:2] + (how, mode)
                c["py"].append('df["%s"] = %s' % (name, vec(e, "df.apply(%s_%d, axis=1)  # generated code; no one-line form" % (fx, n))))
        elif k == "MultiFieldFormula":
            cols = list(ir["fields"])
            c["does"] = "Applies one rule to each of the columns %s%s." % (
                _names(cols), " and every other %s column" % ir["group"].lower() if ir["unknown"] else "")
            c["rule"] = rule_lines("[this column]", ir["expr"])
            c["reads"] = cols + [f for f in (expr.fields(ir["expr"]["ast"]) if ir["expr"]["ast"] is not None else [])
                                 if f.lower() != "_currentfield_"]
            how = "this rule applied to the column"
            for col in cols:
                target = ((ir["addon"] + col) if ir["prefix"] else (col + ir["addon"])) if ir["copy"] else col
                write(target, ir["out_type"], how)
            v = vec(ir["expr"], None, "df[c]")
            c["py"] = ["for c in %s:" % _cols(cols), "    df[%s] = %s" % ('"%s" + c' % ir["addon"] if ir["copy"] and ir["prefix"]
                                                                       else "c + %r" % ir["addon"] if ir["copy"] else "c",
                                                                       v or "df.apply(lambda r: %s(r, r[c], c, None), axis=1)" % fx)]
        elif k == "Filter":
            if ir["mode"] == "simple":
                lines, cond_py = ["[%s] %s %s" % (ir["field"], ir["operator"], ir["operand"])], None
                c["reads"] = [ir["field"]]
            else:
                e = ir["expr"]
                lines = expr.decision(e["ast"], WIDTH) if e["ast"] is not None else [e["text"]]
                cond_py = vec(e, None)
                c["reads"] = expr.fields(e["ast"]) if e["ast"] is not None else []
            cond_py = cond_py or "df.apply(%s, axis=1)  # generated code" % (fx if ir["mode"] != "simple" else "test")
            c["rule"] = lines if len(lines) > 1 or len(lines[0]) > 60 else []
            test = "the test in Rule holds" if c["rule"] else lines[0]
            if used >= {"True", "False"}:
                c["does"] = "Splits the rows: those where %s go to True, the others to False." % test
                c["py"] = ["m = " + cond_py, "%s, %s = df[m], df[~m]" % (self.var(i, "True"), self.var(i, "False"))]
            elif used == {"False"}:
                c["does"] = "Keeps only the rows where %s is not true (the False output)." % test.replace(" holds", "")
                c["py"] = ["df = df[~(%s)]" % cond_py]
            else:
                c["does"] = "Keeps only the rows where %s." % test
                c["py"] = ["df = df[%s]" % cond_py]
        elif k == "Join":
            right = other("R")
            rvar = self.var(right[0][0], right[0][1]) if right else "right"
            keys = ", ".join("[%s] = [%s]" % p for p in zip(ir["left"], ir["right"]))
            c["does"] = "Matches each left row with the right rows that have the same %s." % (
                "key" if len(ir["left"]) == 1 else "keys")
            c["rule"] = ["keys: left %s" % keys.replace(" = ", " = right ")] + self.L.select_words(ir["fields"], ir["unknown"], join=True)
            c["reads"] = list(dict.fromkeys(ir["left"] + ir["right"]))
            for f, sel, r, typ, size in ir["fields"]:
                if f != "*Unknown" and sel and r and r != f:
                    write(r, typ, "renamed from %s" % f.replace("Right_", "the right side's [", 1) + ("]" if f.startswith("Right_") else ""))
            ren = {f: r for f, sel, r, _, _ in ir["fields"] if f != "*Unknown" and sel and r and r != f and f.startswith("Right_")}
            c["py"] = ["m = df.merge(%s.add_prefix(\"Right_\"), how=\"outer\", left_on=%s, right_on=%s, indicator=True)%s" % (
                rvar, _cols(ir["left"]), _cols(["Right_" + x for x in ir["right"]]),
                ".rename(columns={%s})" % ", ".join("%s: %s" % (_q(a), _q(b)) for a, b in ren.items()) if ren else "")]
            for a, flag in (("Join", "both"), ("Left", "left_only"), ("Right", "right_only")):
                if a in used:
                    c["py"].append('%s = m[m["_merge"] == "%s"].drop(columns="_merge")%s' % (
                        self.var(i, a), flag, "  # keep the left columns" if a == "Left" else
                        "  # keep the right columns" if a == "Right" else ""))
            c["notes"].append("In Alteryx only right-side columns whose names clash get the Right_ prefix.")
        elif k == "AppendFields":
            src = other("S")
            svar = self.var(src[0][0], src[0][1]) if src else "source"
            c["does"] = "Adds the columns of the Source input to every row (every row × every Source row)."
            c["rule"] = self.L.select_words(ir["fields"], ir["unknown"], join=True)
            for f, sel, r, typ, size in ir["fields"]:
                if f != "*Unknown" and sel and r and r != f:
                    write(r, typ, "renamed from %s" % f)
            c["py"] = ["df = df.merge(%s, how=\"cross\")" % svar]
        elif k == "Union":
            srcs = [self.var(e[0], e[1]) for e in ins]
            c["does"] = "Stacks the rows of its %d inputs, matching columns %s." % (
                len(ins), "by name" if ir["mode"] == "ByName" else "by position")
            c["py"] = ["df = pd.concat([%s], ignore_index=True)" % ", ".join(srcs)]
        elif k == "Summarize":
            group = [(f, r) for f, a, r, _ in ir["fields"] if a == "GroupBy"]
            aggs = [(f, a, r or "%s_%s" % (a, f), o) for f, a, r, o in ir["fields"] if a != "GroupBy"]
            c["does"] = ("Groups the rows by %s and calculates, for each group, %s." % (_names(f for f, _ in group),
                         ", ".join("**%s**" % r for _, _, r, _ in aggs)) if group else
                         "Calculates over all rows: %s." % ", ".join("**%s**" % r for _, _, r, _ in aggs))
            c["reads"] = list(dict.fromkeys([f for f, _ in group] + [f for f, _, _, _ in aggs]))
            for f, a, r, o in aggs:
                how = 'all [%s] joined with "%s"' % (f, o.get("Separator", "")) if a == "Concat" else \
                    AGG_WORDS.get(a, a + " of [%s]") % ((f,) if "%s" in AGG_WORDS.get(a, "%s") else ())
                c["rule"].append("%s = %s" % (r, how))
                write(r, None, how)
            for f, r in group:
                if r and r != f:
                    write(r, None, "renamed from [%s]" % f)
            spec = ", ".join("%s: (%s, %s)" % (_q(r), _q(f), 'lambda s: %r.join(s.fillna("").astype(str))' % o.get("Separator", "")
                                               if a == "Concat" else AGG.get(a, repr(a))) for f, a, r, o in aggs)
            ren = {f: r for f, r in group if r and r != f}
            if group:
                c["py"] = ["df = df.groupby(%s, sort=True, dropna=False).agg(**{%s}).reset_index()%s" % (
                    _cols([f for f, _ in group]), spec,
                    ".rename(columns={%s})" % ", ".join("%s: %s" % (_q(a), _q(b)) for a, b in ren.items()) if ren else "")]
            else:
                c["py"] = ["df = df.agg(**{%s}).T.reset_index(drop=True)  # one row of totals" % spec]
        elif k == "Sort":
            c["does"] = "Sorts the rows by " + ", ".join("[%s] %s" % (f, "ascending" if a else "descending") for f, a in ir["fields"]) + "."
            c["reads"] = [f for f, _ in ir["fields"]]
            c["py"] = ['df = df.sort_values(%s, ascending=%s, kind="stable", na_position="first")' % (
                _cols([f for f, _ in ir["fields"]]), [a for _, a in ir["fields"]])]
        elif k == "Unique":
            c["does"] = "Keeps the first row for each %s; later rows with the same values are the duplicates." % _names(ir["fields"])
            c["reads"] = list(ir["fields"])
            c["py"] = ["%s = df.drop_duplicates(%s)" % (self.var(i, "Unique"), _cols(ir["fields"])),
                       "%s = df[df.duplicated(%s)]" % (self.var(i, "Duplicates"), _cols(ir["fields"]))]
            c["py"] = [p for p, a in zip(c["py"], ("Unique", "Duplicates")) if a in used] or c["py"][:1]
        elif k == "Sample":
            n, g = ir["n"], ir["group"]
            c["does"] = {"First": "Keeps the first %s rows", "Last": "Keeps the last %s rows", "Skip": "Skips the first %s rows",
                         "Sample": "Keeps 1 row in every %s"}.get(ir["mode"], ir["mode"] + " %s") % n + \
                (" of each group of %s." % _names(g) if g else ".")
            c["reads"] = list(g)
            base = "df.groupby(%s, sort=False)" % _cols(g) if g else "df"
            c["py"] = [{"First": "df = %s.head(%s)" % (base, n), "Last": "df = %s.tail(%s)" % (base, n),
                        "Skip": "df = df[df.groupby(%s).cumcount() >= %s]" % (_cols(g), n) if g else "df = df.iloc[%s:]" % n,
                        "Sample": "df = df.iloc[%d::%s]" % (int(n) - 1, n)}.get(ir["mode"], "# see the generated code")]
        elif k == "RecordID":
            text = ir["type"] in STRING + ("String",) and ir["size"]
            c["does"] = "Numbers the rows into **%s**, from %s%s." % (ir["name"], ir["start"],
                                                                     " as text of %s characters with leading zeros" % ir["size"] if text else "")
            write(ir["name"], ir["type"] + ("(%s)" % ir["size"] if text else ""), "row number from %s" % ir["start"])
            vals = "range(%s, %s + len(df))" % (ir["start"], ir["start"])
            if text:
                vals = "[str(n).zfill(%s) for n in %s]" % (ir["size"], vals)
            c["py"] = ['df.insert(%s, "%s", %s)' % ("len(df.columns)" if ir["last"] else "0", ir["name"], vals)]
        elif k == "Transpose":
            c["does"] = "Turns the columns %s into rows: one row per column, its name in **Name**, its value in **Value**; " \
                        "keeps %s." % (_names(ir["data"]) + (" and every other column" if ir["unknown"] else ""),
                                       _names(ir["keys"]) or "no other column")
            c["reads"] = list(ir["keys"]) + list(ir["data"])
            write("Name", "V_String", "the name of the column the value came from")
            write("Value", None, "the value of that column")
            c["py"] = ['df = df.melt(id_vars=%s, value_vars=%s, var_name="Name", value_name="Value", ignore_index=False)'
                       '.sort_index(kind="stable").reset_index(drop=True)' % (_cols(ir["keys"]), _cols(ir["data"]))]
        elif k == "FindReplace":
            src = other("S")
            svar = self.var(src[0][0], src[0][1]) if src else "source"
            how = {"FindWhole": "equals", "FindBegin": "begins", "FindAny": "appears anywhere in"}.get(ir["mode"], ir["mode"])
            case = "ignoring case" if ir["nocase"] else "matching case"
            c["reads"] = [ir["find"], ir["search"]] + (list(ir["append"]) if ir["replace_mode"] == "Append" else [ir["replace"]])
            if ir["replace_mode"] == "Append":
                c["does"] = "Looks each row up in the Source input and adds %s from the first Source row whose [%s] %s " \
                            "this row's [%s] (%s); empty when none matches." % (", ".join("**%s**" % a for a in ir["append"]),
                                                                              ir["search"], how, ir["find"], case)
                for a in ir["append"]:
                    write(a, None, "looked up: Source [%s] %s [%s]" % (ir["search"], how, ir["find"]))
                if ir["mode"] == "FindWhole" and not ir["nocase"]:
                    c["py"] = ['df = df.merge(%s[%s].drop_duplicates(%s).rename(columns={%s: "_key"}), left_on=%s, '
                               'right_on="_key", how="left").drop(columns="_key")' % (
                                   svar, _cols([ir["search"]] + list(ir["append"])), _q(ir["search"]), _q(ir["search"]),
                                   _q(ir["find"]))]
                else:
                    test = {"FindAny": "str(r[%s]) in str(v)", "FindBegin": "str(v).startswith(str(r[%s]))",
                            "FindWhole": "str(r[%s]).lower() == str(v).lower()"}.get(ir["mode"], "%s") % _q(ir["search"])
                    if ir["nocase"] and ir["mode"] != "FindWhole":
                        test = test.replace("str(r[", "str(r[").replace(") in str(v)", ").lower() in str(v).lower()")
                    c["py"] = ['df["%s"] = df["%s"].map(lambda v: next((r["%s"] for _, r in %s.iterrows() if r[%s] and %s), None))'
                               % (a, ir["find"], a, svar, _q(ir["search"]), test) for a in ir["append"]]
            else:
                c["does"] = "In [%s], replaces text that matches a Source row's [%s] with that row's [%s] (%s)." % (
                    ir["find"], ir["search"], ir["replace"], case)
                write(ir["find"], None, "text replaced from the Source input")
                c["py"] = ['for _, r in %s.iterrows(): df["%s"] = df["%s"].str.replace(r[%s], r[%s], regex=False)  '
                           '# Alteryx replaces only the first matching Source row' % (svar, ir["find"], ir["find"],
                                                                                   _q(ir["search"]), _q(ir["replace"]))]
            if ir["mode"] == "FindAny":
                c["notes"].append("Partial match: a Source value found anywhere inside [%s] counts. It behaves like an "
                                  "exact lookup only while no looked-up value can appear inside another." % ir["find"])
        elif k == "DateTime":
            fmt = _strftime(ir["format"])
            if ir["to_string"]:
                c["does"] = "Writes the date [%s] as text in the format `%s` into **%s**." % (ir["field"], ir["format"], ir["out"])
                write(ir["out"], "V_WString", "[%s] as text" % ir["field"])
                c["py"] = ['df["%s"] = pd.to_datetime(df["%s"]).dt.strftime("%s")' % (ir["out"], ir["field"], fmt)]
            else:
                c["does"] = "Reads [%s] as a date in the format `%s` into **%s**." % (ir["field"], ir["format"], ir["out"])
                write(ir["out"], None, "[%s] read as a date (%s)" % (ir["field"], ir["format"]))
                c["py"] = ['df["%s"] = pd.to_datetime(df["%s"], format="%s", errors="coerce")' % (ir["out"], ir["field"], fmt)]
            c["reads"] = [ir["field"]]
        elif k == "Cleanse":
            opts = [CLEANSE_WORDS[o] for o, v in ir["options"].items() if v and o in CLEANSE_WORDS]
            c["does"] = "Cleans the columns %s: %s." % (_names(ir["fields"]), "; ".join(opts) or "no option ticked")
            c["reads"] = list(ir["fields"])
            for f in ir["fields"]:
                write(f, None, "cleaned: " + "; ".join(opts))
            cols = _cols(ir["fields"])
            pymap = {"trim_ws": "df[cols] = df[cols].apply(lambda s: s.str.strip())",
                     "dup_ws": 'df[cols] = df[cols].apply(lambda s: s.str.replace(r"\\s+", " ", regex=True))',
                     "all_ws": 'df[cols] = df[cols].apply(lambda s: s.str.replace(r"\\s+", "", regex=True))',
                     "nulls_to_blank": 'df[cols] = df[cols].fillna("")  # text columns',
                     "nulls_to_zero": "df[cols] = df[cols].fillna(0)  # number columns"}
            c["py"] = ["cols = %s" % cols] + [pymap[o] for o, v in ir["options"].items() if v and o in pymap]
            c["notes"].append("Options read from the macro's numbered check boxes: confirm them in the tool's settings.")
        elif k == "Message":
            text = expr.readable(ir["text"]["ast"]) if ir["text"]["ast"] is not None else ir["text"]["text"]
            cond = ir["filter"]
            stop = ir["type"].lower().startswith("error")
            c["does"] = "%s %s%s." % ("Stops the run with the error" if stop else "Logs the message", text,
                                      " for rows where the test holds" if cond else "")
            if cond:
                c["rule"] = expr.decision(cond["ast"], WIDTH) if cond["ast"] is not None else [cond["text"]]
                c["reads"] = expr.fields(cond["ast"]) if cond["ast"] is not None else []
                c["py"] = ["if (%s).any(): raise RuntimeError(%s)" % (vec(cond, "df.apply(test, axis=1)"), repr(text))]
        elif k == "Output":
            path, sheet = _file(ir["file"])
            c["does"] = "Writes the rows to %s`%s`." % ("sheet **%s** of " % sheet if sheet else "", path)
            c["py"] = ['df.to_excel(r"%s", sheet_name="%s", index=False)' % (path, sheet or "Sheet1")
                       if path.lower().endswith((".xlsx", ".xlsm")) else 'df.to_csv(r"%s", index=False)' % path]
        elif k in BLOCKERS:
            c["does"] = "Waits until all rows have arrived, then releases output 1, and output 2 only after everything fed " \
                        "by output 1 has finished."
            c["py"] = ["# no pandas step: run what follows output 1 before what follows output 2"]
        elif k == "RunCommand":
            prog, args = t.cfg.findtext("Command", ""), t.cfg.findtext("CommandArguments", "")
            cmd = " ".join(x for x in (prog, args) if x)
            c["does"] = "Runs the external command `%s`." % cmd
            c["py"] = ["subprocess.run(%r, check=True)" % ([prog] + args.split())]
        else:
            c["does"] = "Runs **%s**; its settings are below." % k.replace("Macro:", "the macro ")
            c["rule"] = [ir.get("raw", "")]
            c["py"] = ["# no pandas form; write this step by hand"]
        c["reads"] = list(dict.fromkeys(x for x in c["reads"] if x))
        return c

    # ---- container facts
    def files(self, key):
        reads, writes, runs, dirs = [], [], [], []
        for i in self.order:
            if self.top_of[i] != key:
                continue
            t = self.t[i]
            if t.kind == "Input":
                reads.append(t.ir["file"])
            elif t.kind == "DynamicInput":
                reads.append("files named in [%s]" % t.ir["field"])
            elif t.kind == "Directory":
                dirs.append((t.ir["dir"], t.ir["spec"]))
                reads.append("folder %s (%s)" % (t.ir["dir"], t.ir["spec"]))
            elif t.kind == "Output":
                writes.append(t.ir["file"])
            elif t.kind == "RunCommand":
                runs.append(" ".join(x for x in (t.cfg.findtext("Command", ""), t.cfg.findtext("CommandArguments", "")) if x))
        return reads, writes, runs, dirs

    def links(self):
        """[(from container, to container, how)] by connection or by a file one writes and another reads."""
        out = []
        for e in self.wf.edges:
            a, b = e[0], e[2]
            if a in self.top_of and b in self.top_of and self.top_of[a] != self.top_of[b]:
                out.append((self.top_of[a], self.top_of[b], "step %s → %s" % (self.ref(a), self.ref(b))))
        facts = {k: self.files(k) for k in self.tops}
        for a in self.tops:
            for w in facts[a][1]:
                wpath = _file(w)[0]
                for b in self.tops:
                    if a == b:
                        continue
                    hit = any(_same_file(wpath, _file(r)[0]) for r in facts[b][0] if not r.startswith(("files named", "folder ")))
                    for folder, spec in facts[b][3]:
                        wdir, wname = os.path.split(_norm(wpath))
                        if (_same_file(wdir or ".", folder) if folder else False) and fnmatch.fnmatch(wname, (spec or "*").lower()):
                            hit = True
                    if hit:
                        out.append((a, b, "file `%s`" % wpath))
        return list(dict.fromkeys(out))

    def cname(self, key):
        return key or "Outside any container"

    def cref(self, key):
        return "[[c%d|%d. %s]]" % (self.cnum[key], self.cnum[key], self.cname(key))

    # ---- the document
    def build(self):
        wf, L, blocks = self.wf, self.L, []
        name = os.path.basename(wf.source)
        live = [self.t[i] for i in self.order]
        status = Counter(t.status for t in live)
        links = self.links()
        blocks += [("h", 1, "Migration guide: %s" % name, "top"),
                   ("p", "Generated by ayx2py from `%s` (sha256 %s…). It explains the workflow for someone who has never "
                         "opened Alteryx Designer, so it can be rebuilt in Python step by step." % (name, wf.sha256[:12])),
                   ("h", 2, "How to read this", "howto"),
                   ("steps", [{"text": t} for t in [
                       "**Containers** are the boxes Designer draws around groups of tools. They are numbered 1, 2, 3… "
                       "in the order they run.",
                       "**Branches**: inside a container, the rows flow along branches. A branch is a chain of tools with "
                       "no split or merge in it; it is named after its first tool's note or its filter test. Branch 3.4 "
                       "is the 4th branch of container 3.",
                       "**Steps**: each tool is one step, numbered within its container (step 3.14). The `#1373` after "
                       "it is the tool's ID in Designer.",
                       "**Card fields:**",
                       ]] + [{"text": "**In** — where the rows come from. A filter has outputs True and False; a join has "
                                      "J (matched), L (left rows with no match) and R (right rows with no match)."},
                             {"text": "**Does** — the step in one sentence."},
                             {"text": "**Rule** — the full expression. `when … → …` lines are read top down; the first "
                                      "true condition gives the value."},
                             {"text": "**Reads** / **Writes** — the columns the step uses and the columns it creates or "
                                      "changes (with the type Alteryx gives them)."},
                             {"text": "**Out** — where the rows go next. An output that nothing uses drops its rows."},
                             {"text": "**Python** — the same step in pandas. `df` is the table flowing through the branch; "
                                      "other tables are named after the step that made them (`s2_7` is step 2.7's "
                                      "output, `s2_7_true` its True output). Then the conversion status, and the name "
                                      "of the exact function in the generated module."}], False),
                   ("p", "The pandas lines use two helpers, because Alteryx compares text ignoring case and treats null "
                         "and the empty string separately:"),
                   ("pre", HELPERS),
                   ("h", 2, "Summary", "summary"),
                   ("steps", [{"text": x} for x in [
                       "%d containers, %d tools that run, %d branches." % (
                           len(self.tops), len(live), len({b for b in self.bid.values()})),
                       "**Python:** %d steps convert exactly, %d with a stated assumption, %d are not converted." % (
                           status["exact"], status["caveat"], status["stub"]),
                       "**Before relying on it:** see [[check|the last section]]."]], False)]

        rows, nodes, edges = [], [], []
        for key in self.tops:
            reads, writes, runs, _ = self.files(key)
            reads = list(dict.fromkeys(reads))
            disabled = not any(self.top_of[i] == key for i in self.order)
            n_tools = sum(1 for i in self.order if self.top_of[i] == key)
            fed = list(dict.fromkeys(self.cref(a) for a, b, _ in links if b == key))
            feeds = list(dict.fromkeys(self.cref(b) for a, b, _ in links if a == key))
            base = lambda xs: ", ".join(dict.fromkeys(os.path.basename(_file(x)[0].replace("\\", "/")) for x in xs)) or "—"
            rows.append([self.cref(key), "no (disabled)" if disabled else "yes", n_tools,
                         len({self.bid[s] for s in self.bid if self.top_of[L.stages[s][0]] == key}),
                         ", ".join([base([r for r in reads if not r.startswith(("files named", "folder "))])] * any(
                             not r.startswith(("files named", "folder ")) for r in reads) +
                                   [r for r in reads if r.startswith(("files named", "folder "))]) or "—",
                         base(writes) + ("; runs %d command%s" % (len(runs), "" if len(runs) == 1 else "s") if runs else ""),
                         ", ".join(fed) or "—", ", ".join(feeds) or "—"])
            nodes.append({"id": "c%d" % self.cnum[key], "label": "%d. %s" % (self.cnum[key], self.cname(key)),
                          "sub": "disabled" if disabled else "%d tools" % n_tools, "kind": "stub" if disabled else "stage",
                          "href": "c%d" % self.cnum[key]})
        edges = [("c%d" % self.cnum[a], "c%d" % self.cnum[b]) for a, b, _ in links]
        toc = []
        for key in self.tops:
            kids = []
            for path, ss in self.sections.get(key, []):
                seen = set()
                items = []
                for st in ss:
                    if st in L.copy_of:
                        if L.copy_of[st] in seen:
                            continue
                        seen.add(L.copy_of[st])
                    items.append({"text": "[[b%s|Branch %s]] — %s" % (self.bid[st].replace(".", "-"), self.bid[st],
                                                                     "repeat" if st in L.copy_of else self.label(st))})
                if len(path) > 1:
                    kids.append({"text": "Inside " + " > ".join(path[1:]), "children": items})
                else:
                    kids += items
            toc.append({"text": self.cref(key) + ("" if kids else " (disabled)"), "children": kids})
        blocks.append(("details", 2, "Contents", "toc", [("steps", toc, False)], False))
        blocks += [("h", 2, "Containers", "containers"),
                   ("table", ["Container", "Runs", "Tools", "Branches", "Reads", "Writes", "Fed by", "Feeds"], rows),
                   ("diagram", nodes, edges)]

        for key in self.tops:
            blocks.append(self.container(key, links))
        blocks += self.checks(live)
        return blocks

    def container(self, key, links):
        L, k = self.L, self.cnum[key]
        tools = [i for i in self.order if self.top_of[i] == key]
        title = "%d. %s" % (k, self.cname(key))
        if not tools:
            n = sum(1 for t in self.t.values() if t.path[:1] == [key] and t.disabled)
            kinds = Counter(t.kind for t in self.t.values() if t.path[:1] == [key] and t.disabled)
            return ("details", 2, title + " — disabled", "c%d" % k, [
                ("p", "Disabled in the workflow: its %d tools do not run, and are not described here. Kinds: %s." % (
                    n, ", ".join("%s %d" % kv for kv in kinds.most_common())))], False)
        reads, writes, runs, _ = self.files(key)
        reads, writes = list(dict.fromkeys(reads)), list(dict.fromkeys(writes))
        inner = []
        show = lambda r: r if r.startswith(("files named", "folder ")) else "`%s`" % r
        facts = [{"text": "**Reads:** " + ("; ".join(show(r) for r in reads) or "—")},
                 {"text": "**Writes:** " + ("; ".join("`%s`" % w for w in writes) or "—")}]
        if runs:
            facts.append({"text": "**Runs:** " + "; ".join("`%s`" % r for r in runs)})
        def grouped(pairs):
            by = defaultdict(list)
            for other, how in pairs:
                by[other].append(how)
            return "; ".join("%s (%s)" % (self.cref(o), ", ".join(dict.fromkeys(h))) for o, h in by.items())
        fed = grouped((a, how) for a, b, how in links if b == key)
        feeds = grouped((b, how) for a, b, how in links if a == key)
        facts.append({"text": "**Fed by:** " + (fed or "nothing from other containers")})
        facts.append({"text": "**Feeds:** " + (feeds or "no other container")})
        nested = [p for p, _ in self.sections[key] if len(p) > 1]
        if nested:
            facts.append({"text": "**Containers inside:** " + "; ".join(" > ".join(p[1:]) for p in nested)})
        stubs = [i for i in tools if self.t[i].status == "stub"]
        if stubs:
            facts.append({"text": "**Not converted to Python:** steps " + ", ".join(self.ref(i) for i in stubs)})
        off = [p for p, dis in self.wf.containers if dis and p[:1] == [key] and len(p) > 1]
        if off:
            facts.append({"text": "**Disabled inside (not run):** " + "; ".join(" > ".join(p[1:]) for p in off)})
        inner.append(("steps", facts, False))

        # branch map
        branches = sorted({s for s in self.bid if self.top_of[L.stages[s][0]] == key}, key=lambda s: _bkey(self.bid[s]))
        groups = defaultdict(list)
        for s in branches:
            if s in L.copy_of:
                groups[L.copy_of[s]].append(s)
        shown, rows, nodes, edges = set(), [], [], []
        for s in branches:
            if s in L.copy_of:
                g = groups[L.copy_of[s]]
                if s != g[0]:
                    continue
                label, ids = "repeat of " + self.span_text([self.twin_stage(x) for x in g], branch=True), g
                bid = "%s–%s" % (self.bid[g[0]], self.bid[g[-1]].split(".")[1]) if len(g) > 1 else self.bid[g[0]]
            else:
                label, ids, bid = self.label(s), [s], self.bid[s]
            first, last = L.stages[ids[0]][0], L.stages[ids[-1]][-1]
            members = {i for x in ids for i in L.stages[x]}
            ins = list(dict.fromkeys(self.src(e) for x in ids for i in L.stages[x] for e in self.wf.ins.get(i, [])
                                     if e[0] not in members))
            outs = list(dict.fromkeys(self.ref(e[2]) for x in ids for i in L.stages[x] for e in self.wf.outs.get(i, [])
                                      if e[2] not in members))
            rows.append(["[[b%s|%s]]" % (self.bid[ids[0]].replace(".", "-"), bid), label,
                         "%s–%s" % (self.sid[first], self.sid[last].split(".")[1]) if first != last else self.sid[first],
                         ", ".join(ins) or "—", ", ".join(outs) or "—"])
            nodes.append({"id": "b" + self.bid[ids[0]].replace(".", "-"), "label": "Branch " + bid, "sub": label,
                          "kind": "repeat" if ids[0] in L.copy_of else "stub" if any(self.t[i].status == "stub" for i in members)
                          else "input" if self.t[first].kind in SOURCES else "output" if self.t[last].kind == "Output" else "stage",
                          "href": "b" + self.bid[ids[0]].replace(".", "-")})
            for x in ids:
                for i in L.stages[x]:
                    for e in self.wf.outs.get(i, []):
                        d = L.stage_of[e[2]]
                        if d in self.bid and self.top_of[e[2]] == key and d not in ids:
                            dg = groups[L.copy_of[d]][0] if d in L.copy_of else d
                            edges.append(("b" + self.bid[ids[0]].replace(".", "-"), "b" + self.bid[dg].replace(".", "-")))
        inner += [("h", 3, "Branches", None), ("table", ["Branch", "Label", "Steps", "In", "Out"], rows)]
        if len(nodes) <= 120:
            inner.append(("diagram", nodes, edges))

        # sections and branches
        for path, ss in self.sections[key]:
            body = []
            seen_groups = set()
            for s in sorted(ss, key=lambda s: _bkey(self.bid[s])):
                if s in L.copy_of:
                    if L.copy_of[s] in seen_groups:
                        continue
                    seen_groups.add(L.copy_of[s])
                    body.append(self.repeat(groups[L.copy_of[s]]))
                    continue
                body.append(self.branch(s))
            if len(path) > 1:
                inner.append(("details", 3, "Inside " + " > ".join(path[1:]), None, body, True))
            else:
                inner += body
        inner.append(self.columns(key, tools))
        return ("details", 2, "%s — %d tool%s, %d branch%s" % (title, len(tools), "" if len(tools) == 1 else "s", len(branches),
                                                                "" if len(branches) == 1 else "es"), "c%d" % k, inner, False)

    def twin_stage(self, s):
        b, c = self.L.copy_of[s]
        twin = dict(zip(self.L.blocks[b - 1][c], self.L.blocks[b - 1][0]))
        return self.L.stage_of[twin[self.L.stages[s][0]]]

    def span_text(self, stages, branch=False):
        ids = sorted({self.bid[s] for s in stages if s in self.bid}, key=_bkey)
        if not ids:
            return "?"
        word = "branch" if len(ids) == 1 else "branches"
        if len(ids) == 1:
            return "%s %s" % (word, ids[0])
        return "%s %s–%s" % (word, ids[0], ids[-1].split(".")[1] if ids[0].split(".")[0] == ids[-1].split(".")[0] else ids[-1])

    def label(self, s):
        L = self.L
        first = self.t[L.stages[s][0]]
        if first.note:
            return first.note if len(first.note) <= 70 else first.note[:68] + "…"
        if first.kind == "Filter":
            ir = first.ir
            cond = "[%s] %s %s" % (ir["field"], ir["operator"], ir["operand"]) if ir["mode"] == "simple" else \
                (expr.readable(ir["expr"]["ast"]) if ir["expr"]["ast"] is not None else ir["expr"]["text"])
            return "Filter: " + (cond if len(cond) <= 60 else cond[:58] + "…")
        ir = first.ir
        special = {"Join": lambda: "Join on " + _names(ir["left"]),
                   "Union": lambda: "Union of %d inputs" % len(self.wf.ins.get(first.id, [])),
                   "FindReplace": lambda: "Look up [%s]" % ir["find"], "AppendFields": lambda: "Append columns",
                   "Summarize": lambda: "Summarize by " + (_names(f for f, a, _, _ in ir["fields"] if a == "GroupBy") or "all rows"),
                   "Transpose": lambda: "Transpose " + _names(ir["data"][:3]), "Unique": lambda: "Unique on " + _names(ir["fields"][:3]),
                   "DynamicInput": lambda: "Read files listed in [%s]" % ir["field"], "TextInput": lambda: "Typed table",
                   "Directory": lambda: "List files in " + ir["dir"]}
        if first.kind in special:
            text = special[first.kind]()
            return text if len(text) <= 70 else text[:68] + "…"
        e = L.primary(first.id)
        if e is not None and self.t[e[0]].kind == "Filter":
            ir = self.t[e[0]].ir
            cond = "[%s] %s %s" % (ir["field"], ir["operator"], ir["operand"]) if ir["mode"] == "simple" else \
                (expr.readable(ir["expr"]["ast"]) if ir["expr"]["ast"] is not None else ir["expr"]["text"])
            cond = cond if len(cond) <= 50 else cond[:48] + "…"
            return "%s: %s" % ("passes" if e[1] == "True" else "fails", cond)
        if first.kind in ("Input", "Output"):
            return ("Read " if first.kind == "Input" else "Write ") + os.path.basename(_file(first.ir["file"])[0].replace("\\", "/"))
        kinds = list(dict.fromkeys(self.t[i].kind for i in L.stages[s]))
        return " → ".join(kinds[:4]) + (" → …" if len(kinds) > 4 else "")

    def branch(self, s):
        L = self.L
        ids = L.stages[s]
        first, last = ids[0], ids[-1]
        ins = [self.src(e) for i in ids for e in self.wf.ins.get(i, []) if e[0] not in ids]
        outs = [self.ref(e[2]) for e in self.wf.outs.get(last, [])]
        head = "Branch %s — %s · steps %s" % (self.bid[s], self.label(s), self.sid[first] if first == last else
                                               "%s–%s" % (self.sid[first], self.sid[last].split(".")[1]))
        body = [("p", "**In:** %s.  **Out:** %s." % (", ".join(dict.fromkeys(ins)) or "— (starts here)",
                                                     ", ".join(dict.fromkeys(outs)) or "— (ends here)"))]
        body += [self.card_block(i) for i in ids]
        return ("details", 4, head, "b" + self.bid[s].replace(".", "-"), body, False)

    def card_block(self, i):
        t, c = self.t[i], self.cards[i]
        title = "Step %s · %s · #%s%s" % (self.sid[i], t.kind.replace("Macro:", "Macro "), i,
                                          " · “%s”" % t.note if t.note else "")
        fields = [("In", self.ins_text(i), []), ("Does", c["does"] + ("  " + " ".join(c["notes"]) if c["notes"] else ""), [])]
        if c["rule"]:
            fields.append(("Rule", "", c["rule"]))
        if c["reads"]:
            fields.append(("Reads", ", ".join("[%s]" % x for x in c["reads"]), []))
        if c["writes"]:
            fields.append(("Writes", ", ".join("**%s**%s%s" % (n, " (%s)" % typ if typ else "", w[3] if len(w) > 3 else "")
                                               for w in c["writes"] for n, typ in [w[:2]]), []))
        fields.append(("Out", self.outs_text(i), []))
        if t.status == "stub":
            status = "**not converted**: " + "; ".join(t.reasons())
        elif t.status == "caveat":
            refs = ["[[a%d|A%d]]" % (self.assume[t.kind], self.assume[t.kind])] if t.kind in self.assume else []
            status = "converted" + (", assuming " + refs[0] if refs else "") + \
                ("; note: " + "; ".join(t.caveats) if t.caveats else "")
        else:
            status = "exact"
        status += "" if t.kind in BLOCKERS else " · generated function `%s`" % self.fn[i]
        py = c["py"]
        if len(py) == 1 and len(py[0]) <= 110 and "`" not in py[0]:
            fields.append(("Python", "`%s` — %s" % (py[0], status), []))
        else:
            fields.append(("Python", status, py))
        return ("card", "s" + self.sid[i].replace(".", "-"), title, fields)

    def repeat(self, group):
        L = self.L
        b, c = L.copy_of[group[0]]
        copy, first = L.blocks[b - 1][c], L.blocks[b - 1][0]
        twin = dict(zip(copy, first))
        tools = sorted((i for s in group for i in L.stages[s]), key=lambda i: self.pos[i])
        orig = [twin[i] for i in tools]
        mem = set(tools)
        lines = []
        for i in tools:
            for e in self.wf.ins.get(i, []):
                if e[0] not in mem:
                    mirror = next((f for f in self.wf.ins.get(twin[i], []) if f[3] == e[3]), None)
                    lines.append("step %s takes %s, where step %s takes %s" % (
                        self.ref(i), self.src(e), self.ref(twin[i]), self.src(mirror) if mirror else "—"))
            for e in self.wf.outs.get(i, []):
                if e[2] not in mem:
                    lines.append("step %s sends its rows to %s" % (self.ref(i), self.ref(e[2])))
        span = lambda xs: "%s–%s" % (self.sid[xs[0]], self.sid[xs[-1]].split(".")[1]) if len(xs) > 1 else self.sid[xs[0]]
        delta = {self.pos[x] - self.pos[y] for x, y in zip(tools, orig)}
        same_c = len({self.sid[x].split(".")[0] for x in orig}) == 1
        mapping = "Step for step: %s here is %s there, in the same order." % (span(tools), span(sorted(orig, key=lambda i: self.pos[i]))) \
            if same_c else "Step for step: " + ", ".join("%s = %s" % (self.sid[x], self.sid[y]) for x, y in zip(tools, orig))
        head = "Branches %s — repeat of %s · steps %s" % (
            self.bid[group[0]] if len(group) == 1 else "%s–%s" % (self.bid[group[0]], self.bid[group[-1]].split(".")[1]),
            self.span_text([self.twin_stage(s) for s in group], branch=True), span(tools))
        return ("details", 4, head, "b" + self.bid[group[0]].replace(".", "-"), [
            ("p", "These %d steps have the same rules and wiring as steps %s (see there); only the rows differ:" % (
                len(tools), span(sorted(orig, key=lambda i: self.pos[i])))),
            ("steps", [{"text": x} for x in dict.fromkeys(lines)], False),
            ("p", mapping + " Tool IDs: %s." % _id_map(tools, orig))], False)

    def columns(self, key, tools):
        made = defaultdict(list)
        for i in tools:
            if self.L.stage_of[i] in self.L.copy_of:
                continue
            for w in self.cards[i]["writes"]:
                made[w[0]].append((i, w[1], w[2], w[3] if len(w) > 3 else ""))
        readers = defaultdict(list)
        for i in self.order:
            for col in self.cards[i]["reads"]:
                readers[col.lower()].append(i)
        rows = []
        for col, sets in sorted(made.items(), key=lambda kv: self.pos[kv[1][0][0]]):
            first = min(self.pos[s[0]] for s in sets)
            hows = list(dict.fromkeys(s[2] for s in sets if s[2]))
            types = list(dict.fromkeys(s[1] for s in sets if s[1]))
            users = [i for i in readers.get(col.lower(), []) if self.pos[i] > first]
            shown = ", ".join(self.ref(i) for i in users[:12]) + (" and %d more" % (len(users) - 12) if len(users) > 12 else "")
            how = hows[0] if len(hows) == 1 else (" / ".join(hows) if sum(map(len, hows)) <= 80 else
                                                   "differs by step (%d rules; see the steps)" % len(hows)) if hows else ""
            rows.append(["**%s**" % col, ", ".join(types) or "", ", ".join(self.ref(s[0]) for s in sets),
                         how if len(how) <= 120 else how[:118] + "…", shown or "not read by a later step"])
        if not rows:
            return ("p", "")
        return ("details", 3, "Columns created or changed in container %d (%d)" % (self.cnum[key], len(rows)),
                "cols%d" % self.cnum[key],
                [("p", "Each column this container creates or changes: the type Alteryx gives it, the steps that set it, "
                       "how, and the later steps (anywhere in the workflow) that use a column of that name."),
                 ("table", ["Column", "Type", "Set at", "How", "Used later by"], rows)], False)

    def checks(self, live):
        L, items = self.L, []
        stubs = [t for t in live if t.status == "stub"]
        if stubs:
            why = defaultdict(list)
            for t in stubs:
                why[(t.kind, "; ".join(t.reasons()))].append(t)
            items.append({"text": "**Not converted to Python** (%d steps): the generated code stops there with an error "
                                  "until the step is written by hand." % len(stubs),
                          "children": [{"text": "%s, %d step%s (%s): %s" % (kind, len(ts), "" if len(ts) == 1 else "s",
                                                                           ", ".join(self.ref(t.id) for t in ts), reason)}
                                       for (kind, reason), ts in why.items()]})
        fa = [t.id for t in live if t.kind == "FindReplace" and t.ir.get("mode") == "FindAny"]
        if fa:
            items.append({"text": "**Partial-match lookups** (Find Replace, any part of the field) at steps " +
                                  ", ".join(self.ref(i) for i in fa) + ": they behave like an exact lookup only while no "
                                  "looked-up value can appear inside another."})
        ao = [t.id for t in live if any("AND and OR" in c for c in t.caveats)]
        if ao:
            items.append({"text": "**AND and OR mixed without brackets** at steps " + ", ".join(self.ref(i) for i in ao) +
                                  ": Alteryx reads them left to right, so `a OR b AND c` means `(a OR b) AND c`. The rules "
                                  "show the brackets Alteryx applies; check that this was intended."})
        if self.assume:
            items.append({"text": "**Alteryx behaviours the Python assumes** (cards refer to them as A1, A2…; settle them "
                                  "once with the probe workflow):",
                          "children": [{"text": "**A%d** — %s: %s" % (n, k, STATUS[k][1]), "anchor": "a%d" % n}
                                       for k, n in self.assume.items()]})
        waits = [t.id for t in live if t.kind in BLOCKERS]
        if waits:
            items.append({"text": "**Run order:** Block Until Done at steps %s: a rewrite must finish what follows output "
                                  "1 before starting what follows output 2." % ", ".join(self.ref(i) for i in waits)})
        if self.wf.cyclic:
            items.append({"text": "**Tools in a loop** (order unknown): " + " ".join("#" + i for i in self.wf.cyclic)})
        return [("h", 2, "Before relying on this", "check"), ("steps", items or [{"text": "Nothing to flag."}], False)]


def _bkey(bid):
    return tuple(int(x) for x in bid.split("."))


def _id_map(copy, first):
    if all(x.isdigit() for x in copy + first):
        deltas = {int(x) - int(y) for x, y in zip(copy, first)}
        if len(deltas) == 1:
            return "each is the original's ID %+d" % deltas.pop()
    return ", ".join("#%s = #%s" % (x, y) for x, y in zip(copy, first))


def build(wf):
    return Guide(wf).build()
