"""Read an Alteryx workflow (.yxmd, or the same XML saved as .txt) into a tool graph.

Standard library only; the file is opened read-only and nothing else is touched.
Each tool becomes a Tool with a plain-dict `ir` (its settings, parsed once) that both the
specification writer and the code generator use.
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from collections import defaultdict

from . import expr

END_TAG = "</AlteryxDocument>"
IGNORED = {"Browse", "BrowseV2", "TextBox", "ToolContainer"}

# How far each kind converts. "exact": follows documented behaviour, no open question.
# "caveat": converts, with the stated open question. Everything else becomes a raising stub.
STATUS = {
    "Select": ("exact", ""),
    "Formula": ("exact", ""),
    "Filter": ("exact", ""),
    "RecordID": ("exact", ""),
    "TextInput": ("exact", ""),
    "Transpose": ("exact", ""),
    "AppendFields": ("exact", ""),
    "MultiFieldFormula": ("exact", ""),
    "Sample": ("exact", ""),
    "BlockUntilDone": ("caveat", "data passes through; only the run order is reproduced"),
    "ParallelBlockUntilDone": ("caveat", "data passes through; only the run order is reproduced"),
    "Input": ("caveat", "column types depend on Alteryx's file driver; the types recorded in the workflow are applied when present"),
    "DynamicInput": ("caveat", "as Input; 'read a list of data sources' (whole path, file name, prefix or suffix)"),
    "RunCommand": ("caveat", "runs a program outside Alteryx; the generated code only logs it, so write it by hand"),
    "Directory": ("caveat", "file times come from the operating system"),
    "Join": ("caveat", "row order of the three outputs and null = null on keys are not documented"),
    "Union": ("caveat", "the type chosen when inputs disagree, and the order of the inputs, are not documented"),
    "Summarize": ("caveat", "output order, Sum of no values and Concat of empty values are not documented"),
    "Sort": ("caveat", "position of nulls and the order of upper/lower case are not documented"),
    "Unique": ("caveat", "output order is assumed to be the input order"),
    "FindReplace": ("caveat", "which Source row wins when several match is not documented (first in Source order assumed)"),
    "DateTime": ("caveat", "English only; output type for a date-only format is assumed to be Date"),
    "Output": ("caveat", "cell formats written by Alteryx are not reproduced; writes only with Context(write=True)"),
    "Cleanse": ("caveat", "the check-box numbers are mapped to options from memory of the standard macro; confirm against Cleanse.yxmc"),
    "Message": ("caveat", "an Error message stops the run; other kinds are logged"),
}


class WorkflowError(Exception):
    pass


def load(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
    a, b = text.find("<AlteryxDocument"), text.rfind(END_TAG)
    if a < 0 or b < 0:
        raise WorkflowError("%s: no <AlteryxDocument> ... </AlteryxDocument> block found" % path)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text[a:b + len(END_TAG)])
    if "<!DOCTYPE" in text or "<!ENTITY" in text:          # a workflow never declares entities
        raise WorkflowError("%s: refusing a file that declares a DOCTYPE or ENTITY" % path)
    return ET.fromstring(text), hashlib.sha256(raw).hexdigest()


def flat(s):
    s = re.sub(r"\s*\r?\n\s*", " ", (s or "").strip())
    return re.sub(r"[ \t]+", " ", s)


def _val(cfg, tag, default=""):
    """Setting stored either as <Tag value="x"/> or <Tag>x</Tag>."""
    e = cfg.find(tag)
    if e is None:
        return default
    v = e.get("value")
    return v if v is not None else (e.text or default)


def _true(s):
    return str(s).strip().lower() == "true"


def _kind(plugin, macro):
    last = plugin.split(".")[-1] if plugin else ""
    name = last or re.split(r"[\\/]", macro)[-1]
    low = name.lower()
    if "parallelblockuntildone" in low:
        return "ParallelBlockUntilDone"
    if "cleanse" in low:
        return "Cleanse"
    if macro and not last:
        return "Macro:" + re.sub(r"\.yxmc$", "", name, flags=re.I)
    return {"DbFileInput": "Input", "DbFileOutput": "Output", "AlteryxSelect": "Select", "MessageTool": "Message",
            "LockInInput": "Input", "LockInOutput": "Output"}.get(name, name or "Unknown")


class Tool:
    def __init__(self, tid, kind, cfg, note, path, disabled, meta):
        self.id, self.kind, self.cfg, self.note = tid, kind, cfg, note
        self.path, self.disabled, self.meta = path, disabled, meta
        self.ir, self.problems, self.caveats = {}, [], []

    @property
    def where(self):
        return " > ".join(self.path) or "(no container)"

    @property
    def status(self):
        if self.kind in IGNORED:
            return "skip"
        if self.problems or self.kind not in STATUS:
            return "stub"
        return "caveat" if self.caveats or STATUS[self.kind][0] == "caveat" else "exact"

    def reasons(self):
        if self.status == "stub":
            return self.problems or ["tool kind %s has no converter" % self.kind]
        base = [STATUS[self.kind][1]] if self.kind in STATUS and STATUS[self.kind][1] else []
        return base + self.caveats


class Workflow:
    """enabled_only: leave out the tools of disabled containers (by default they are read like the others, because
    teams often run one container at a time by disabling the rest before saving)."""

    def __init__(self, path, enabled_only=False):
        self.source, self.enabled_only = path, enabled_only
        root, self.sha256 = load(path)
        self.tools, self.containers, self.own_off = {}, [], {}
        self._walk(root.find("Nodes"), [], False)
        self.edges = []                     # (src, src_anchor, dst, dst_anchor, name)
        for c in root.findall("Connections/Connection"):
            o, d = c.find("Origin"), c.find("Destination")
            if o is not None and d is not None:
                self.edges.append((o.get("ToolID"), o.get("Connection") or "Output", d.get("ToolID"),
                                   d.get("Connection") or "Input", c.get("name") or ""))
        for t in self.tools.values():
            _parse(t)
        self.live = {i for i, t in self.tools.items() if t.kind not in IGNORED and not (enabled_only and t.disabled)}
        self.missing_ends = [e for e in self.edges if e[0] not in self.tools or e[2] not in self.tools]
        self.ins, self.outs = defaultdict(list), defaultdict(list)
        for e in self.edges:
            if e[0] in self.live and e[2] in self.live:
                self.ins[e[2]].append(e)
                self.outs[e[0]].append(e)
        for i in self.ins:                  # Union inputs follow their connection names (#1, #2, ...)
            self.ins[i].sort(key=lambda e: (_conn_no(e[4]), _id_key(e[0])))
        self.order_edges = self._block_until_done()
        self.rank, self.cycle_edges, self.dropped = self._topo()
        self.cyclic = sorted({i for e in self.cycle_edges for i in (e[0], e[2])}, key=_id_key)

    def _walk(self, parent, path, disabled):
        if parent is None:
            return
        for node in parent.findall("Node"):
            gui, eng = node.find("GuiSettings"), node.find("EngineSettings")
            plugin = (gui.get("Plugin") if gui is not None else "") or ""
            macro = (eng.get("Macro") if eng is not None else "") or ""
            kind = _kind(plugin, macro)
            cfg = node.find("Properties/Configuration")
            if cfg is None:
                cfg = ET.Element("Configuration")
            if kind == "ToolContainer":
                own = _true(_val(cfg, "Disabled", "False"))
                off = disabled or own
                caption = flat(cfg.findtext("Caption", "")) or "(unnamed container)"
                self.own_off[tuple(path + [caption])] = own
                self.containers.append((path + [caption], off))
                self._walk(node.find("ChildNodes"), path + [caption], off)
                continue
            ann = node.find("Properties/Annotation")
            note = flat(ann.findtext("AnnotationText", "") or ann.findtext("Name", "")) if ann is not None else ""
            meta = {}
            for mi in node.findall("Properties/MetaInfo"):
                meta[mi.get("connection") or "Output"] = [(f.get("name"), f.get("type"), f.get("size"))
                                                          for f in mi.iter("Field")]
            tid = node.get("ToolID", "?")
            self.tools[tid] = Tool(tid, kind, cfg, note, list(path), disabled, meta)
            self.tools[tid].macro = macro
            self._walk(node.find("ChildNodes"), path, disabled)

    def off_inside(self, i):
        """True when tool i sits in a disabled container nested inside its top-level container."""
        p = self.tools[i].path
        return any(self.own_off.get(tuple(p[:n])) for n in range(2, len(p) + 1))

    def reach(self, start_edges):
        """Every tool downstream of the given connections (iterative; safe on any graph)."""
        seen, todo = set(), [e[2] for e in start_edges]
        while todo:
            i = todo.pop()
            if i not in seen:
                seen.add(i)
                todo += [e[2] for e in self.outs.get(i, ())]
        return seen

    def _block_until_done(self):
        """(Parallel) Block Until Done: everything fed by output 1 runs before anything fed by
        output 2, and so on. Returned as extra ordering edges (before, after)."""
        extra = []
        for i in sorted(self.live, key=_id_key):
            if self.tools[i].kind not in ("ParallelBlockUntilDone", "BlockUntilDone"):
                continue
            anchors = sorted({e[1] for e in self.outs.get(i, ())}, key=_anchor_no)
            done = set()
            for a in anchors:
                first = [e for e in self.outs[i] if e[1] == a]
                here = self.reach(first)
                for after in {e[2] for e in first}:
                    for before in sorted(done - here, key=_id_key):
                        if not self.outs.get(before) and before not in self.reach([(0, 0, after)]):
                            extra.append((before, after))
                done |= here
        return extra

    def _topo(self):
        """Run order: real connections decide (this always succeeds on a workflow without a loop); Block Until Done
        only decides which ready branch goes first. A Block Until Done preference that the connections contradict is
        dropped and returned, never turned into a loop.
        -> (rank, connections that form a real loop, dropped (before, after) preferences)."""
        succ, indeg = defaultdict(list), {i: 0 for i in self.live}
        for i in self.live:
            for e in self.outs.get(i, ()):
                if e[2] not in succ[i]:
                    succ[i].append(e[2])
                    indeg[e[2]] += 1
        wait = defaultdict(set)                       # after -> befores it would like to follow
        for before, after in self.order_edges:
            wait[after].add(before)
        ready = sorted((i for i, n in indeg.items() if n == 0), key=_id_key)
        rank, dropped = {}, []
        while ready:
            pick = next((n for n, i in enumerate(ready) if wait[i] <= rank.keys()), 0)
            i = ready.pop(pick)
            dropped += [(b, i) for b in sorted(wait[i] - rank.keys(), key=_id_key)]
            rank[i] = len(rank)
            freed = []
            for d in sorted(succ.get(i, ()), key=_id_key):
                indeg[d] -= 1
                if indeg[d] == 0:
                    freed.append(d)
            ready[0:0] = freed                        # follow one chain to its end before starting the next
        left = set(self.live) - set(rank)
        loop = [e for i in sorted(left, key=_id_key) for e in self.outs.get(i, ()) if e[2] in left]
        for i in sorted(left, key=_id_key):
            rank[i] = len(rank)
        return rank, loop, dropped

    def ordered(self):
        return sorted(self.live, key=lambda i: self.rank[i])


def _id_key(i):
    return (int(i) if str(i).isdigit() else 10 ** 9, str(i))


def _conn_no(name):
    m = re.search(r"\d+", name or "")
    return int(m.group(0)) if m else 0


def _anchor_no(name):
    m = re.search(r"\d+", name or "")
    return (int(m.group(0)) if m else 0, name)


# ----------------------------------------------------------------------------- settings -> ir
def _select_fields(cfg):
    """[(field, selected, rename, type, size)], unknown_selected"""
    out, unknown = [], True
    for sf in cfg.iter("SelectField"):
        name = sf.get("field")
        sel = sf.get("selected", "True") != "False"
        if name == "*Unknown":
            unknown = sel
        out.append((name, sel, sf.get("rename") or None, sf.get("type") or None, sf.get("size") or None))
    return out, unknown


def _expr(tool, text, label):
    """Parse one expression; record why it cannot be converted, or what to watch."""
    text = text or ""
    try:
        ast = expr.parse(text)
        for f in expr.functions(ast):
            if f in expr.VOLATILE:
                tool.caveats.append("%s uses %s(): the result depends on the run date" % (label, f))
        for n in expr.precedence_notes(text):
            tool.caveats.append("%s: %s" % (label, n))
        for node in expr.walk(ast):
            if node[0] == "call" and node[1].startswith("regex_") and node[2][1][0] != "str":
                tool.caveats.append("%s: regular expression is built at run time; its syntax is not checked here" % label)
        return {"text": flat(text), "ast": ast, "canon": expr.canon(ast)}
    except expr.ExprError as e:
        tool.problems.append("%s: %s" % (label, e))
    except (RecursionError, MemoryError):
        tool.problems.append("%s: %s" % (label, expr.too_deep(text)))
    return {"text": flat(text), "ast": None, "canon": flat(text)}


def dyn_kind(how):
    """Dynamic Input 'read a list of data sources': what the listed value replaces in the template.
    -> "path" (the entire file path), "name" (the file name, in the template's folder), "prefix" or "suffix"
    (added to the template's file name), or None when the setting is not one of these."""
    h = re.sub(r"[^a-z]", "", (how or "").lower())
    if "prepend" in h or "prefix" in h:
        return "prefix"
    if "append" in h or "suffix" in h:
        return "suffix"
    if "path" in h:
        return "path"
    if "file" in h or "table" in h or "name" in h:
        return "name"
    return None


def _file(cfg):
    e = cfg.find("File")
    fso = cfg.find("FormatSpecificOptions")
    opts = {c.tag: (c.text or "") for c in (fso if fso is not None else [])}
    if e is None:
        return "", opts
    for k in ("FileFormat", "RecordLimit", "MaxRecords"):
        if e.get(k):
            opts["@" + k] = e.get(k)
    return (e.text or "").strip(), opts


def _recorded(tool):
    return {n: t for n, t, _ in tool.meta.get("Output", []) if n and t}


CLEANSE_BOXES = {"Check Box (84)": "nulls_to_blank", "Check Box (117)": "nulls_to_zero", "Check Box (15)": "trim_ws",
                 "Check Box (109)": "dup_ws", "Check Box (122)": "all_ws", "Check Box (53)": "letters",
                 "Check Box (58)": "numbers", "Check Box (70)": "punctuation", "Check Box (77)": "case",
                 "Check Box (135)": "drop_null_rows", "Check Box (136)": "drop_null_cols"}


# Settings each kind must have in its XML. If one is missing, the file does not have the shape this
# reader expects, and the tool becomes a stub instead of being converted with a silent default.
REQUIRED = {"Sample": ("Mode", "N"), "RecordID": ("FieldName", "StartValue"), "DateTime": ("InputFieldName", "Format"),
            "Directory": ("Directory",), "DynamicInput": ("ReadList_Field",), "Union": ("Mode",),
            "FindReplace": ("FieldFind", "FieldSearch", "FindMode", "ReplaceMode"), "MultiFieldFormula": ("Expression", "Fields"),
            "Summarize": ("SummarizeFields",), "Transpose": ("KeyFields", "DataFields"), "Message": ("MessageExpression",),
            "Formula": ("FormulaFields",), "Unique": ("UniqueFields",), "Sort": ("SortInfo",), "Select": ("SelectFields",),
            "TextInput": ("Fields", "Data")}


def _parse(t):
    k, cfg, ir = t.kind, t.cfg, t.ir
    missing = [tag for tag in REQUIRED.get(k, ()) if cfg.find(".//" + tag) is None]
    if missing:
        t.problems.append("settings not found in the XML where expected: %s (the file's layout differs from what this "
                          "converter knows; nothing was assumed)" % ", ".join(missing))
    if k in ("Input", "Output"):
        ir["file"], ir["options"] = _file(cfg)
        if k == "Input":
            ir["types"] = _recorded(t)
            if not ir["types"]:
                t.caveats.append("the workflow holds no recorded field types for this input")
        if not ir["file"]:
            t.problems.append("no file is configured")
        elif not re.search(r"\.(csv|txt|tsv|dat|xlsx|xlsm)(\|\|\||$)", ir["file"], re.I):
            t.problems.append("file type of %s is not supported (csv, txt, xlsx, xlsm only)" % ir["file"])
    elif k == "Directory":
        ir["dir"], ir["spec"] = _val(cfg, "Directory"), _val(cfg, "FileSpec", "*.*")
        if _true(_val(cfg, "IncludeSubDirs", "False")):
            t.problems.append("Include Subdirectories is not supported")
    elif k == "DynamicInput":
        inner = cfg.find("InputConfiguration/Configuration")
        ir["file"], ir["options"] = _file(inner if inner is not None else cfg)
        ir["mode"], ir["field"] = _val(cfg, "Mode", "ReadList"), _val(cfg, "ReadList_Field")
        ir["how"], ir["types"] = _val(cfg, "ReadList_Type", "FullPath"), _recorded(t)
        ir["kind"] = dyn_kind(ir["how"])
        if ir["mode"] != "ReadList" or ir["kind"] is None:
            t.problems.append("Dynamic Input mode %s/%s is not known to the converter" % (ir["mode"], ir["how"]))
    elif k == "RunCommand":
        ir["command"], ir["args"] = flat(cfg.findtext("Command", "")), flat(cfg.findtext("CommandArguments", ""))
        ir["folder"] = flat(cfg.findtext("WorkingDirectory", "") or cfg.findtext("RunDirectory", ""))
        ir["files"] = list(dict.fromkeys(
            flat(e.text) for e in cfg.iter() if e.text and e.tag not in ("Command", "CommandArguments") and
            re.search(r"\.(csv|txt|xlsx|xlsm|xls|yxdb|json|xml|dat)(\|\|\|.*)?$", flat(e.text), re.I)))
        # the program, under whichever name the settings use; and the file the incoming rows are written to first
        # (Run Command can write its input to a file, often a .bat or .vbs it then runs, and read a result back)
        ir["program"] = next((flat(cfg.findtext(n, "")) for n in ("Command", "RunCommand", "CommandLine", "Program")
                              if flat(cfg.findtext(n, ""))), "")
        ir["write"] = next((flat(f.text) for el in cfg.iter() if "write" in el.tag.lower()
                            for f in el.iter("File") if f.text and flat(f.text)), "")
    elif k == "TextInput":
        ir["columns"] = [f.get("name", "") for f in cfg.findall("Fields/Field")]
        ir["rows"] = [[c.text for c in r.findall("c")] for r in cfg.findall("Data/r")]
        ir["types"] = _recorded(t)
    elif k == "Select":
        ir["fields"], ir["unknown"] = _select_fields(cfg)
    elif k == "Formula":
        ir["fields"] = [(f.get("field"), f.get("type"), f.get("size"),
                         _expr(t, f.get("expression"), "[%s]" % f.get("field"))) for f in cfg.iter("FormulaField")]
    elif k == "Filter":
        simple = cfg.find("Simple")
        if cfg.findtext("Mode", "Custom") == "Simple" and simple is not None:
            ops = [flat(e.text) for e in simple.iter() if re.fullmatch(r"Operand\d*", e.tag) and flat(e.text)]
            ir.update(mode="simple", field=simple.findtext("Field", ""), operator=simple.findtext("Operator", ""),
                      operand=ops[0] if ops else "")
            known = "= != > >= < <= isnull isnotnull isempty isnotempty contains notcontains startswith endswith istrue isfalse"
            if ir["operator"].replace(" ", "").lower() not in known.split():
                t.problems.append("basic filter operator %r is not supported" % ir["operator"])
        else:
            ir.update(mode="custom", expr=_expr(t, cfg.findtext("Expression", ""), "filter"))
    elif k in ("Join", "AppendFields"):
        if k == "Join":
            keys = {ji.get("connection"): [f.get("field", "") for f in ji.findall("Field")] for ji in cfg.iter("JoinInfo")}
            ir["left"], ir["right"] = keys.get("Left", []), keys.get("Right", [])
            if _true(cfg.get("joinByRecordPos", "False")):
                t.problems.append("join by record position is not supported")
            elif not ir["left"] or len(ir["left"]) != len(ir["right"]):
                t.problems.append("join keys are missing or uneven")
        ir["fields"], ir["unknown"] = _select_fields(cfg)
    elif k == "Union":
        ir["mode"] = cfg.findtext("Mode", "ByName")
        ir["order"] = [c.text for c in cfg.findall("OutputOrder/Connection")] if _true(_val(cfg, "SetOutputOrder", "False")) else []
        if ir["mode"] not in ("ByName", "ByPos"):
            t.problems.append("Union mode %s (manual field mapping) is not supported" % ir["mode"])
    elif k == "Summarize":
        ir["fields"] = []
        for f in cfg.iter("SummarizeField"):
            opts = {c.tag: (c.text or "") for c in f}
            ir["fields"].append((f.get("field"), f.get("action"), f.get("rename") or None, opts))
            if f.get("action") not in ("GroupBy", "Sum", "Count", "CountNonNull", "CountNull", "CountDistinct",
                                       "CountDistinctNonNull", "Min", "Max", "First", "Last", "Avg", "Median", "Concat"):
                t.problems.append("Summarize action %s is not supported" % f.get("action"))
    elif k == "Sort":
        ir["fields"] = [(f.get("field"), f.get("order", "Ascending") != "Descending") for f in cfg.iter("Field")]
        info = cfg.find("SortInfo")
        if info is not None and info.get("locale", "0") not in ("0", ""):
            t.caveats.append("dictionary (locale %s) order is not reproduced" % info.get("locale"))
    elif k == "Unique":
        ir["fields"] = [f.get("field") for f in cfg.iter("Field")]
    elif k == "Sample":
        ir["mode"], ir["n"] = cfg.findtext("Mode", "First"), cfg.findtext("N", "1")
        ir["group"] = [f.get("field") or f.get("name") for f in cfg.findall("GroupFields/Field")]
        if ir["mode"] not in ("First", "Last", "Skip", "Sample"):
            t.problems.append("Sample mode %s is not deterministic or not supported" % ir["mode"])
    elif k == "RecordID":
        ir.update(name=cfg.findtext("FieldName", "RecordID"), start=cfg.findtext("StartValue", "1"),
                  type=cfg.findtext("FieldType", "Int32"), size=cfg.findtext("FieldSize", ""),
                  last=cfg.findtext("Position", "0") == "1")
    elif k == "Transpose":
        ir["keys"] = [f.get("field") for f in cfg.findall("KeyFields/Field")]
        fields = cfg.findall("DataFields/Field")
        ir["data"] = [f.get("field") for f in fields if f.get("selected") != "False" and f.get("field") != "*Unknown"]
        ir["unknown"] = any(f.get("field") == "*Unknown" and f.get("selected") != "False" for f in fields)
    elif k == "MultiFieldFormula":
        fields = cfg.findall("Fields/Field")
        ir["fields"] = [f.get("name") for f in fields if f.get("selected") != "False" and f.get("name") != "*Unknown"]
        ir["unknown"] = any(f.get("name") == "*Unknown" and f.get("selected") == "True" for f in fields)
        ir["group"] = cfg.findtext("FieldType", "Text")
        ir["copy"] = _true(_val(cfg, "CopyOutput", "False"))
        ir["addon"], ir["prefix"] = cfg.findtext("NewFieldAddOn", "New_"), cfg.findtext("NewFieldAddOnPos", "Prefix") != "Suffix"
        out = cfg.find("OutputFieldType")
        change = _true(_val(cfg, "ChangeFieldType", "False")) and out is not None
        ir["out_type"], ir["out_size"] = (out.get("type"), out.get("size")) if change else (None, None)
        ir["expr"] = _expr(t, cfg.findtext("Expression", ""), "expression")
    elif k == "FindReplace":
        ir.update(find=_val(cfg, "FieldFind"), search=_val(cfg, "FieldSearch"), mode=_val(cfg, "FindMode", "FindAny"),
                  nocase=_true(_val(cfg, "NoCase", "False")), whole=_true(_val(cfg, "MatchWholeWord", "False")),
                  replace_mode=_val(cfg, "ReplaceMode", "Replace"), replace=_val(cfg, "ReplaceFoundField"),
                  multiple=_true(_val(cfg, "ReplaceMultipleFound", "False")),
                  append=[f.get("field") for f in cfg.findall("ReplaceAppendFields/Field") if f.get("field")])
        if ir["mode"] not in ("FindBegin", "FindAny", "FindWhole"):
            t.problems.append("Find Replace mode %s is not supported" % ir["mode"])
        if ir["mode"] == "FindAny":
            t.caveats.append("matches any part of the field: behaves as equality only while the searched values "
                             "cannot occur inside each other")
        if ir["replace_mode"] != "Append":
            t.caveats.append("replace mode: the order in which several found values are replaced is assumed")
    elif k == "DateTime":
        ir.update(to_string=_true(_val(cfg, "IsFrom", "False")), field=_val(cfg, "InputFieldName"),
                  format=_val(cfg, "Format"), out=_val(cfg, "OutputFieldName", "DateTime_Out"),
                  language=_val(cfg, "Language", "English"))
        why = _datetime_format_problem(ir["format"])
        if why:
            t.problems.append(why)
        if ir["language"] not in ("English", ""):
            t.problems.append("DateTime language %s is not supported" % ir["language"])
    elif k == "Cleanse":
        raw = {v.get("name"): (v.text or "") for v in cfg.findall("Value")}
        ir["raw"] = raw
        ir["fields"] = re.findall(r'"((?:[^"]|"")*)"', raw.get("List Box (11)", ""))
        ir["options"] = {opt: _true(raw.get(box, "False")) for box, opt in CLEANSE_BOXES.items()}
        ir["options"]["case"] = raw.get("Drop Down (81)", "upper").lower() if ir["options"]["case"] else None
        unknown = [n for n in raw if n not in CLEANSE_BOXES and n not in ("List Box (11)", "Drop Down (81)")]
        if unknown:
            t.caveats.append("macro settings not recognised and ignored: " + ", ".join(sorted(unknown)))
    elif k == "Message":
        ir.update(when=cfg.findtext("When", "First"), type=cfg.findtext("MessageType", "Message"),
                  text=_expr(t, cfg.findtext("MessageExpression", '""'), "message"))
        ir["filter"] = _expr(t, cfg.findtext("Filter", ""), "condition") if ir["when"] == "Filter" else None
    elif k in ("ParallelBlockUntilDone", "BlockUntilDone") or k in IGNORED:
        pass
    else:
        ir["raw"] = generic(cfg)


DATETIME_TOKENS = ("yyyy", "yy", "Month", "MMMM", "Mon", "MMM", "MM", "M", "day", "dy", "dd", "d", "HH", "H", "hh",
                   "mm", "ss")          # the same list as ayx_runtime._TOOL_TOKENS


def _datetime_format_problem(fmt):
    i = 0
    while i < len(fmt):
        tok = next((t for t in DATETIME_TOKENS if fmt.startswith(t, i)), None)
        if tok is None and fmt[i].isalpha():
            return "DateTime format %r: the part %r is not supported" % (fmt, fmt[i:])
        i += len(tok) if tok else 1
    return "" if fmt else "DateTime format is empty"


def generic(cfg):
    """Every setting of a tool that has no converter, as one line."""
    parts = []
    for e in cfg.iter():
        if e is cfg:
            continue
        attrs = " ".join("%s=%s" % (a, flat(v)) for a, v in e.attrib.items() if v)
        text = flat(e.text)
        if attrs or text:
            parts.append(e.tag + ("(" + attrs + ")" if attrs else "") + ("=" + text if text else ""))
    return "; ".join(parts)
