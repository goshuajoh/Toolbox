"""Migration guide: a workflow explained for an engineer who has never opened Designer.

Top level: the containers, the files (with sheet and cells) each reads and writes, and which
containers feed which (by a connection or through a file one writes and another reads). Inside a
container: its branches, as Designer draws them, each labelled by its first tool's annotation or its
filter test. Inside a branch: one table that reads like the canvas (step, tool, what it does,
columns it creates or changes, next step), then each step's full rule and pandas form folded away.
Per container: a table of the columns it creates or changes and where each is used later. Repeated
sub-chains are described once. Deterministic; standard library only.
"""
import fnmatch
import os
import re
import traceback
from collections import Counter, defaultdict

from . import codegen, expr, sketch
from .spec import describe
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
DESIGNER = {"Input": "Input Data", "Output": "Output Data", "DynamicInput": "Dynamic Input", "Directory": "Directory",
            "TextInput": "Text Input", "Select": "Select", "Formula": "Formula", "MultiFieldFormula": "Multi-Field Formula",
            "MultiRowFormula": "Multi-Row Formula", "Filter": "Filter", "Join": "Join", "JoinMultiple": "Join Multiple",
            "AppendFields": "Append Fields", "Union": "Union", "Summarize": "Summarize", "Sort": "Sort", "Unique": "Unique",
            "Sample": "Sample", "RecordID": "Record ID", "Transpose": "Transpose", "CrossTab": "Cross Tab",
            "FindReplace": "Find Replace", "DateTime": "DateTime", "Cleanse": "Data Cleansing", "Message": "Message",
            "BlockUntilDone": "Block Until Done", "ParallelBlockUntilDone": "Parallel Block Until Done",
            "RunCommand": "Run Command", "TextToColumns": "Text To Columns", "RegEx": "RegEx",
            "GenerateRows": "Generate Rows", "Tile": "Tile", "DynamicRename": "Dynamic Rename",
            "DynamicReplace": "Dynamic Replace", "XMLParse": "XML Parse", "JSONParse": "JSON Parse", "Imputation": "Imputation"}
OUT_MODE = {"Overwrite": "replaces the sheet", "RemoveFile": "replaces the whole file", "Append": "adds rows",
            "Create": "new sheet; stops if it exists"}
