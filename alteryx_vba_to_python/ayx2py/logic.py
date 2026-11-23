"""The logic of a workflow, written for a person: stages of plain-language steps.

A stage is a run of tools in which the data simply flows from one tool to the next: it starts where
streams meet (a Join, a Union, an input) or split (a Filter with both outputs used, a stream that feeds
several tools), or at a container boundary. Each tool becomes one or more numbered steps; every rule is
written out in full, once. A sub-chain that repeats with identical rules is described once and its
copies are one paragraph each. Deterministic: the same workflow always gives the same document.
"""
import os
import re
from collections import Counter, defaultdict

from . import expr, spec
from .workflow import IGNORED, STATUS, _id_key

STRING = ("String", "WString", "V_String", "V_WString")
ROUTE = {"True": "rows that pass", "False": "rows that fail", "Join": "matched rows", "Left": "left rows with no match",
         "Right": "right rows with no match", "Unique": "first row of each", "Duplicates": "duplicate rows"}
BLOCKERS = ("ParallelBlockUntilDone", "BlockUntilDone")
CODE_PAGES = {"28591": "Latin-1", "65001": "UTF-8", "1252": "Windows-1252", "1200": "UTF-16"}
CLEANSE_WORDS = {"trim_ws": "remove leading and trailing spaces", "dup_ws": "collapse repeated spaces",
                 "all_ws": "remove all spaces", "nulls_to_blank": "nulls become blank in text columns",
                 "nulls_to_zero": "nulls become 0 in number columns", "letters": "remove letters", "numbers": "remove digits",
                 "punctuation": "remove punctuation", "drop_null_rows": "remove rows that are entirely null",
                 "drop_null_cols": "remove columns that are entirely null"}
WIDTH = 100


def _names(items):
    return ", ".join("[%s]" % i for i in items if i)


def _file(spec_text):
    path, _, sheet = spec_text.partition("|||")
    sheet = sheet.strip().strip("`'\"")
    sheet = sheet[:-1] if sheet.endswith("$") else sheet
    return path.strip(), sheet


def _ranges(nums):
    nums, out = sorted(nums), []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(str(a) if a == b else "%d–%d" % (a, b) for a, b in out)


def _step(text, pre=None, children=None, short="", tid=None, kind=""):
    if tid:
        text = text[:-1] + " (#%s):" % tid if text.endswith(":") else text + " (#%s)" % tid
    return {"text": text, "pre": pre or [], "children": [{"text": c} if isinstance(c, str) else c for c in children or []],
            "short": short, "kind": kind}