HELPERS = [
    "import functools, glob, operator, os, subprocess",
    "import numpy as np",
    "import pandas as pd",
    "",
    "pd.options.mode.copy_on_write = True   # df = s2_6, then changing df, leaves s2_6 as it was",
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


def designer(kind):
    """The tool's name on Designer's palette."""
    if kind.startswith("Macro:"):
        return "Macro " + kind[6:]
    return DESIGNER.get(kind, re.sub(r"(?<=[a-z])(?=[A-Z])", " ", kind))


def place(spec, options=None):
    """'Book.xlsx|||`Sheet1$A1:D20`' -> (path, sheet, cells, first line read or None)."""
    path, _, rest = str(spec).partition("|||")
    rest = rest.strip().strip("`'\"")
    m = re.fullmatch(r"(.*?)\$([A-Za-z]+\d+:[A-Za-z]+\d+)?", rest)
    sheet, cells = (m.group(1).strip("'"), m.group(2) or "") if m else (rest, "")
    line = str((options or {}).get("ImportLine", "") or "")
    return path.strip(), sheet, cells, int(line) if line.isdigit() and int(line) > 1 else None


def place_text(spec, options=None, bold=True):
    """File name, sheet, cells and first line, for a table cell."""
    path, sheet, cells, line = place(spec, options)
    name = os.path.basename(path.replace("\\", "/")) or path
    parts = [("**%s**" if bold else "`%s`") % (name if bold else path)]
    if sheet:
        parts.append("sheet **%s**" % sheet if not sheet.startswith("<") else "sheets " + sheet)
    if cells:
        parts.append("cells %s" % cells.upper())
    if line:
        parts.append("from line %d" % line)
    opts = options or {}
    if opts.get("HeaderRow") == "False" or opts.get("FirstRowData") == "True":
        parts.append("no header row")
    return " · ".join(parts)


def file_text(spec, options=None):
    """`Mapping.xlsx` › sheet `Ccy` › from row 16: how a person names what they open."""
    path, sheet, cells, line = place(spec, options)
    parts = ["`%s`" % (os.path.basename(path.replace("\\", "/")) or path)]
    if sheet:
        parts.append("sheet `%s`" % sheet if not sheet.startswith("<") else "sheets " + sheet)
    if cells:
        parts.append("cells %s" % cells.upper())
    if line:
        parts.append("from row %d" % line)
    opts = options or {}
    if opts.get("HeaderRow") == "False" or opts.get("FirstRowData") == "True":
        parts.append("no header row")
    return " › ".join(parts)


def _slug(text):
    s = re.sub(r"\W+", "_", text).strip("_").lower()
    return ("f_" + s) if s[:1].isdigit() else s


def path_parts(path, *more):
    """A workflow path as Python: os.path.join(FOLDER, ...) when relative, the literal when absolute."""
    p = path.strip()
    tail = "".join(', "%s"' % x for x in more)
    if re.match(r"^([A-Za-z]:[\\/]|\\\\|/)", p):
        return ('os.path.join(r"%s"%s)' % (p, tail)) if more else 'r"%s"' % p
    parts = [x for x in re.split(r"[\\/]", p) if x and x != "."]
    return "os.path.join(FOLDER%s%s)" % ("".join(', "%s"' % x for x in parts), tail)


SIMPLE = {"=": ("is", 'eq(df[{f}], {v})'), "!=": ("is not", '~eq(df[{f}], {v})'), ">": (">", "df[{f}] > {v}"),
          ">=": ("≥", "df[{f}] >= {v}"), "<": ("<", "df[{f}] < {v}"), "<=": ("≤", "df[{f}] <= {v}"),
          "isnull": ("is null", "df[{f}].isna()"), "isnotnull": ("is not null", "df[{f}].notna()"),
          "isempty": ("is empty", "empty(df[{f}])"), "isnotempty": ("is not empty", "~empty(df[{f}])"),
          "contains": ("contains", "df[{f}].str.contains({v}, case=False, regex=False, na=False)"),
          "notcontains": ("does not contain", "~df[{f}].str.contains({v}, case=False, regex=False, na=False)"),
          "startswith": ("starts with", "df[{f}].str.lower().str.startswith({v}.lower(), na=False)"),
          "endswith": ("ends with", "df[{f}].str.lower().str.endswith({v}.lower(), na=False)"),
          "istrue": ("is true", "df[{f}] == True"), "isfalse": ("is false", "df[{f}] == False")}


def _operand(v):
    try:
        float(v)
        return v
    except (TypeError, ValueError):
        return repr(v)


def simple_words(ir):
    op = ir["operator"].replace(" ", "").lower()
    word = SIMPLE.get(op, (ir["operator"],))[0]
    if op in ("isnull", "isnotnull", "isempty", "isnotempty", "istrue", "isfalse"):
        return "[%s] %s" % (ir["field"], word)
    v = ir["operand"]
    return "[%s] %s %s" % (ir["field"], word, v if _operand(v) == v else '"%s"' % v)


def simple_py(ir):
    op = ir["operator"].replace(" ", "").lower()
    if op not in SIMPLE:
        return None
    return "(" + SIMPLE[op][1].format(f=_q(ir["field"]), v=_operand(ir["operand"])) + ")"


def _encoding(opts):
    return {"28591": ', encoding="latin-1"', "65001": ', encoding="utf-8-sig"', "1252": ', encoding="cp1252"',
            "1200": ', encoding="utf-16"'}.get(str((opts or {}).get("CodePage", "")), "")


def _short(text, n=70):
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _list(names, n=6, bold=False):
    names = list(dict.fromkeys(names))
    fmt = "**%s**" if bold else "[%s]"
    return ", ".join(fmt % x for x in names[:n]) + (" +%d more" % (len(names) - n) if len(names) > n else "")


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


def where_raised(exc):
    """The innermost function of this package that the exception passed through: "guide.short"."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if os.sep + "ayx2py" + os.sep in f.filename]
    f = frames[-1] if frames else (traceback.extract_tb(exc.__traceback__) or [None])[-1]
    return "%s.%s" % (os.path.splitext(os.path.basename(f.filename))[0], f.name) if f else "?"


class Guide:
    def __init__(self, wf):
        self.wf, self.t = wf, wf.tools
        self.errors = []                 # (exception type, where, tool id, tool kind): shown in the guide, and --diagnose
        self.L = Logic(wf)
        self.fn, _ = codegen.function_names(wf)
        self.rank = wf.rank
        top = lambda i: self.t[i].path[0] if self.t[i].path else ""
        self.top_of = {i: top(i) for i in wf.live}
        live_tops = sorted({top(i) for i in wf.live}, key=lambda k: min(self.rank[i] for i in wf.live if top(i) == k))
        off = [p[0] for p, dis in wf.containers if len(p) == 1 and dis and p[0] not in live_tops]
        self.tops = live_tops + list(dict.fromkeys(off))
        self.cnum = {k: n for n, k in enumerate(self.tops, 1)}
        self.off = {p[0] for p, dis in wf.containers if len(p) == 1 and dis}      # top containers disabled when saved
        self.number()
        self.cards = {i: self.safe_card(i) for i in self.order}
        kinds = sorted({self.t[i].kind for i in self.order if self.t[i].status == "caveat" and STATUS.get(self.t[i].kind, ("", ""))[1]})
        self.assume = {k: n for n, k in enumerate(kinds, 1)}

    # ---- numbering: containers k, branches k.m, steps k.n, in reading order
    def number(self):
        L = self.L
        self.sid, self.bid, self.order, self.sections = {}, {}, [], {}
        self.find_folded()
        done = set(self.folded)
        for key in self.tops:
            k = self.cnum[key]
            stages = [s for s in range(len(L.stages)) if self.top_of[L.stages[s][0]] == key and s not in self.folded]
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
        order, letters = [], defaultdict(int)          # a folded read sits just before the step that uses it
        first_use = defaultdict(list)
        for s, users in self.folded.items():
            first_use[min(users, key=lambda u: self.order.index(u))].append(s)
        for i in self.order:
            for s in first_use.get(i, []):
                for x in L.stages[s]:
                    letters[i] += 1
                    self.sid[x] = self.sid[i] + "abcdefghijklmnopqrstuvwxyz"[(letters[i] - 1) % 26] * (1 + (letters[i] - 1) // 26)
                    self.fold_of[x] = s
                    order.append(x)
            order.append(i)
        self.order = order
        self.pos = {i: n for n, i in enumerate(self.order)}
        self.name_reads()

    def find_folded(self):
        """Stages that only read a file (or a typed table), maybe choose or clean its columns, and hand the rows to
        a step with several inputs (Join, Find Replace, Append Fields, Union): shown inside that step, not as a branch."""
        L, wf = self.L, self.wf
        self.folded, self.fold_of = {}, {}
        for s, ids in enumerate(L.stages):
            if s in L.copy_of or self.t[ids[0]].kind not in ("Input", "TextInput"):
                continue
            if any(self.t[i].kind not in ("Select", "Cleanse") for i in ids[1:]):
                continue
            outs = wf.outs.get(ids[-1], [])
            if outs and all(len(wf.ins.get(e[2], [])) > 1 and e[2] in self.top_of for e in outs):
                self.folded[s] = list(dict.fromkeys(e[2] for e in outs))

    def name_reads(self):
        """Variable names for the files the workflow reads: FOLDER-relative path variables, one src_ table per
        distinct (file, sheet, cells, header) read."""
        self.paths, self.reads, self.src_of = {}, {}, {}
        taken = set()

        def unique(name):
            base, n = name, 1
            while name in taken:
                n += 1
                name = "%s_%d" % (base, n)
            taken.add(name)
            return name

        for i in self.order:
            t = self.t[i]
            if t.kind == "Input":
                path, sheet, cells, line = place(t.ir["file"], t.ir["options"])
                if _norm(path) not in self.paths:
                    self.paths[_norm(path)] = (unique(_slug(os.path.basename(path.replace("\\", "/"))).upper() or "FILE"), path, [])
                self.paths[_norm(path)][2].append(i)
                key = (_norm(path), sheet.lower(), cells.upper(), line, t.ir["options"].get("HeaderRow"),
                       t.ir["options"].get("FirstRowData"))
                stem = os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]
                name = "src_" + _slug(stem) + ("_" + _slug(sheet) if sheet and sheet.lower() != "sheet1" else "")
            elif t.kind == "TextInput":
                key, name = ("typed", i), "src_typed_" + _slug("_".join(t.ir["columns"][:2]))
            else:
                continue
            if key not in self.reads:
                self.reads[key] = {"var": unique(name), "tools": []}
            self.reads[key]["tools"].append(i)
            self.src_of[i] = self.reads[key]["var"]

    def chain(self, i):
        """The tools of the folded read that ends at tool i, and the name of the table it gives."""
        s = self.fold_of[i]
        ids = self.L.stages[s]
        v = self.src_of[ids[0]]
        return ids, v if len(ids) == 1 else "%s_%s" % (v, self.sid[ids[-1]].replace(".", "_"))

    def read_label(self, i):
        """How a folded read is named in the step that uses it: `file` › sheet › cells, or the typed table."""
        first = self.t[self.L.stages[self.fold_of[i]][0]]
        if first.kind == "TextInput":
            return "a typed table (%s; %d rows)" % (_list(first.ir["columns"], 3), len(first.ir["rows"]))
        return file_text(first.ir["file"], first.ir["options"])

    # ---- references
    def ref(self, i):
        if i in self.fold_of:
            return self.read_label(i)
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
        if i in self.fold_of:
            return self.chain(i)[1]
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

    # ---- reading a file into a src_ table, as the notebook's Load cell does it
    def load_lines(self, i):
        t, ir = self.t[i], self.t[i].ir
        v = self.src_of[i]
        numbers = {n: ("float" if typ in ("Double", "Float", "FixedDecimal") else "Int64")
                   for n, typ in ir.get("types", {}).items() if typ in ("Double", "Float", "FixedDecimal", "Byte", "Int16",
                                                                         "Int32", "Int64")}
        typed = ".astype({%s})" % ", ".join("%s: %s" % (_q(n), x if x == "float" else '"Int64"') for n, x in numbers.items()) \
            if numbers else ""
        if t.kind == "TextInput":
            rows = [[None if x in (None, "") else x for x in r] + [None] * (len(ir["columns"]) - len(r)) for r in ir["rows"]]
            return ["%s = pd.DataFrame(%r, columns=%s)%s" % (v, rows, _cols(ir["columns"]), typed)]
        path, sheet, cells, line = place(ir["file"], ir["options"])
        opts = ir["options"]
        pv = self.paths[_norm(path)][0]
        no_header = opts.get("HeaderRow") == "False" or opts.get("FirstRowData") == "True"
        low = path.lower()
        if low.endswith((".csv", ".txt", ".tsv", ".dat", ".prn")):
            sep = opts.get("Delimeter", opts.get("Delimiter", ",")) or ","
            sep = {"\\t": "\t", "\\0": "\n"}.get(sep, sep)
            args = ["sep=%r" % sep] + [x for x in (_encoding(opts)[2:],) if x] + ["dtype=str", "keep_default_na=False"]
            if line:
                args.append("skiprows=%d" % (line - 1))
            if no_header:
                args.append("header=None")
            return ['%s = pd.read_csv(%s, %s).replace("", None)%s' % (v, pv, ", ".join(args), typed)]
        if low.endswith((".xlsx", ".xlsm", ".xls", ".xlsb")):
            args = ['sheet_name="%s"' % sheet] if sheet and not sheet.startswith("<") else []
            if cells:
                c1, r1, c2, r2 = re.fullmatch(r"([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)", cells).groups()
                args += ["header=%s" % ("None" if no_header else int(r1) - 1), 'usecols="%s:%s"' % (c1.upper(), c2.upper()),
                         "nrows=%d" % (int(r2) - int(r1) + (1 if no_header else 0))]
            elif line or no_header:
                args.append("header=%s" % ("None" if no_header else line - 1))
                if no_header and line:
                    args.append("skiprows=%d" % (line - 1))
            return ["%s = pd.read_excel(%s%s)%s" % (v, pv, "".join(", " + a for a in args), typed)]
        return ["%s = pd.DataFrame()  # pandas cannot read %s directly: export it to CSV or Excel and read that" % (v, path)]

    def failed(self, i, exc):
        """Record an exception met while describing tool i; -> the words to show for it."""
        what = "%s in %s" % (type(exc).__name__, where_raised(exc))
        self.errors.append((type(exc).__name__, where_raised(exc), i, self.t[i].kind if i in self.t else "?"))
        return what

    def safe_card(self, i):
        try:
            return self.card(i)
        except Exception as exc:  # noqa: BLE001 - one tool must not stop the guide; the error is reported
            t = self.t[i]
            what = self.failed(i, exc)
            t.problems.append("the converter failed on this step (%s)" % what)
            return {"does": "Runs **%s** (the converter could not describe this step: %s; its settings are below)."
                            % (designer(t.kind), what),
                    "rule": ["setting: " + x for x in describe(t)], "reads": [], "writes": [],
                    "py": ["# not converted (%s): write this step by hand from its settings" % what], "notes": []}

    def guard(self, i, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 - reported in the row and in the last section
            return "(could not describe: %s)" % self.failed(i, exc)

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

        pre = []                                 # lines a long or deep rule needs before its pandas expression

        def vec(e, fallback, col=None):
            try:
                if e["ast"] is None:
                    raise sketch.NoSketch("unreadable")
                lines, text = sketch.vec_lines(e["ast"], col)
                pre[:] = lines
                return text
            except (sketch.NoSketch, RecursionError, MemoryError):
                pre[:] = []
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
            c["py"] = self.load_lines(i)
        elif k == "DynamicInput":
            tpl, sheet, _, _ = place(ir["file"], ir["options"])
            kind = ir.get("kind")
            c["does"] = "Opens, for each incoming row, %s, and stacks their rows." % self.dyn_words(i)
            c["reads"] = [ir["field"]]
            folder, name = os.path.split(tpl.replace("\\", "/"))
            stem, ext = os.path.splitext(name)
            value = 'p.split("|||")[0]'
            path = {"path": 'os.path.join(FOLDER, %s.replace("\\\\", os.sep))' % value,
                    "name": "os.path.join(%s, %s)" % (path_parts(folder or "."), value),
                    "prefix": "os.path.join(%s, %s + %r)" % (path_parts(folder or "."), value, name),
                    "suffix": "os.path.join(%s, %r + %s + %r)" % (path_parts(folder or "."), stem, value, ext)}.get(kind)
            excel = tpl.lower().endswith((".xlsx", ".xlsm", ".xls"))
            opts = ', sheet_name="%s"' % sheet if excel and sheet else ", dtype=str%s" % _encoding(ir["options"]) if not excel else ""
            fn = "pd.read_excel" if excel else "pd.read_csv"
            if path is None:
                c["py"] = ["# Dynamic Input mode %s/%s is not known to the converter: read each file named in [%s] by hand"
                           % (ir["mode"], ir["how"], ir["field"])]
            else:
                c["py"] = ['files = [%s(%s%s) for p in df["%s"].dropna()]' % (fn, path, opts, ir["field"]),
                           'df = pd.concat(files or [%s(%s%s, nrows=0)], ignore_index=True)  # no file listed: the '
                           'template\'s columns, no rows' % (fn, path_parts(tpl), opts)]
            for name, typ in ir.get("types", {}).items():
                write(name, typ, "read from the files")
        elif k == "Directory":
            c["does"] = "Lists the files in `%s` matching `%s`: one row per file." % (ir["dir"], ir["spec"])
            for name in ("FullPath", "FileName", "CreationTime", "LastWriteTime"):
                write(name, None, "file details")
            c["py"] = ['files = sorted(glob.glob(%s))' % path_parts(ir["dir"], ir["spec"] or "*"),
                       'df = pd.DataFrame({"Directory": [os.path.dirname(f) for f in files], "FileName": [os.path.basename(f) '
                       'for f in files], "FullPath": files,',
                       '                   "CreationTime": [pd.Timestamp.fromtimestamp(os.path.getctime(f)) for f in files],',
                       '                   "LastWriteTime": [pd.Timestamp.fromtimestamp(os.path.getmtime(f)) for f in files],',
                       '                   "Size": [os.path.getsize(f) for f in files]})']
        elif k == "TextInput":
            rows = [["" if x is None else x for x in r] for r in ir["rows"]]
            widths = [max([len(col)] + [len(r[j]) if j < len(r) else 0 for r in rows]) for j, col in enumerate(ir["columns"])]
            line = lambda cells: "  ".join(x.ljust(w) for x, w in zip(cells, widths)).rstrip()
            c["does"] = "Starts from a table typed into the workflow (%d rows)." % len(rows)
            c["rule"] = [line(ir["columns"]), line(["-" * w for w in widths])] + [line(r + [""] * (len(widths) - len(r))) for r in rows]
            for name in ir["columns"]:
                write(name, ir.get("types", {}).get(name), "typed in the workflow")
            c["py"] = self.load_lines(i)
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
                text = vec(e, "df.apply(%s_%d, axis=1)  # generated code; no one-line form" % (fx, n))
                c["py"] += pre + ['df["%s"] = %s' % (name, text)]
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
            target = '"%s" + c' % ir["addon"] if ir["copy"] and ir["prefix"] else "c + %r" % ir["addon"] if ir["copy"] else "c"
            c["py"] = ["for c in %s:" % _cols(cols)] + ["    " + x for x in pre] + ["    df[%s] = %s" % (target, v)] if v else \
                ["# no one-line pandas form for this rule; write it by hand (the generated function %s has the logic)" % fx]
        elif k == "Filter":
            if ir["mode"] == "simple":
                lines, cond_py = [simple_words(ir)], simple_py(ir)
                c["reads"] = [ir["field"]]
            else:
                e = ir["expr"]
                lines = expr.decision(e["ast"], WIDTH) if e["ast"] is not None else [e["text"]]
                cond_py = vec(e, None)
                c["reads"] = expr.fields(e["ast"]) if e["ast"] is not None else []
            cond_pre = list(pre)
            cond_py = cond_py or "df.apply(%s, axis=1)  # generated code" % (fx if ir["mode"] != "simple" else "test")
            c["rule"] = lines if len(lines) > 1 or len(lines[0]) > 60 else []
            test = "the test in Rule holds" if c["rule"] else lines[0]
            if used >= {"True", "False"}:
                c["does"] = "Splits the rows: those where %s go to True, the others to False." % test
                c["py"] = cond_pre + ["m = " + cond_py, "%s, %s = df[m], df[~m]" % (self.var(i, "True"), self.var(i, "False"))]
            elif used == {"False"}:
                c["does"] = "Keeps only the rows where %s is not true (the False output)." % test.replace(" holds", "")
                c["py"] = cond_pre + ["df = df[~(%s)]" % cond_py]
            else:
                c["does"] = "Keeps only the rows where %s." % test
                c["py"] = cond_pre + ["df = df[%s]" % cond_py]
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
            ren = {f: r for f, sel, r, _, _ in ir["fields"] if f != "*Unknown" and sel and r and r != f}
            drop = [f for f, sel, *_ in ir["fields"] if f != "*Unknown" and not sel]
            c["py"] = ['right = %s.rename(columns=lambda c: "Right_" + c if c in df.columns else c)  # as Alteryx: only '
                       'clashing names get Right_' % rvar,
                       'm = df.merge(right, how="outer", left_on=%s, right_on=[c if c in right.columns else "Right_" + c for c '
                       'in %s], indicator=True)' % (_cols(ir["left"]), _cols(ir["right"]))]
            if drop or ren:
                c["py"].append("m = m%s%s" % ('.drop(columns=%s, errors="ignore")' % _cols(drop) if drop else "",
                                              ".rename(columns={%s})" % ", ".join("%s: %s" % (_q(a), _q(b)) for a, b in ren.items())
                                              if ren else ""))
            for a, flag in (("Join", "both"), ("Left", "left_only"), ("Right", "right_only")):
                if a in used:
                    keep = {"Join": "", "Left": "[[c for c in df.columns if c in m.columns]]",
                            "Right": "[[c for c in right.columns if c in m.columns]]"}[a]
                    c["py"].append('%s = m[m["_merge"] == "%s"]%s' % (self.var(i, a), flag, keep or '.drop(columns="_merge")'))
        elif k == "AppendFields":
            src = other("S")
            svar = self.var(src[0][0], src[0][1]) if src else "source"
            c["does"] = "Adds the columns of the Source input to every row (every row × every Source row)."
            c["rule"] = self.L.select_words(ir["fields"], ir["unknown"], join=True)
            for f, sel, r, typ, size in ir["fields"]:
                if f != "*Unknown" and sel and r and r != f:
                    write(r, typ, "renamed from %s" % f)
            ren = {f: r for f, sel, r, _, _ in ir["fields"] if f != "*Unknown" and sel and r and r != f}
            drop = [f for f, sel, *_ in ir["fields"] if f != "*Unknown" and not sel]
            keep = [f for f, sel, *_ in ir["fields"] if f != "*Unknown" and sel]
            c["py"] = ['source = %s.rename(columns=lambda c: "Source_" + c if c in df.columns else c)  # as Alteryx: clashing '
                       'names get Source_' % svar]
            if not ir["unknown"]:
                c["py"].append("source = source[[c for c in %s if c in source.columns]]  # only the columns ticked" % _cols(keep))
            c["py"].append('df = df.merge(source, how="cross")%s%s' % (
                '.drop(columns=%s, errors="ignore")' % _cols(drop) if drop else "",
                ".rename(columns={%s})" % ", ".join("%s: %s" % (_q(a), _q(b)) for a, b in ren.items()) if ren else ""))
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
            c["py"] = ['df = df.drop(columns=["%s"], errors="ignore")  # if an earlier step made one' % ir["name"],
                       'df.insert(%s, "%s", %s)' % ("len(df.columns)" if ir["last"] else "0", ir["name"], vals)]
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
                    c["py"] = ['df["%s"] = df["%s"].map(lambda v: next((r["%s"] for _, r in %s.iterrows() if pd.notna(r[%s]) and %s), None))'
                               % (a, ir["find"], a, svar, _q(ir["search"]), test) for a in ir["append"]]
            else:
                c["does"] = "In [%s], replaces text that matches a Source row's [%s] with that row's [%s] (%s)." % (
                    ir["find"], ir["search"], ir["replace"], case)
                write(ir["find"], None, "text replaced from the Source input")
                c["py"] = ['for _, r in %s.dropna(subset=[%s]).iterrows(): df["%s"] = df["%s"].str.replace(str(r[%s]), str(r[%s]), '
                           'regex=False)  # Alteryx replaces only the first matching Source row' % (
                               svar, _q(ir["search"]), ir["find"], ir["find"], _q(ir["search"]), _q(ir["replace"]))]
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
                test_py = vec(cond, "df.apply(test, axis=1)")
                c["py"] = pre + ["if (%s).any(): raise RuntimeError(%s)" % (test_py, repr(text))]
        elif k == "Output":
            path, sheet = _file(ir["file"])
            c["does"] = "Writes the rows to %s`%s`." % ("sheet **%s** of " % sheet if sheet else "", path)
            c["py"] = ["%s = df  # the rows this step saves; the notebook's last cell writes them to OUT" % self.var(i)]
        elif k in BLOCKERS:
            c["does"] = "Waits until all rows have arrived, then releases output 1, and output 2 only after everything fed " \
                        "by output 1 has finished."
            in_by_no = {re.sub(r"\D", "", e[3]): e for e in ins}
            for a in sorted(used, key=lambda a: int(re.sub(r"\D", "", a) or 0)):
                e = in_by_no.get(re.sub(r"\D", "", a)) or (ins[0] if len(ins) == 1 else None)
                same = e is not None and e[0] in self.L.stages[self.L.stage_of[i]]
                c["py"].append("%s = %s  # the data passes through" % (self.var(i, a), self.var(e[0], e[1]) if e and not same else "df"))
            c["py"].append("# run what follows output 1 before what follows output 2")
        elif k == "RunCommand":
            cmd = " ".join(x for x in (ir["command"], ir["args"]) if x)
            c["does"] = "Runs `%s` outside Alteryx%s: write this step by hand." % (
                cmd, " (in folder `%s`)" % ir["folder"] if ir["folder"] else "")
            if ir["files"]:
                c["notes"].append("Files named in its settings: %s." % ", ".join("`%s`" % f for f in ir["files"]))
            c["rule"] = ["program:   " + (ir["command"] or "?"), "arguments: " + (ir["args"] or "—")] + \
                (["folder:    " + ir["folder"]] if ir["folder"] else []) + ["file:      " + f for f in ir["files"]]
            c["py"] = ["# runs %s outside Alteryx (often a .vbs or .bat that drives an Excel macro): write its work in "
                       "Python by hand" % cmd,
                       "# subprocess.run(%r, check=True)  # or run the same program: uncomment" % ([ir["command"]] +
                                                                                                ir["args"].split())]
        else:
            c["does"] = "Runs **%s**; its settings are below." % k.replace("Macro:", "the macro ")
            c["rule"] = [ir.get("raw", "")]
            c["py"] = ["# no pandas form; write this step by hand"]
        c["reads"] = list(dict.fromkeys(x for x in c["reads"] if x))
        c["py"] = [("# " + x if "generated code" in x and not x.lstrip().startswith("#") else x) for x in c["py"]]
        stage = self.L.stages[self.L.stage_of[i]]
        if k in ("Join", "Unique") and len(used) == 1 and i != stage[-1]:
            c["py"].append("df = %s" % self.var(i, next(iter(used))))           # the branch goes on from that output
        if k in MULTI and i == stage[-1]:                                     # name every output that later steps use
            for a in sorted(used):
                if not re.search(r"\b%s\b" % re.escape(self.var(i, a)), "\n".join(c["py"])):
                    c["py"].append("%s = df" % self.var(i, a))
        prep = []
        for e in ins:                                     # a folded read that also chooses or cleans columns
            if e[0] in self.fold_of and i not in self.fold_of:      # (only for the step that uses it)
                ids, v = self.chain(e[0])
                if len(ids) > 1 and v not in "".join(prep):
                    prep.append("%s = %s.copy()  # %s, then:" % (v, self.src_of[ids[0]], _md_plain(self.read_label(e[0]))))
                    for x in ids[1:]:
                        prep += [re.sub(r"\bdf\b", v, line) for line in self.card(x)["py"]]
        c["py"] = prep + c["py"]
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
                       "in the order they run. Each starts with the files it opens and saves: file › sheet › first row "
                       "or cells.",
                       "**Opening a file**: in Alteryx every file read is its own tool box. Here it is shown inside the "
                       "step that uses it (\"matches its rows with `Config.xlsm` › sheet `Clients` on [ID]\"); only a "
                       "file that starts a flow of rows gets its own row (\"opens …\").",
                       "**Branches**: inside a container, the rows flow along branches. A branch is a chain of tools with "
                       "no split or merge in it; it is named after its first tool's note or its filter test. Branch 3.4 "
                       "is the 4th branch of container 3.",
                       "**Steps**: each branch is one table, a row per tool as it sits on the canvas: step number (3.14 "
                       "is step 14 of container 3), the tool's name in Designer and its note, what it does, the columns "
                       "it adds or changes, and the next step. A filter's outputs are T (true) and F (false); a join's "
                       "are J (matched), L (left rows with no match) and R (right rows with no match). An output that "
                       "nothing uses is dropped.",
                       "**Under each table**, one folded line per step holds its full rule (`when … → …` lines are read "
                       "top down; the first true condition gives the value) and the same step in pandas, to paste into "
                       "the steps notebook: `df` is the table flowing through the branch, `s2_7` is step 2.7's output "
                       "and `s2_7_true` its True output; `src_…` is a file as loaded in the notebook's Load cell, and "
                       "`FOLDER` the workflow's folder. It also says whether the generated code converts the step "
                       "exactly and names its function. ⚠ marks a step that is not converted.",
                       ]], False),
                   ("p", "The pandas lines use two helpers, because Alteryx compares text ignoring case and treats null "
                         "and the empty string separately:"),
                   ("pre", HELPERS),
                   ("h", 2, "Summary", "summary"),
                   ("steps", [{"text": x} for x in [
                       "%d containers%s, %d tools, %d branches." % (
                           len(self.tops), ", %d disabled in the saved file (documented anyway)" % len(self.off & set(self.tops))
                           if self.off & set(self.tops) else "", len(live), len({b for b in self.bid.values()})),
                       "**Python:** %d steps convert exactly, %d with a stated assumption, %d are not converted." % (
                           status["exact"], status["caveat"], status["stub"]),
                       "**Before relying on it:** see [[check|the last section]]."]], False)]

        rows, nodes, edges = [], [], []
        for key in self.tops:
            reads, writes, runs, _ = self.files(key)
            reads = list(dict.fromkeys(reads))
            empty = not any(self.top_of[i] == key for i in self.order)
            disabled = key in self.off
            n_tools = sum(1 for i in self.order if self.top_of[i] == key)
            fed = list(dict.fromkeys(self.cref(a) for a, b, _ in links if b == key))
            feeds = list(dict.fromkeys(self.cref(b) for a, b, _ in links if a == key))
            base = lambda xs: ", ".join(dict.fromkeys(place_text(x, bold=False).replace("`" + place(x)[0] + "`", os.path.basename(
                place(x)[0].replace("\\", "/"))) for x in xs)) or "—"
            rows.append([self.cref(key), ("no: disabled in the saved file" + (" (left out: --enabled-only)" if empty else
                                                                               " (documented anyway)")) if disabled else "yes", n_tools,
                         len({self.bid[s] for s in self.bid if self.top_of[L.stages[s][0]] == key}),
                         ", ".join([base([r for r in reads if not r.startswith(("files named", "folder "))])] * any(
                             not r.startswith(("files named", "folder ")) for r in reads) +
                                   [r for r in reads if r.startswith(("files named", "folder "))]) or "—",
                         base(writes) + ("; runs %d command%s" % (len(runs), "" if len(runs) == 1 else "s") if runs else ""),
                         ", ".join(fed) or "—", ", ".join(feeds) or "—"])
            nodes.append({"id": "c%d" % self.cnum[key], "label": "%d. %s" % (self.cnum[key], self.cname(key)),
                          "sub": ("disabled · %d tools" if disabled else "%d tools") % n_tools,
                          "kind": "stub" if disabled else "stage",
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
            toc.append({"text": self.cref(key) + (" (disabled in the saved file)" if key in self.off else ""), "children": kids})
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
            return ("details", 2, title + " — disabled in the saved file", "c%d" % k, [
                ("p", "Disabled in the saved file, and left out because the guide was made with --enabled-only: its %d "
                      "tools are not described. Kinds: %s." % (n, ", ".join("%s %d" % kv for kv in kinds.most_common())))],
                    False)
        reads, writes, runs, _ = self.files(key)
        reads, writes = list(dict.fromkeys(reads)), list(dict.fromkeys(writes))
        inner, facts = [], []
        frows = []
        for i in tools:
            t = self.t[i]
            if t.kind in ("Input", "Output", "DynamicInput"):
                path, sheet, cells, line = place(t.ir["file"], t.ir["options"])
                what = "opens each file listed in [%s], like" % t.ir["field"] if t.kind == "DynamicInput" else \
                    "opens" if t.kind == "Input" else "saves"
                extra = [x for x in (("from row %d" % line) if line else "",
                                     OUT_MODE.get(t.ir["options"].get("OutputOption", ""), "") if t.kind == "Output" else "") if x]
                at = self.ref(self.folded[self.fold_of[i]][0]) + " (inside)" if i in self.fold_of else self.ref(i)
                frows.append([at, what, "`%s`" % path, sheet or "—", cells.upper() or "all", "; ".join(extra)])
            elif t.kind == "Directory":
                frows.append([self.ref(i), "lists", "`%s`" % t.ir["dir"], "—", "—", "files named `%s`" % t.ir["spec"]])
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
        if off and key not in self.off:
            facts.append({"text": "**Disabled in the saved file, inside it:** " + "; ".join(" > ".join(p[1:]) for p in off) +
                                  " (documented below anyway)"})
        if key in self.off:
            facts.insert(0, {"text": "**Disabled in the saved file.** Alteryx skips this container until someone "
                                     "enables it (teams often run one container at a time this way). It is documented "
                                     "and converted like the others; in the generated code run it with `only=%d`." % k})
        if frows:
            inner += [("p", "**Files it opens and saves:**"), ("table", ["Step", "", "File", "Sheet", "Cells", "Notes"], frows)]
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
        return ("details", 2, "%s%s — %d tool%s, %d branch%s" % (
            title, " — disabled in the saved file" if key in self.off else "", len(tools), "" if len(tools) == 1 else "s",
            len(branches), "" if len(branches) == 1 else "es"), "c%d" % k, inner, False)

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
        body += self.step_table(ids)
        return ("details", 4, head, "b" + self.bid[s].replace(".", "-"), body, False)

    def cond_text(self, t, n=70):
        ir = t.ir
        if ir["mode"] == "simple":
            return simple_words(ir)
        e = ir["expr"]
        return _short(expr.readable(e["ast"]) if e["ast"] is not None else e["text"], n)

    def other(self, i, letter):
        """References to the steps feeding input L/R/S of a two-input tool."""
        return ", ".join(self.src(e) for e in self.wf.ins.get(i, []) if e[3][:1].upper() == letter) or "nothing connected"

    def short(self, i):
        """What the step does, in one line, as the action a person would take in Excel."""
        t, ir, k = self.t[i], self.t[i].ir, self.t[i].kind
        used = set(self.outs(i))
        if k == "Input":
            return "opens " + file_text(ir["file"], ir["options"]).replace(" › ", ", ")
        if k == "Output":
            mode = OUT_MODE.get(ir["options"].get("OutputOption", ""), "")
            return "saves the table to " + file_text(ir["file"], ir["options"]).replace(" › ", ", ") + (" (%s)" % mode if mode else "")
        if k == "DynamicInput":
            return "opens %s, and stacks them" % self.dyn_words(i)
        if k == "Directory":
            return "lists the files in folder `%s` named `%s`" % (ir["dir"], ir["spec"])
        if k == "TextInput":
            return "starts from a typed table: %d rows of %s" % (len(ir["rows"]), _list(ir["columns"], 4))
        if k == "Select":
            real = [f for f in ir["fields"] if f[0] != "*Unknown"]
            ren = [(f, r) for f, sel, r, _, _ in real if sel and r and r != f]
            drop = [f for f, sel, *_ in real if not sel]
            retype = [(r or f, typ) for f, sel, r, typ, _ in real if sel and typ]
            keep = [r or f for f, sel, r, _, _ in real if sel]
            parts = []
            if not ir["unknown"]:
                parts.append("keeps only the columns %s" % _list(keep, 5))
            if ren:
                parts.append("renames " + ", ".join("[%s] to [%s]" % p for p in ren[:3]) + (" +%d more" % (len(ren) - 3) if len(ren) > 3 else ""))
            if drop and ir["unknown"]:
                parts.append("deletes the columns " + _list(drop, 4))
            if retype:
                parts.append("sets the type of " + _list([n for n, _ in retype], 3))
            return "; ".join(parts) or "leaves the columns as they are"
        if k == "Formula":
            have, parts = self.input_columns(i), []
            for name, _, _, e in ir["fields"]:
                verb = "fills column" if have is None else ("overwrites column" if name.lower() in have else "adds column")
                n = 60 if len(ir["fields"]) == 1 else 45
                one = expr.decision(e["ast"], WIDTH) if e["ast"] is not None else [e["text"]]
                parts.append("%s **%s** = %s" % (verb, name, one[0]) if len(one) == 1 and len(one[0]) <= n else
                             "%s **%s** (see the rule)" % (verb, name))
            return "; ".join(parts[:3]) + (" … (%d columns)" % len(parts) if len(parts) > 3 else "")
        if k == "MultiFieldFormula":
            return "applies one formula to each of the columns %s" % _list(ir["fields"], 4)
        if k == "Filter":
            cond = self.cond_text(t)
            if used >= {"True", "False"}:
                return "splits the rows in two: where %s (T), and the rest (F)" % cond
            if used == {"False"}:
                return "removes the rows where %s" % cond
            return "keeps only the rows where %s" % cond
        if k == "Join":
            keys = ", ".join("[%s]" % a if a == b else "[%s] = [%s]" % (a, b) for a, b in zip(ir["left"], ir["right"]))
            left = self.other(i, "L")
            start = "takes %s and " % left if any(e[0] in self.fold_of for e in self.wf.ins.get(i, []) if e[3][:1] == "L") else ""
            return "%smatches its rows with %s on %s" % (start, self.other(i, "R"), keys)
        if k == "AppendFields":
            return "adds the columns of %s to every row" % self.other(i, "S")
        if k == "Union":
            return "stacks the tables %s (columns matched %s)" % (", ".join(self.src(e) for e in self.wf.ins.get(i, [])) or "—",
                                                               "by name" if ir["mode"] == "ByName" else "by position")
        if k == "Summarize":
            group = [f for f, a, _, _ in ir["fields"] if a == "GroupBy"]
            aggs = [r or "%s_%s" % (a, f) for f, a, r, _ in ir["fields"] if a != "GroupBy"]
            return ("groups the rows by %s and calculates %s (like a pivot table)" % (_list(group, 4), _list(aggs, 4, True))
                    if group else "calculates one row of totals: %s" % _list(aggs, 4, True))
        if k == "Sort":
            return "sorts by " + ", ".join("[%s] %s" % (f, "A→Z" if a else "Z→A") for f, a in ir["fields"])
        if k == "Unique":
            return "removes duplicate rows by %s (keeps the first)" % _list(ir["fields"], 4)
        if k == "Sample":
            n, g = ir["n"], ir["group"]
            what = {"First": "keeps the first %s rows", "Last": "keeps the last %s rows", "Skip": "skips the first %s rows",
                    "Sample": "keeps 1 row in every %s"}.get(ir["mode"], ir["mode"] + " %s") % n
            if str(n) == "1" and ir["mode"] in ("First", "Last", "Skip"):
                what = what.replace(" 1 rows", " row")
            return what + (" of each %s" % _list(g, 3) if g else "")
        if k == "RecordID":
            return "adds a row-number column **%s**, from %s" % (ir["name"], ir["start"])
        if k == "Transpose":
            return "unpivots %s into Name / Value rows; keeps %s" % (_list(ir["data"], 3), _list(ir["keys"], 3) or "nothing else")
        if k == "FindReplace":
            how = {"FindWhole": "equals", "FindBegin": "starts", "FindAny": "appears in"}.get(ir["mode"], ir["mode"])
            src = self.other(i, "S")
            if ir["replace_mode"] == "Append":
                return "looks up [%s] in %s (where its [%s] %s [%s]) and adds %s" % (
                    ir["find"], src, ir["search"], how, ir["find"], _list(ir["append"], 4, True))
            return "replaces text in [%s] using %s: its [%s] becomes its [%s]" % (ir["find"], src, ir["search"], ir["replace"])
        if k == "DateTime":
            return "adds column **%s** = [%s] %s `%s`" % (ir["out"], ir["field"], "as text" if ir["to_string"] else "read as a date",
                                                          ir["format"])
        if k == "Cleanse":
            opts = [CLEANSE_WORDS[o] for o, v in ir["options"].items() if v and o in CLEANSE_WORDS]
            return "cleans the columns %s: %s" % (_list(ir["fields"], 4), "; ".join(opts) or "no option ticked")
        if k == "Message":
            stop = ir["type"].lower().startswith("error")
            return ("stops with an error" if stop else "writes a message") + (" when a row passes the test" if ir["filter"] else "")
        if k in BLOCKERS:
            return "waits for all rows; what follows output 2 runs after output 1 is done"
        if k == "RunCommand":
            return "runs `%s` outside Alteryx: write this step by hand" % _short(
                " ".join(x for x in (ir["command"], ir["args"]) if x), 60)
        if k == "TextToColumns":
            cfg = t.cfg
            delim = cfg.find("Delimeters")
            delim = delim.get("value") if delim is not None else cfg.findtext("Delimeters", "")
            n = cfg.find("NumFields")
            n = n.get("value") if n is not None else ""
            return "splits [%s] at `%s`%s (Text to Columns)" % (cfg.findtext("Field", "?"), delim, " into %s columns" % n if n else "")
        if k == "RegEx":
            rx = t.cfg.find("RegExExpression")
            return "applies the pattern `%s` to [%s] (%s)" % (_short(rx.get("value") if rx is not None else "?", 40),
                                                            t.cfg.findtext("Field", "?"), t.cfg.findtext("Method", "?"))
        return "settings in the step's details"

    def dyn_words(self, i):
        ir = self.t[i].ir
        like = file_text(ir["file"], ir["options"]).replace(" › ", ", ")
        tpl = place(ir["file"])[0]
        folder, name = os.path.split(tpl.replace("\\", "/"))
        return {"path": "every file listed in column [%s] (the column holds the whole path; sheet as in the template %s)"
                        % (ir["field"], like),
                "name": "the file named in column [%s], in folder `%s` (like %s)" % (ir["field"], folder or ".", like),
                "prefix": "the template %s with column [%s] put before its file name" % (like, ir["field"]),
                "suffix": "the template %s with column [%s] added to its file name" % (like, ir["field"])}.get(
            ir.get("kind"), "the files given by column [%s] (mode %s/%s, not known to the converter; template %s)"
            % (ir["field"], ir.get("mode"), ir.get("how"), like))

    def new_cols(self, i):
        w = [x[0] for x in self.cards[i]["writes"]]
        if self.t[i].kind in SOURCES + ("DynamicInput",):
            return "%d columns" % len(w) if w else ""
        return _list(w, 5, True) if w else ""

    def next_text(self, i):
        t, groups = self.t[i], self.outs(i)
        if t.kind in ("Filter", "Join", "Unique"):
            names = {"Filter": ("True", "False"), "Join": ("Join", "Left", "Right"), "Unique": ("Unique", "Duplicates")}[t.kind]
            short = {"True": "T", "False": "F", "Join": "J", "Left": "L", "Right": "R", "Unique": "U", "Duplicates": "D"}
            used = ["%s → %s" % (short[a], ", ".join(self.ref(d) for d in groups[a])) for a in names if groups.get(a)]
            dropped = [short[a] for a in names if not groups.get(a)]
            return "; ".join(used + (["%s dropped" % ", ".join(dropped)] if dropped else []))
        if len(groups) > 1:
            return "; ".join("%s → %s" % (re.sub(r"\D+", "", a) or a, ", ".join(self.ref(d) for d in v))
                             for a, v in sorted(groups.items()))
        dst = [d for v in groups.values() for d in v]
        if dst:
            return ", ".join(self.ref(d) for d in dst)
        return "—" if t.kind in ("Output", "RunCommand", "Message") else "ends (rows dropped)"

    def status(self, i):
        t = self.t[i]
        if t.status == "stub":
            return "NOT CONVERTED: " + "; ".join(t.reasons())
        if t.status == "caveat":
            text = "converted" + (", assuming A%d" % self.assume[t.kind] if t.kind in self.assume else "")
            return text + ("; note: " + "; ".join(t.caveats) if t.caveats else "")
        return "exact"

    def fold(self, i):
        """One folded line per step: status in the summary; the rule, then the pandas lines, below it."""
        t, c = self.t[i], self.cards[i]
        lines = list(c["rule"])
        if t.kind in SOURCES + ("DynamicInput",) and c["writes"]:
            lines += ["# columns: " + x for x in _wrap(", ".join("%s (%s)" % (n, typ) if typ else n
                                                                  for n, typ, *_ in c["writes"]), 100)]
        lines += ["# note: " + x for x in _wrap(" ".join(c["notes"]), 100)] if c["notes"] else []
        py = c["py"]
        status = self.status(i) + ("" if t.kind in BLOCKERS else " · `%s`" % self.fn[i])
        summary = "**%s** %s #%s · %s" % (self.sid[i], designer(t.kind), i,
                                         "**not converted**" + status[len("NOT CONVERTED"):] if t.status == "stub" else status)
        if not lines and len(py) == 1 and len(py[0]) <= 100 and "`" not in py[0]:
            return {"anchor": "r" + self.sid[i].replace(".", "-"), "summary": summary + " · `%s`" % py[0], "lines": []}
        if lines and py:
            lines.append("# pandas:")
        return {"anchor": "r" + self.sid[i].replace(".", "-"), "summary": summary, "lines": lines + py}

    def step_table(self, ids):
        rows, anchors = [], []
        for i in ids:
            t = self.t[i]
            tool = designer(t.kind) + (" “%s”" % _short(t.note, 40) if t.note else "")
            rows.append(["[[r%s|%s]]" % (self.sid[i].replace(".", "-"), self.sid[i]), tool + (" ⚠" if t.status == "stub" else ""),
                         self.guard(i, self.short, i), self.guard(i, self.new_cols, i), self.guard(i, self.next_text, i)])
            anchors.append("s" + self.sid[i].replace(".", "-"))
        return [("table", ["Step", "Tool", "What it does", "New / changed columns", "Next"], rows, anchors),
                ("folds", [self.safe_fold(i) for i in ids])]

    def safe_fold(self, i):
        try:
            return self.fold(i)
        except Exception as exc:  # noqa: BLE001 - reported
            return {"anchor": "r" + self.sid[i].replace(".", "-"), "lines": ["setting: " + x for x in describe(self.t[i])],
                    "summary": "**%s** %s #%s · could not describe: %s" % (self.sid[i], designer(self.t[i].kind), i,
                                                                           self.failed(i, exc))}

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
            ("p", mapping + " Tool IDs: %s." % _id_map(tools, orig)),
            ("folds", [{"anchor": "r" + self.sid[tools[0]].replace(".", "-"),
                        "summary": "**Steps %s** in pandas: the same lines as there, with this copy's tables" % span(tools),
                        "lines": [x for i in tools for x in ["# step %s %s" % (self.sid[i], designer(self.t[i].kind))] +
                                  self.cards[i]["py"]]}])], False)

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
            items.append({"text": "**Not converted to Python** (%d step%s): the generated code stops there with an error "
                                  "until the step is written by hand, or until the test notebook gives it a stand-in."
                                  % (len(stubs), "" if len(stubs) == 1 else "s"),
                          "children": [{"text": "%s, %d step%s (%s): %s" % (kind, len(ts), "" if len(ts) == 1 else "s",
                                                                           ", ".join(self.step_ref(t.id) for t in ts), reason)}
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
            items.append({"text": "**Alteryx behaviours the Python assumes** (steps refer to them as A1, A2…; settle them "
                                  "once with the probe workflow):",
                          "children": [{"text": "**A%d** — %s: %s" % (n, k, STATUS[k][1]), "anchor": "a%d" % n}
                                       for k, n in self.assume.items()]})
        waits = [t.id for t in live if t.kind in BLOCKERS]
        if waits:
            items.append({"text": "**Run order:** Block Until Done at steps %s: a rewrite must finish what follows output "
                                  "1 before starting what follows output 2." % ", ".join(self.ref(i) for i in waits)})
        ext = [t.id for t in live if t.kind == "RunCommand"]
        if ext:
            items.append({"text": "**%d external program%s to replace** (Run Command): Alteryx starts them; the generated "
                                  "code only logs them, so write each by hand: %s." % (
                                      len(ext), "" if len(ext) == 1 else "s", ", ".join(self.step_ref(i) for i in ext))})
        if self.wf.dropped:
            items.append({"text": "**Run order: %d Block Until Done preference%s could not be honoured**, because the "
                                  "connections require the opposite order:" % (len(self.wf.dropped),
                                                                              "" if len(self.wf.dropped) == 1 else "s"),
                          "children": [{"text": "could not honour: step %s before step %s" % (self.step_ref(b), self.step_ref(a))}
                                       for b, a in self.wf.dropped[:50]] +
                                      ([{"text": "… and %d more" % (len(self.wf.dropped) - 50)}] if len(self.wf.dropped) > 50
                                       else [])})
        if self.wf.cycle_edges:
            items.append({"text": "**The connections form a loop**, which Alteryx would not run; the order of these steps "
                                  "is a guess:",
                          "children": [{"text": "step %s → step %s" % (self.step_ref(e[0]), self.step_ref(e[2]))}
                                       for e in self.wf.cycle_edges]})
        if self.wf.missing_ends:
            items.append({"text": "**%d connection%s to a tool that is not in the file** (ignored): %s" % (
                len(self.wf.missing_ends), "" if len(self.wf.missing_ends) == 1 else "s",
                ", ".join("#%s → #%s" % (e[0], e[2]) for e in self.wf.missing_ends[:20]))})
        if self.errors:
            by = defaultdict(list)
            for typ, where, i, kind in self.errors:
                by[(typ, where)].append(i)
            items.append({"text": "**The converter failed on %d step%s** (please report; each step is still listed, "
                                  "with its settings):" % (len({e[2] for e in self.errors}),
                                                          "" if len({e[2] for e in self.errors}) == 1 else "s"),
                          "children": [{"text": "%s in %s: steps %s" % (typ, where, ", ".join(
                              self.step_ref(i) for i in dict.fromkeys(ids)))} for (typ, where), ids in by.items()]})
        return [("h", 2, "Before relying on this", "check"), ("steps", items or [{"text": "Nothing to flag."}], False)]

    def step_ref(self, i):
        """A step number to cite; a file read shown inside a step is cited as that step."""
        if i in self.fold_of:
            return "%s (the file it reads)" % self.ref(self.folded[self.fold_of[i]][0])
        if i not in self.sid:
            return "#%s" % i
        return self.ref(i)


def _md_plain(text):
    return re.sub(r"\[\[[\w-]+\|([^\]]+)\]\]", r"\1", text).replace("`", "").replace("**", "")


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width, break_on_hyphens=False, break_long_words=False) or [""]


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