class Logic:
    def __init__(self, wf):
        self.wf, self.t = wf, wf.tools
        order = wf.ordered()
        self.blocks = spec.find_blocks(wf, {i: spec.signature(self.t[i]) for i in order})
        self.block_of = {i: (b, c) for b, copies in enumerate(self.blocks, 1) for c, ids in enumerate(copies) for i in ids}
        self.stage_of, self.stages = {}, []
        for i in order:
            src = self._continues(i)
            if src is None:
                self.stage_of[i] = len(self.stages)
                self.stages.append([i])
            else:
                self.stage_of[i] = self.stage_of[src]
                self.stages[self.stage_of[src]].append(i)
        self.copy_of = {}                     # stage index -> (block, copy) for stages inside a repeated copy
        for s, ids in enumerate(self.stages):
            keys = {self.block_of.get(i) for i in ids}
            if len(keys) == 1 and None not in keys and next(iter(keys))[1] > 0:
                self.copy_of[s] = next(iter(keys))
        self.rule_seen = {}
        self.notes = defaultdict(list)       # topic -> [stage refs]

    # ---- segmentation
    def primary(self, i):
        ins, kind = self.wf.ins.get(i, []), self.t[i].kind
        if kind == "Union" or not ins:
            return None
        if kind == "Join":
            ins = [e for e in ins if e[3][:1].upper() == "L"]
        elif kind in ("FindReplace", "AppendFields"):
            ins = [e for e in ins if e[3][:1].upper() == "T"]
        return ins[0] if len(ins) == 1 else None

    def _continues(self, i):
        e = self.primary(i)
        if e is None:
            return None
        src = e[0]
        if len(self.wf.outs.get(src, [])) != 1 or src not in self.stage_of:
            return None
        if self.t[src].where != self.t[i].where or self.block_of.get(src) != self.block_of.get(i):
            return None
        return src

    # ---- references
    def num(self, s):
        return s + 1

    def anchor(self, s):
        if s in self.copy_of:
            b, c = self.copy_of[s]
            return "s%d" % self.num(min(x for x, k in self.copy_of.items() if k == (b, c)))
        return "s%d" % self.num(s)

    def sref(self, s):
        return "[[%s|stage %d]]" % (self.anchor(s), self.num(s))

    def eref(self, e, side=False):
        """'stage 4 (rows that pass)' for the source of edge e."""
        src, anchor = e[0], e[1]
        text = self.sref(self.stage_of[src])
        kind = self.t[src].kind
        if kind in ("Filter", "Join", "Unique"):
            text += " (%s)" % ROUTE.get(anchor, anchor)
        elif kind in BLOCKERS:
            text += " (%s)" % re.sub(r"(\D+)(\d+)", r"\1 \2", anchor).lower()
        return text

    def slist(self, ss):
        """'stage 8' / 'stages 8 and 9' / 'stages 8, 9 and 12', each number a link."""
        ss = list(dict.fromkeys(ss))
        if len(ss) == 1:
            return self.sref(ss[0])
        refs = ["[[%s|%d]]" % (self.anchor(x), self.num(x)) for x in ss]
        return "stages " + ", ".join(refs[:-1]) + " and " + refs[-1]

    def outs_by_anchor(self, i):
        groups = defaultdict(list)
        for e in self.wf.outs.get(i, []):
            groups[e[1]].append(self.stage_of[e[2]])
        return {a: self.slist(v) for a, v in groups.items()}

    # ---- rules
    def rule(self, label, node_text, ast, s, step_no):
        """Text and preformatted lines for 'label = rule'; a long rule used before is referred to."""
        if ast is None:
            return label + " = `%s` (could not be read)" % node_text, []
        lines = expr.decision(ast, WIDTH)
        key = expr.canon(ast)
        long = len(lines) > 2 or len(lines[0]) > 90
        if long and key in self.rule_seen and self.rule_seen[key][0] != s:
            st, n = self.rule_seen[key]
            return "%s = the same rule as %s, step %d" % (label, self.sref(st), n), []
        if long:
            self.rule_seen.setdefault(key, (s, step_no))
        if len(lines) == 1 and len(lines[0]) + len(label) <= WIDTH:
            return "%s = %s" % (label, lines[0]), []
        return label + ":", lines

    def flag(self, topic, s):
        ref = self.sref(s)
        if ref not in self.notes[topic]:
            self.notes[topic].append(ref)

    # ---- one tool -> steps
    def steps(self, i, s, start):
        t, ir, k = self.t[i], self.t[i].ir, self.t[i].kind
        out = []
        n = lambda: start + len(out)
        last = self.stages[s][-1] == i
        routes = self.outs_by_anchor(i) if last else {}
        if k == "Input":
            path, sheet = _file(ir["file"])
            opts, words = dict(ir["options"]), []
            if opts.get("CodePage") in CODE_PAGES:
                words.append(CODE_PAGES[opts.pop("CodePage")] + " text")
            d = opts.pop("Delimeter", opts.pop("Delimiter", None))
            if d is not None:
                words.append({",": "comma", "\\t": "tab", ";": "semicolon", "|": "pipe"}.get(d, repr(d)) + "-separated")
            if opts.get("HeaderRow") is not None:
                words.append("first row is the header" if opts.pop("HeaderRow") == "True" else "no header row")
            if opts.get("FieldLen"):
                words.append("text cut at %s characters" % opts.pop("FieldLen"))
            if opts.get("ImportLine") not in (None, "", "1"):
                words.append("starting at line %s" % opts.pop("ImportLine"))
            rest = ["%s=%s" % kv for kv in sorted(opts.items()) if kv[1] and not kv[0].startswith("@")]
            kids = (["read as: " + ", ".join(words)] if words else []) + (["other settings: " + "; ".join(rest)] if rest else [])
            if ir.get("types"):
                by = defaultdict(list)
                for name, typ in ir["types"].items():
                    by[typ].append(name)
                kids.append("columns as Alteryx typed them: " + "; ".join("%s: %s" % (typ, ", ".join(v)) for typ, v in by.items()))
            text = "Read " + ("sheet **%s** of " % sheet if sheet else "") + "`%s`" % path
            out.append(_step(text, children=kids, short="Read " + os.path.basename(path.replace("\\", "/")), tid=i))
        elif k == "DynamicInput":
            out.append(_step("For each row, read the file named in [%s] (read like `%s`) and stack the rows of all files"
                             % (ir["field"], _file(ir["file"])[0]), short="Read files listed in [%s]" % ir["field"], tid=i))
        elif k == "Directory":
            out.append(_step("List the files in `%s` that match `%s` (one row per file: name, full path, dates, size)"
                             % (ir["dir"], ir["spec"]), short="List files in " + ir["dir"], tid=i))
        elif k == "TextInput":
            rows = [["" if c is None else c for c in r] for r in ir["rows"]]
            widths = [max([len(c)] + [len(r[j]) if j < len(r) else 0 for r in rows]) for j, c in enumerate(ir["columns"])]
            line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()
            pre = [line(ir["columns"]), line(["-" * w for w in widths])] + [line(r + [""] * (len(widths) - len(r))) for r in rows]
            out.append(_step("Start from a table typed into the workflow (%d rows):" % len(rows), pre=pre,
                             short="Typed table", tid=i))
        elif k == "Select":
            kids = self.select_words(ir["fields"], ir["unknown"])
            if kids:
                out.append(_step("Choose columns:", children=kids, short="Choose columns", tid=i))
        elif k == "Formula":
            for name, typ, _, e in ir["fields"]:
                label = "Set **%s**%s" % (name, "" if not typ or typ in STRING else " (%s)" % typ)
                text, pre = self.rule(label, e["text"], e["ast"], s, n() + 1)
                out.append(_step(text, pre=pre, short="Set " + name, tid=i, kind="set"))
                if e["ast"] is not None and expr.precedence_notes(e["text"]):
                    self.flag("and_or", s)
        elif k == "MultiFieldFormula":
            cols = _names(ir["fields"]) + (" and every other %s column" % ir["group"].lower() if ir["unknown"] else "")
            where = " into a new column %s<column>" % ir["addon"] if ir["copy"] and ir["prefix"] else \
                " into a new column <column>%s" % ir["addon"] if ir["copy"] else ""
            if len(ir["fields"]) == 1 and not ir["unknown"]:
                label = "Set **%s%s**" % (ir["addon"] if ir["copy"] and ir["prefix"] else "", ir["fields"][0]) + \
                    (ir["addon"] if ir["copy"] and not ir["prefix"] else "")
                short = "Update [%s]" % ir["fields"][0]
            else:
                label, short = "For each of the columns %s: set it%s" % (cols, where), "Update %d columns" % len(ir["fields"])
            text, pre = self.rule(label, ir["expr"]["text"], ir["expr"]["ast"], s, n() + 1)
            if len(ir["fields"]) == 1 and not ir["unknown"]:
                text, pre = (x.replace("[this column]", "[%s]" % ir["fields"][0]) for x in (text, "\n".join(pre)))
                pre = pre.split("\n") if pre else []
            out.append(_step(text, pre=pre, short=short, tid=i))
        elif k == "Filter":
            if ir["mode"] == "simple":
                cond = "[%s] %s %s" % (ir["field"], ir["operator"], ir["operand"])
                lines = [cond]
            else:
                ast = ir["expr"]["ast"]
                lines = expr.decision(ast, WIDTH) if ast is not None else [ir["expr"]["text"]]
                if ast is not None and expr.precedence_notes(ir["expr"]["text"]):
                    self.flag("and_or", s)
            used = set(self.outs_by_anchor(i)) if last else {e[1] for e in self.wf.outs.get(i, [])}
            first = re.search(r"\[([^\]]+)\]", lines[0])
            short = "Filter on [%s]" % first.group(1) if first else "Filter"
            if used == {"True", "False"}:
                verb, routes_here = "Split the rows by the test", True
            elif used == {"False"}:
                verb, routes_here = "Drop the rows where", False
            elif used == {"True"}:
                verb, routes_here = "Keep the rows where", False
            else:
                verb, routes_here = "Test (no output is used)", False
            one = len(lines) == 1 and len(lines[0]) <= 80
            kids = ["%s → %s" % (ROUTE[a], routes[a]) for a in ("True", "False") if a in routes] if routes_here else []
            if routes_here:
                routes = {}
            out.append(_step("%s %s" % (verb, lines[0]) if one else verb + ":", pre=None if one else lines,
                             children=kids, short=short, tid=i))
        elif k == "Join":
            right = [e for e in self.wf.ins.get(i, []) if e[3][:1].upper() == "R"]
            keys = ", ".join("left [%s] = right [%s]" % p for p in zip(ir["left"], ir["right"]))
            kids = ["right side: " + ", ".join(self.eref(e) for e in right)] if right else []
            kids += self.select_words(ir["fields"], ir["unknown"], join=True)
            used = set(routes) if last else {e[1] for e in self.wf.outs.get(i, [])}
            if used == {"Join"}:
                kids.append("only matched rows continue; rows without a partner on either side are dropped")
            elif last and routes:
                kids += ["%s → %s" % (ROUTE.get(a, a), routes[a]) for a in ("Join", "Left", "Right") if a in routes]
                routes = {}
            out.append(_step("Match rows where %s" % keys, children=kids, short="Match on " + _names(ir["left"]), tid=i))
        elif k == "AppendFields":
            src = [e for e in self.wf.ins.get(i, []) if e[3][:1].upper() == "S"]
            kids = self.select_words(ir["fields"], ir["unknown"], join=True)
            out.append(_step("Add the columns of %s to every row (each row repeats once for every row there)"
                             % ", ".join(self.eref(e) for e in src), children=kids, short="Add columns", tid=i))
        elif k == "Union":
            srcs = [self.eref(e) for e in self.wf.ins.get(i, [])]
            how = "columns matched by name" if ir["mode"] == "ByName" else "columns matched by position"
            out.append(_step("Stack the rows of %s (%s)" % (", ".join(srcs), how), short="Stack %d inputs" % len(srcs), tid=i))
        elif k == "Summarize":
            group = [(f, r) for f, a, r, _ in ir["fields"] if a == "GroupBy"]
            words = {"Count": "count of rows", "CountNonNull": "count of non-null [%s]", "CountNull": "count of null [%s]",
                     "CountDistinct": "number of different [%s]", "CountDistinctNonNull": "number of different non-null [%s]",
                     "Sum": "sum of [%s]", "Avg": "average of [%s]", "Median": "median of [%s]", "Min": "smallest [%s]",
                     "Max": "largest [%s]", "First": "[%s] of the first row", "Last": "[%s] of the last row (in row order)"}
            kids = []
            for f, a, r, o in ir["fields"]:
                if a == "GroupBy":
                    if r and r != f:
                        kids.append("[%s] is renamed **%s**" % (f, r))
                    continue
                if a == "Concat":
                    what = 'all [%s] joined with "%s"' % (f, o.get("Separator", "")) + \
                        (' between "%s" and "%s"' % (o.get("Concat_Start"), o.get("Concat_End"))
                         if o.get("Concat_Start") or o.get("Concat_End") else "")
                else:
                    what = words.get(a, a + " of [%s]")
                    what = what % f if "%s" in what else what
                kids.append("**%s** = %s" % (r or "%s_%s" % (a, f), what))
            head = "Group the rows by %s; for each group:" % _names(f for f, _ in group) if group else "Over all rows:"
            out.append(_step(head, children=kids, short="Group by " + _names(f for f, _ in group) if group else "Totals",
                             tid=i))
        elif k == "Sort":
            out.append(_step("Sort by " + ", ".join("[%s] %s" % (f, "ascending" if a else "descending") for f, a in ir["fields"]),
                             short="Sort", tid=i))
        elif k == "Unique":
            used = set(routes) if last else {e[1] for e in self.wf.outs.get(i, [])}
            kids = []
            if last and len(routes) > 1:
                kids = ["%s → %s" % (ROUTE.get(a, a), routes[a]) for a in ("Unique", "Duplicates") if a in routes]
                routes = {}
            text = "Keep the first row for each %s" % _names(ir["fields"]) if used <= {"Unique"} else \
                "Keep the later rows that repeat an earlier %s" % _names(ir["fields"]) if used == {"Duplicates"} else \
                "Separate the first row for each %s from the rows that repeat it" % _names(ir["fields"])
            out.append(_step(text, children=kids, short="First per " + _names(ir["fields"][:2]), tid=i))
        elif k == "Sample":
            per = " for each %s" % _names(ir["group"]) if ir["group"] else ""
            text = {"First": "Keep the first %s rows", "Last": "Keep the last %s rows", "Skip": "Skip the first %s rows",
                    "Sample": "Keep 1 row in every %s"}.get(ir["mode"], ir["mode"] + " %s") % ir["n"]
            out.append(_step(text + per, short=text, tid=i))
        elif k == "RecordID":
            how = " as text of %s characters with leading zeros" % ir["size"] if ir["type"] in STRING + ("String",) and ir["size"] else ""
            out.append(_step("Number the rows into **%s**, starting at %s%s" % (ir["name"], ir["start"], how),
                             short="Number rows", tid=i))
        elif k == "Transpose":
            data = _names(ir["data"]) + (" and every other column" if ir["unknown"] else "")
            out.append(_step("Turn the columns %s into rows: one row per column, with its name in **Name** and its value "
                             "in **Value**; keep %s on each row" % (data, _names(ir["keys"]) or "no other column"),
                             short="Columns to rows", tid=i))
        elif k == "FindReplace":
            src = [e for e in self.wf.ins.get(i, []) if e[3][:1].upper() == "S"]
            where = ", ".join(self.eref(e) for e in src) or "the lookup input"
            how = {"FindWhole": "equals this row's [%s]", "FindBegin": "begins this row's [%s]",
                   "FindAny": "appears anywhere in this row's [%s]"}.get(ir["mode"], ir["mode"] + " [%s]") % ir["find"]
            case = "ignoring case" if ir["nocase"] else "matching case"
            if ir["replace_mode"] == "Append":
                text = "Add %s from the first row of %s whose [%s] %s (%s%s; empty when no row matches)" % (
                    ", ".join("**%s**" % a for a in ir["append"]), where, ir["search"], how, case,
                    ", whole words" if ir["whole"] else "")
                short = "Look up " + ", ".join(ir["append"][:2])
            else:
                text = "In [%s], replace text found in the [%s] column of %s with that row's [%s]%s (%s)" % (
                    ir["find"], ir["search"], where, ir["replace"], ", every match" if ir["multiple"] else "", case)
                short = "Replace text in [%s]" % ir["find"]
            kids = []
            if ir["mode"] == "FindAny":
                kids.append("partial match: a value found anywhere inside [%s] counts, not only an equal value" % ir["find"])
                self.flag("find_any", s)
            out.append(_step(text, children=kids, short=short, tid=i))
        elif k == "DateTime":
            verb = "Write [%s] as text in the format `%s`" if ir["to_string"] else "Read [%s] as a date with the format `%s`"
            out.append(_step((verb % (ir["field"], ir["format"])) + " into **%s**" % ir["out"],
                             short="Read [%s] as a date" % ir["field"], tid=i))
        elif k == "Cleanse":
            opts = [CLEANSE_WORDS[o] for o, v in ir["options"].items() if v and o in CLEANSE_WORDS]
            if ir["options"].get("case"):
                opts.append("change to %s case" % ir["options"]["case"])
            out.append(_step("Clean the columns %s: %s" % (_names(ir["fields"]), "; ".join(opts) or "no option ticked"),
                             short="Clean columns", tid=i))
            self.flag("cleanse", s)
        elif k == "Message":
            text = expr.readable(ir["text"]["ast"]) if ir["text"]["ast"] is not None else ir["text"]["text"]
            when = {"First": "on the first row", "Last": "after the last row", "Filter": "for each row where %s"}.get(ir["when"], ir["when"])
            if "%s" in when:
                when = when % (expr.readable(ir["filter"]["ast"]) if ir["filter"] and ir["filter"]["ast"] is not None else "?")
            verb = "Stop the run with the error" if ir["type"].lower().startswith("error") else "Log the message"
            out.append(_step("%s %s %s" % (verb, text, when), short="Check rows", tid=i))
        elif k == "Output":
            path, sheet = _file(ir["file"])
            how = {"Overwrite": "replace the file or sheet", "Append": "add the rows to the end", "RemoveFile": "replace the file",
                   "Create": "fail if it exists"}.get(ir["options"].get("OutputOption", ""), "")
            out.append(_step("Write the rows to " + ("sheet **%s** of " % sheet if sheet else "") + "`%s`" % path +
                             (" (%s)" % how if how else ""), short="Write " + os.path.basename(path.replace("\\", "/")), tid=i))
        elif k in BLOCKERS:
            out.append(_step("Wait until all rows have arrived, then pass them on: output 1 first, then output 2 "
                             "(Block Until Done)", short="Wait for all rows", tid=i))
        elif k == "RunCommand":
            cmd = " ".join(x for x in (t.cfg.findtext("Command", ""), t.cfg.findtext("CommandArguments", "")) if x)
            out.append(_step("Run the external command `%s`" % (cmd or ir.get("raw", "")), short="Run a command", tid=i))
        else:
            out.append(_step("Run **%s**%s; its settings:" % (k.replace("Macro:", "macro "), ""), pre=[ir.get("raw", "")],
                             short=k.replace("Macro:", "Macro "), tid=i))
        if t.status == "stub":
            out[-1]["text"] += " — not converted to Python: " + "; ".join(t.reasons())
            self.flag("stub", s)
        if t.note and out and t.note.lower() not in out[-1]["text"].lower():
            out[-1]["children"].append({"text": "note in the workflow: " + t.note})
        if last and routes and t.kind not in ("Output",) and len(routes) > 1:
            for a, v in routes.items():
                out[-1]["children"].append({"text": "%s → %s" % (ROUTE.get(a, re.sub(r"(\D+)(\d+)", r"\1 \2", a).lower()), v)})
        if last and not self.wf.outs.get(i) and k not in ("Output", "RunCommand", "Message"):
            out.append(_step("The rows are not used after this (the stream ends here)", kind="end"))
        return out

    def select_words(self, fields, unknown, join=False):
        real = [f for f in fields if f[0] != "*Unknown"]
        kids = []
        ren = [(f, r) for f, sel, r, _, _ in real if sel and r and r != f]
        if ren:
            kids.append("rename " + spec._prefix_groups(ren).replace("→", " → "))
        retype = ["[%s] to %s%s" % (r or f, typ, "(%s)" % size if size and typ in STRING else "")
                  for f, sel, r, typ, size in real if sel and typ]
        if retype and not join:
            kids.append("change type: " + ", ".join(retype))
        keep = [r or f for f, sel, r, _, _ in real if sel]
        drop = [f for f, sel, *_ in real if not sel]
        if not unknown:
            kids.append("keep only these columns, in this order: " + ", ".join(keep))
        elif drop:
            kids.append("drop " + ", ".join(drop))
        return kids

    # ---- the document
    def stage_title(self, s, steps):
        first = self.t[self.stages[s][0]]
        if first.note and len(first.note) <= 70:
            return first.note
        shorts, merged = [], []
        for st in steps:
            if not st["short"]:
                continue
            if st["short"].startswith("Set ") and merged and merged[-1].startswith("Set "):
                merged[-1] += ", " + st["short"][4:]
            elif not merged or merged[-1] != st["short"]:
                merged.append(st["short"])
        title = "; ".join(merged[:3]) + ("; …" if len(merged) > 3 else "")
        return title if len(title) <= 90 else title[:88] + "…"

    def build(self):
        wf, blocks = self.wf, []
        live = [self.t[i] for i in wf.ordered()]
        stage_steps, titles = {}, {}
        for s, ids in enumerate(self.stages):
            if s in self.copy_of:
                continue
            steps = []
            for i in ids:
                steps += self.steps(i, s, len(steps))
            stage_steps[s] = steps
            titles[s] = self.stage_title(s, steps)
        groups = defaultdict(list)
        for s, key in self.copy_of.items():
            groups[key].append(s)
        for key, ss in groups.items():
            b, c = key
            orig = sorted({self.stage_of[self.blocks[b - 1][0][self.blocks[b - 1][c].index(self.stages[s][0])]] for s in ss})
            titles[min(ss)] = "Repeat of stages %s" % _ranges([self.num(o) for o in orig])

        def incoming(s):
            first = self.stages[s][0]
            es = self.wf.ins.get(first, []) if self.t[first].kind == "Union" else \
                [e for e in self.wf.ins.get(first, []) if self.primary(first) is None or e == self.primary(first)]
            return [e for e in es if self.stage_of.get(e[0]) != s]

        def outgoing(s, skip=()):
            return [self.stage_of[e[2]] for i in self.stages[s] for e in self.wf.outs.get(i, [])
                    if self.stage_of[e[2]] != s and self.stage_of[e[2]] not in skip]

        name = os.path.basename(wf.source)
        status = Counter(t.status for t in live)
        inputs = [s for s in stage_steps if not incoming(s) and self.t[self.stages[s][0]].kind in
                  ("Input", "TextInput", "Directory", "DynamicInput")]
        outputs = [(s, i) for s in stage_steps for i in self.stages[s] if self.t[i].kind in ("Output", "RunCommand")]
        n_cmd = sum(1 for _, i in outputs if self.t[i].kind == "RunCommand")
        reps = ["stages %s repeat stages %s" % (_ranges([self.num(x) for x in ss]), titles[min(ss)].split("stages ")[1])
                for ss in sorted(groups.values(), key=min)]
        blocks += [("h", 1, "Logic of %s" % name, "top"),
                   ("p", "Generated by ayx2py from `%s` (sha256 %s…). The same workflow always gives the same text." %
                    (name, wf.sha256[:12])),
                   ("p", "**How to read it.** The workflow is cut into stages: in a stage the rows flow from one step to the "
                         "next; a new stage starts where streams split or meet. Stages are numbered in the order they "
                         "run. [Name] is a column. A rule written as a list of `when … → …` lines takes the value of the "
                         "first line whose condition holds. #123 is the tool's ID in Alteryx."),
                   ("h", 2, "Summary", "summary"),
                   ("steps", [{"text": t} for t in [
                       "**Reads** %d source%s; **writes** %d output%s%s (lists below)." % (
                           len(inputs), "" if len(inputs) == 1 else "s", len(outputs) - n_cmd,
                           "" if len(outputs) - n_cmd == 1 else "s",
                           "; **runs** %d external command%s" % (n_cmd, "" if n_cmd == 1 else "s") if n_cmd else ""),
                       "**Size:** %d tools that run, in %d stages%s." % (len(live), len(self.stages),
                                                                       ("; " + "; ".join(reps)) if reps else ""),
                       "**Python:** %d tools convert exactly, %d with a stated assumption, %d are not converted." % (
                           status["exact"], status["caveat"], status["stub"]),
                       "**Before relying on it:** see [[check|the last section]]."]], False)]
        blocks += [("h", 2, "Inputs", "inputs"),
                   ("table", ["Stage", "Reads"], [[self.sref(s), stage_steps[s][0]["text"]] for s in inputs] or [["", "none"]]),
                   ("h", 2, "Outputs and external commands", "outputs"),
                   ("table", ["Stage", "Writes or runs"], [[self.sref(s), st["text"]] for s, i in outputs
                                                   for st in stage_steps[s] if st["text"].endswith("(#%s)" % i)
                                                   or "(#%s)" % i in st["text"]] or [["", "none"]])]

        # stage map, by container
        by_where = defaultdict(list)
        for s in range(len(self.stages)):
            if s in self.copy_of and s != min(groups[self.copy_of[s]]):
                continue
            where = self.t[self.stages[s][0]].where
            by_where["outside any container" if where == "(no container)" else "container " + where].append(s)
        blocks.append(("h", 2, "Stage map", "map"))
        nodes, edges = [], []
        for s in sorted(by_where and [x for v in by_where.values() for x in v]):
            kind = "repeat" if s in self.copy_of else "stub" if any(self.t[i].status == "stub" for i in self.stages[s]) else \
                "input" if s in inputs else "output" if any(x[0] == s for x in outputs) else "stage"
            label = "Stages %s" % _ranges([self.num(x) for x in groups[self.copy_of[s]]]) if s in self.copy_of else \
                "Stage %d" % self.num(s)
            nodes.append({"id": self.anchor(s), "label": label, "sub": titles[s], "kind": kind, "href": self.anchor(s)})
        node_ids = {n["id"] for n in nodes}
        for s in range(len(self.stages)):
            for i in self.stages[s]:
                for e in self.wf.outs.get(i, []):
                    a, b = self.anchor(s), self.anchor(self.stage_of[e[2]])
                    if a != b and a in node_ids and b in node_ids:
                        edges.append((a, b))
        if len(nodes) <= 160:
            blocks.append(("diagram", nodes, edges))
        for where, ss in by_where.items():
            if len(by_where) > 1:
                blocks.append(("h", 4, where[0].upper() + where[1:], None))
            rows = []
            for s in ss:
                if s in self.copy_of:
                    mem = groups[self.copy_of[s]]
                    froms = list(dict.fromkeys(self.eref(e) for x in mem for i in self.stages[x] for e in self.wf.ins.get(i, [])
                                               if self.stage_of[e[0]] not in mem))
                    tos = [r for x in mem for r in outgoing(x, mem)]
                    rows.append(["[[%s|%s]]" % (self.anchor(s), _ranges([self.num(x) for x in mem])), titles[s],
                                 ", ".join(froms) or "—", self.slist(tos) if tos else "—"])
                else:
                    rows.append(["[[%s|%d]]" % (self.anchor(s), self.num(s)), titles[s],
                                 ", ".join(self.eref(e) for e in incoming(s)) or "—",
                                 self.slist(outgoing(s)) if outgoing(s) else "—"])
            blocks.append(("table", ["Stage", "What happens", "Takes rows from", "Sends rows to"], rows))

        # the stages
        if len(by_where) == 1:
            blocks.append(("h", 2, "Stages", "stages"))
        for where, ss in by_where.items():
            if len(by_where) > 1:
                blocks.append(("h", 2, "Stages " + ("in " if where.startswith("container") else "") + where, None))
            for s in ss:
                if s in self.copy_of:
                    blocks += self.repeat_section(groups[self.copy_of[s]], titles)
                    continue
                blocks.append(("h", 3, "Stage %d — %s" % (self.num(s), titles[s]), self.anchor(s)))
                ins, outs = incoming(s), outgoing(s)
                io = (["**In:** " + ", ".join(self.eref(e) for e in ins) + "."] if ins else []) + \
                     (["**Out:** " + self.slist(sorted(set(outs))) + "."] if outs else [])
                if io:
                    blocks.append(("p", "  ".join(io)))
                blocks.append(("steps", stage_steps[s], True))

        # what to check
        blocks.append(("h", 2, "Before relying on this", "check"))
        items = []
        stubs = [t for t in live if t.status == "stub"]
        if stubs:
            why = defaultdict(list)
            for t in stubs:
                why[(t.kind, "; ".join(t.reasons()))].append(t)
            items.append({"text": "**Not converted to Python** (%d tools): the generated code stops there with an error "
                                  "until the step is written by hand." % len(stubs),
                          "children": [{"text": "%s, %d tool%s (%s): %s" % (
                              kind, len(ts), "" if len(ts) == 1 else "s",
                              self.slist(sorted({self.stage_of[t.id] for t in ts})), reason)}
                              for (kind, reason), ts in why.items()]})
        if self.notes["find_any"]:
            items.append({"text": "**Partial-match lookups** (Find Replace, any part of the field) in " +
                                  ", ".join(self.notes["find_any"]) + ": they behave like an exact lookup only while no "
                                  "looked-up value can appear inside another one."})
        if self.notes["and_or"]:
            items.append({"text": "**AND and OR mixed without brackets** in " + ", ".join(self.notes["and_or"]) +
                                  ": Alteryx reads them left to right, so `a OR b AND c` means `(a OR b) AND c`. The rules "
                                  "above show the brackets Alteryx applies; check that this was intended."})
        if self.notes["cleanse"]:
            items.append({"text": "**Data Cleansing options** in " + ", ".join(self.notes["cleanse"]) + " are read from the "
                                  "macro's numbered check boxes; confirm them in the tool's settings."})
        kinds = sorted({t.kind for t in live if t.status == "caveat" and STATUS.get(t.kind, ("", ""))[1]})
        if kinds:
            items.append({"text": "**Alteryx behaviours the Python assumes** (settle them once with the probe workflow):",
                          "children": [{"text": "%s: %s" % (k, STATUS[k][1])} for k in kinds]})
        waits = [i for i in wf.ordered() if self.t[i].kind in BLOCKERS]
        if waits:
            items.append({"text": "**Run order:** %d Block Until Done tool%s (%s) hold back their second output until "
                                  "everything fed by the first has finished. The generated Python keeps that order; a "
                                  "rewrite must keep it too when the outputs are files that later steps read." % (
                                      len(waits), "" if len(waits) == 1 else "s",
                                      self.slist(sorted({self.stage_of[i] for i in waits})))})
        off = [(p, sum(1 for t in self.t.values() if t.disabled and t.path[:len(p)] == p)) for p, o in wf.containers if o]
        if off:
            items.append({"text": "**Disabled containers** (not run, not described): " +
                                  "; ".join("%s (%d tools)" % (" > ".join(p), n) for p, n in off) + "."})
        if wf.cyclic:
            items.append({"text": "**Tools in a loop** (order unknown): " + " ".join("#" + i for i in wf.cyclic)})
        blocks.append(("steps", items or [{"text": "Nothing to flag."}], False))
        return blocks

    def repeat_section(self, ss, titles):
        b, c = self.copy_of[ss[0]]
        copy, first = self.blocks[b - 1][c], self.blocks[b - 1][0]
        twin = {x: y for x, y in zip(copy, first)}
        orig = sorted({self.stage_of[twin[self.stages[s][0]]] for s in ss})
        mem = set(ss)
        head = "Stages %s — repeat of stages %s" % (_ranges([self.num(x) for x in ss]), _ranges([self.num(o) for o in orig]))
        lines = []
        for x in sorted(ss):
            for i in self.stages[x]:
                for e in self.wf.ins.get(i, []):
                    if self.stage_of[e[0]] in mem:
                        continue
                    mirror = next((f for f in self.wf.ins.get(twin[i], []) if f[3] == e[3]), None)
                    lines.append("takes %s%s" % (self.eref(e), " where the original takes %s" % self.eref(mirror)
                                                 if mirror else ""))
                for e in self.wf.outs.get(i, []):
                    if self.stage_of[e[2]] in mem:
                        continue
                    mirror = next((f for f in self.wf.outs.get(twin[i], []) if f[1] == e[1]), None)
                    lines.append("sends rows to %s%s" % (self.sref(self.stage_of[e[2]]), " where the original sends them to %s"
                                                         % self.sref(self.stage_of[mirror[2]]) if mirror else ""))
        pairs = ", ".join("%d = %d" % (self.num(x), self.num(self.stage_of[twin[self.stages[x][0]]])) for x in sorted(ss))
        return [("h", 3, head, self.anchor(min(ss))),
                ("p", "The same %d tools with the same rules and wiring as stages %s; only the rows differ:" % (
                    len(copy), _ranges([self.num(o) for o in orig]))),
                ("steps", [{"text": x} for x in dict.fromkeys(lines)], False),
                ("p", "Stage by stage: %s. Tool IDs: %s." % (pairs, _id_map(copy, first)))]


def _id_map(copy, first):
    if all(x.isdigit() for x in copy + first):
        deltas = {int(x) - int(y) for x, y in zip(copy, first)}
        if len(deltas) == 1:
            return "each is the original's ID %+d" % deltas.pop()
    return ", ".join("#%s = #%s" % (x, y) for x, y in zip(copy, first))


def build(wf):
    return Logic(wf).build()
