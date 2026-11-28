"""The guide's page, for a reader who has never opened Alteryx Designer.

1. What this workflow does: one block per container (a short summary, then the files it opens and saves, and the
   programs it runs), with a contents list above it.
2. How each output is made: one compact table per output, from its source files to the saved sheet, in run order.
   A row is shown only for a step that changes which rows or which values end up in the output (a filter, a rule
   that sets columns, a match with another table, a total, the save...), with its rule. Everything else is presumed:
   opening the file the story starts from, a Select that only keeps or retypes columns, Block Until Done, a lookup's
   own preparation, a settings value attached to every row. Those are listed, folded, at the end of the story.
   Neighbouring steps of one kind are one row; parallel paths stacked by a Union are one row with a small table.
   Work that several outputs share is told once, as Table A, B..., and the stories start from it.
3. Run order: what must finish before what.
4. Files, sheets and scripts: one table.
5. Detail (folded): how to read the pandas, the helper code, what to check before relying on the Python, the
   columns each container makes, and every step by number.

Every step keeps its number (2.14) and tool ID in small text, so the steps notebook and Designer can be matched to it.
The page is built from the same step descriptions as before (guide.Guide); only the arrangement is new.
"""
import os
import re
from collections import Counter, defaultdict

from . import expr
from .guide import BLOCKERS, HELPERS, OUT_MODE, SOURCES, _list, _md_plain, designer, file_text, place, simple_words

SIDE = {"Join": "R", "FindReplace": "S", "AppendFields": "S"}       # inputs that bring a lookup into the main flow
OUTS = {"Filter": ("True", "False"), "Join": ("Join", "Left", "Right"), "Unique": ("Unique", "Duplicates")}
PLAIN = {"True": "rows that pass", "False": "rows that fail", "Join": "matched rows",
         "Left": "rows only in the first input", "Right": "rows only in the second input",
         "Unique": "unique rows", "Duplicates": "duplicate rows"}
ENDS = ("Output",)                                   # what a story is told for
ATTACH = ("RunCommand", "Message")                    # joins the story whose rows it shares, or is a story itself
SETS = ("Formula", "MultiFieldFormula")
REORDER = ("Sort", "Summarize", "CrossTab")           # a sort before these is not seen in the result
NEEDS_ORDER = ("Sample", "Unique", "MultiRowFormula", "RecordID", "Output", "RunCommand", "Message")
INLINE = 160                                          # a rule longer than this shows its start; the rest is folded
ORDINAL = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
VERB = [("Filter", "filters rows"), ("Join", "matches tables"), ("FindReplace", "looks values up"),
        ("AppendFields", "matches tables"), ("Summarize", "totals rows"), ("Formula", "calculates columns"),
        ("MultiFieldFormula", "calculates columns"), ("Union", "stacks tables"), ("Unique", "removes duplicates"),
        ("Transpose", "reshapes tables"), ("CrossTab", "reshapes tables")]
WHY = {"source": "opens what the story starts from (named at the top)",
       "dir": "lists the files the story starts from (named at the top)",
       "order": "only sets the run order (see Run order)",
       "select": "only keeps, reorders or retypes columns",
       "sort": "sorts, but nothing after it depends on the order",
       "rid": "numbers the rows, but the number is not used or saved",
       "setting": "attaches a settings value to every row (named where it is used)",
       "settingprep": "prepares a settings value",
       "lookup": "prepares a lookup (said in the step that uses it)"}



# ----------------------------------------------------------------------------- rules in words
class _NoWords(Exception):
    pass


def _size(node):
    return sum(1 for _ in expr.walk(node))


def words(node, limit=110):
    """A simple rule in plain words, or None when it is not simple enough to say safely."""
    if node is None or _size(node) > 24:
        return None
    try:
        text = _say(node, 0)
    except (_NoWords, expr.ExprError, KeyError, IndexError, TypeError):
        return None
    return text if len(text) <= limit else None


def _lit(node):
    return node[0] in ("str", "num", "bool", "null") or (node[0] == "neg" and node[1][0] == "num")


def _say(n, depth):
    if depth > 4:
        raise _NoWords()
    k = n[0]
    if k in ("str", "num", "bool", "null", "field"):
        return expr.readable(n)
    if k == "neg" and n[1][0] == "num":
        return "-" + n[1][1]
    if k == "call":
        name, a = n[1], n[2]
        arg = lambda j: _say(a[j], depth + 1)
        if name == "replace" and len(a) == 3 and _lit(a[1]) and _lit(a[2]):
            return ("%s with %s removed" % (arg(0), arg(1)) if a[2] == ("str", "") else
                    "%s with %s replaced by %s" % (arg(0), arg(1), arg(2)))
        if name in ("left", "right") and len(a) == 2 and a[1][0] == "num":
            return "the %s %s character%s of %s" % ("first" if name == "left" else "last", a[1][1],
                                                     "" if a[1][1] == "1" else "s", arg(0))
        if name == "substring" and len(a) == 3 and a[1][0] == "num" and a[2][0] == "num":
            return "%s characters of %s from character %d" % (a[2][1], arg(0), int(a[1][1]) + 1)
        one = {"trim": "%s without spaces at either end", "uppercase": "%s in capitals", "lowercase": "%s in lower case",
               "length": "the length of %s", "tostring": "%s as text", "tonumber": "%s as a number",
               "abs": "%s without its sign", "titlecase": "%s with each word capitalised",
               "isempty": "%s is empty", "isnull": "%s is null", "round": None}
        if name in one and one[name] and len(a) == 1:
            return one[name] % arg(0)
        if name in ("contains", "startswith", "endswith") and len(a) == 2:
            return expr.readable(n) if all(x[0] in ("field", "str") for x in a) else _no()
        if name in ("datetimetoday", "datetimenow") and not a:
            return "today's date" if name == "datetimetoday" else "the time the workflow runs"
        raise _NoWords()
    if k == "cmp":
        left, right = _say(n[2], depth + 1), _say(n[3], depth + 1)
        plural = left.startswith(("the first ", "the last ")) and not left.split()[2] == "character"
        word = {"eq": "equal" if plural else "is" if _lit(n[3]) else "equals", "ne": "is not",
                "lt": "<", "le": "≤", "gt": ">", "ge": "≥"}[n[1]]
        if n[1] == "ne" and plural:
            word = "do not equal"
        return "%s %s %s" % (left, word, right)
    if k == "not":
        x = n[1]
        if x[0] == "call" and x[1] in ("isempty", "isnull") and len(x[2]) == 1:
            return "%s is not %s" % (_say(x[2][0], depth + 1), "empty" if x[1] == "isempty" else "null")
        if x[0] in ("in", "cmp") or (x[0] == "call" and x[1] == "contains"):
            return expr.readable(n)
        raise _NoWords()
    if k == "in":
        if all(_lit(v) for v in n[2]) and len(n[2]) <= 6:
            return expr.readable(n)
        raise _NoWords()
    if k in ("and", "or"):
        if len(n) > 4 or any(x[0] in ("and", "or") for x in n[1:]):
            raise _NoWords()
        return (" %s " % k).join(_say(x, depth + 1) for x in n[1:])
    if k == "arith":
        if len(n) > 5:
            raise _NoWords()
        sym = {"add": "+", "sub": "-", "mul": "×", "div": "÷"}.get(n[1])
        if sym is None or any(x[0] in ("arith", "if", "and", "or", "cmp") for x in n[2:]):
            raise _NoWords()
        return (" %s " % sym).join(_say(x, depth + 1) for x in n[2:])
    if k == "if":
        if len(n[1]) > 2 or not all(_lit(v) or v[0] == "field" for _, v in n[1]) or not (_lit(n[2]) or n[2][0] == "field"):
            raise _NoWords()
        parts = ["%s when %s" % (_say(v, depth + 1), _say(c, depth + 1)) for c, v in n[1]]
        return "; ".join(parts) + ", otherwise %s" % _say(n[2], depth + 1)
    raise _NoWords()


def _no():
    raise _NoWords()


def rule_text(e):
    """(inline text, cut) for an expression: plain words when safe, else the Alteryx rule as written."""
    if e is None:
        return "?", False
    said = words(e["ast"]) if e.get("ast") is not None else None
    if said:
        return said, False
    text = re.sub(r"\s+", " ", e.get("text") or "").strip()
    if len(text) <= INLINE:
        return "`%s`" % text.replace("`", "'"), False
    return "`%s …`" % text[:INLINE - 10].rstrip().replace("`", "'"), True


# ----------------------------------------------------------------------------- groups of steps
class Group:
    """A story (one output), a working table (shared by several), or the steps of a container that feed no output."""

    def __init__(self, kind, key, tools, rank):
        self.kind, self.key = kind, key              # kind: "story", "table" or "unused"; key: top container
        self.tools = sorted(tools, key=rank)
        self.ends, self.main, self.sides, self.side_of = [], [], {}, {}
        self.title, self.anchor, self.about, self.letter = "", "", "", ""
        self.rows, self.hidden, self.row_of = [], [], {}
        self.extra = []                              # more output anchors, when several saves are told as one


class Side:
    def __init__(self, tools, target):
        self.tools, self.target = tools, target


class Layout:
    def __init__(self, g):
        self.g, self.wf, self.t = g, g.wf, g.t
        self.rk = lambda i: (self.wf.rank.get(i, 10 ** 9), g.pos.get(i, 0))
        self.ctx = None                                  # the group whose rows are referred to as "row 3"
        self.groups, self.group_of = [], {}
        for key in g.tops:
            self.split(key)
        for grp in self.groups:
            self.shape(grp)
        self.settings = self.find_settings()            # Append Fields id -> (columns, source text, descendants)
        for grp in self.groups:
            self.hidden_of(grp)
            if grp.kind == "table" and all(i in grp.why for i in grp.tools):
                grp.kind = "pass"                        # shared, but nothing in it changes the data: not a table
        for key in g.tops:
            tables = sorted((x for x in self.groups if x.key == key and x.kind == "table"), key=lambda x: self.rk(x.tools[-1]))
            for n, grp in enumerate(tables):
                grp.letter = _letters(n)
        for grp in self.groups:
            self.name(grp)
        g.hook = self
        try:
            for grp in sorted(self.groups, key=lambda x: self.rk(x.tools[-1])):
                self.ctx = grp
                grp.rows = self.make_rows(grp)
            self.ctx = None
            for grp in self.groups:
                grp.about = grp.about or self.describe(grp)
        finally:
            g.hook = None

    # ---- which steps go into which story
    def steps_of(self, key):
        g = self.g
        return [i for i in g.order if g.top_of[i] == key and i not in g.fold_of]

    def ancestors(self, i, inside):
        seen, todo = set(), [i]
        while todo:
            x = todo.pop()
            for e in self.wf.ins.get(x, []):
                if e[0] in inside and e[0] not in seen:
                    seen.add(e[0])
                    todo.append(e[0])
        return seen

    def split(self, key):
        g, t = self.g, self.t
        tools = self.steps_of(key)
        inside = set(tools)
        token = {i: i for i in tools if t[i].kind in ENDS}
        anc = {o: self.ancestors(o, inside) for o in token}
        for m in (i for i in tools if t[i].kind in ATTACH):
            a = self.ancestors(m, inside)
            down = [o for o in anc if m in anc[o]]
            if down:                                   # it feeds an output: it is a step of that output's story
                continue
            waits = {x for x in a if t[x].kind in BLOCKERS}   # run after a save by Block Until Done: part of its story
            tied = [o for o in anc if waits & anc[o]]
            best = max(tied, key=lambda o: (len(a & anc[o]), -self.rk(o)[0]), default=None)
            token[m] = best if best is not None else m
        sig = {i: frozenset([token[i]] if i in token else []) for i in tools}
        changed = True
        while changed:                                 # to a fixed point: right whatever order the tools come in
            changed = False
            for i in sorted(tools, key=self.rk, reverse=True):
                s = set(sig[i])
                for e in self.wf.outs.get(i, []):
                    c = e[2]
                    if c in inside:
                        s |= sig[c]
                    elif c in g.top_of and c not in g.fold_of:
                        s.add(("exit", g.top_of[c]))
                if s != sig[i]:
                    sig[i], changed = frozenset(s), True
        stories = defaultdict(list)
        rest = []
        for i in tools:
            if len(sig[i]) == 1 and not isinstance(next(iter(sig[i])), tuple):
                stories[next(iter(sig[i]))].append(i)
            elif sig[i]:
                rest.append(i)
        unused = [i for i in tools if not sig[i]]
        tables, seen = [], set()                         # connected steps with the same set of outputs
        for i in sorted(rest, key=self.rk):
            if i in seen:
                continue
            comp, todo = [], [i]
            seen.add(i)
            while todo:
                x = todo.pop()
                comp.append(x)
                for e in self.wf.ins.get(x, []) + self.wf.outs.get(x, []):
                    y = e[2] if e[0] == x else e[0]
                    if y in inside and y not in seen and y in rest and sig[y] == sig[i]:
                        seen.add(y)
                        todo.append(y)
            tables.append(comp)
        order = sorted(stories.items(), key=lambda kv: self.rk(kv[0]))
        made = [Group("story", key, ids, self.rk) for _, ids in order]
        for grp, (end, _) in zip(made, order):
            grp.ends = [i for i in grp.tools if token.get(i) == end and (i == end or t[i].kind in ATTACH)]
        made += [Group("table", key, ids, self.rk) for ids in tables]
        if unused:
            made.append(Group("unused", key, unused, self.rk))
        for grp in made:
            for i in grp.tools:
                self.group_of[i] = grp
        self.groups += made

    def side_edge(self, i, e):
        letter = SIDE.get(self.t[i].kind)
        if self.t[i].kind == "JoinMultiple":
            return e[3] not in ("#1", "Input1", "Input")
        return bool(letter and e[3][:1].upper() == letter)

    def shape(self, grp):
        """Main steps (on the way to the result) and side preparations (a lookup's own steps), in run order."""
        mem = set(grp.tools)
        if grp.kind == "unused":
            grp.main = list(grp.tools)
            return
        if grp.kind == "table":
            grp.ends = [i for i in grp.tools if any(e[2] not in mem for e in self.wf.outs.get(i, []))] or grp.tools[-1:]
        main, todo = set(grp.ends), list(grp.ends)
        while todo:
            x = todo.pop()
            for e in self.wf.ins.get(x, []):
                if e[0] in mem and e[0] not in main and not self.side_edge(x, e):
                    main.add(e[0])
                    todo.append(e[0])
        grp.main = [i for i in grp.tools if i in main]
        target = {}
        for i in sorted((i for i in grp.tools if i not in main), key=self.rk, reverse=True):
            hits = [e[2] if e[2] in main else target.get(e[2]) for e in self.wf.outs.get(i, []) if e[2] in mem]
            hits = [h for h in hits if h is not None]
            target[i] = min(hits, key=self.rk) if hits else grp.main[-1] if grp.main else None
        by = defaultdict(list)
        for i, m in target.items():
            by[m].append(i)
        for m, ids in by.items():
            side = Side(sorted(ids, key=self.rk), m)
            grp.sides[m] = side
            for i in side.tools:
                grp.side_of[i] = side

    # ---- settings attached to every row
    def chain_of(self, i, e):
        """The tools that make the table arriving on edge e of tool i: its side preparation and any file read in it."""
        g, grp = self.g, self.group_of.get(i)
        out = []
        if e[0] in g.fold_of:
            out = list(g.L.stages[g.fold_of[e[0]]])
        side = grp.sides.get(i) if grp else None
        if side is not None:
            for x in side.tools:
                out.append(x)
                for f in self.wf.ins.get(x, []):
                    if f[0] in g.fold_of:
                        out += [y for y in g.L.stages[g.fold_of[f[0]]] if y not in out]
        return out

    def find_settings(self):
        g, out = self.g, {}
        for grp in self.groups:
            for i in grp.main:
                if self.t[i].kind != "AppendFields":
                    continue
                src = [e for e in self.wf.ins.get(i, []) if self.side_edge(i, e)]
                if len(src) != 1:
                    continue
                chain = self.chain_of(i, src[0])
                one = any((self.t[x].kind == "TextInput" and len(self.t[x].ir.get("rows", [])) <= 1) or
                          (self.t[x].kind == "Sample" and str(self.t[x].ir.get("n")) == "1") or
                          (self.t[x].kind == "Summarize" and not any(a == "GroupBy" for _, a, _, _ in self.t[x].ir["fields"]))
                          or self.t[x].kind == "Filter" for x in chain)
                named = any(self.t[x].kind == "Input" and re.search(r"setting|config|param|control", self.t[x].ir["file"], re.I)
                            for x in chain)
                if not chain or not (one or named):
                    continue
                cols = self.made_cols(chain)
                first = next((x for x in chain if self.t[x].kind in ("Input", "TextInput")), None)
                srct = self.file_chip(first) if first is not None and self.t[first].kind == "Input" else "a typed table"
                down, todo = set(), [i]
                while todo:
                    x = todo.pop()
                    for e in self.wf.outs.get(x, []):
                        if e[2] not in down:
                            down.add(e[2])
                            todo.append(e[2])
                out[i] = (cols, srct, down)
        return out

    def made_cols(self, chain):
        t, cols = self.t, []
        for x in chain:
            k, ir = t[x].kind, t[x].ir
            if k == "TextInput":
                cols += ir["columns"]
            elif k == "Input":
                cols += list(ir.get("types", {}))
            elif k in SETS:
                cols += [f[0] for f in ir.get("fields", [])] if k == "Formula" else []
            elif k == "Summarize":
                cols += [r or "%s_%s" % (a, f) for f, a, r, _ in ir["fields"] if a != "GroupBy"]
            elif k == "Select" and not ir["unknown"]:
                cols = [r or f for f, sel, r, _, _ in ir["fields"] if sel and f != "*Unknown"]
        return list(dict.fromkeys(cols))

    def with_settings(self, text, i):
        """Name a settings column where a rule uses it: the setting [T-1 Sheet] (Config.xlsx › Settings)."""
        for a, (cols, src, down) in self.settings.items():
            if i not in down:
                continue
            for c in cols:
                text = re.sub(r"(?<!setting )\[%s\]" % re.escape(c), lambda m: "the setting %s (%s)" % (m.group(0), src),
                              text, count=1)
        return text

    # ---- what is shown, and what is presumed
    def hidden_of(self, grp):
        grp.why = {}
        for i in grp.tools:
            why = self.why_hidden(i, grp)
            if why:
                grp.why[i] = why

    def why_hidden(self, i, grp):
        t, g = self.t[i], self.g
        k, ir = t.kind, t.ir
        if grp.kind == "unused":
            return None
        if i in grp.side_of:
            side = grp.side_of[i]
            if side.target in self.settings:
                return "settingprep"
            if k in SOURCES or k == "Select" and not self.renames(i) or k in BLOCKERS:
                return "lookup"
            if k == "Filter" and set(g.outs(i)) == {"True"}:
                return "lookup"
        if k in ("Input", "TextInput"):
            return "source"
        if k == "Directory" and all(self.t[e[2]].kind == "DynamicInput" for e in self.wf.outs.get(i, [])):
            return "dir"
        if k == "DynamicInput" and self.direct_dyn(i):
            return "source"
        if k in BLOCKERS:
            return "order"
        if k == "Select" and self.select_quiet(i):
            return "select"
        if k == "Sort" and not self.order_matters(i):
            return "sort"
        if k == "RecordID" and not self.id_used(i):
            return "rid"
        if k == "AppendFields" and i in self.settings:
            return "setting"
        return None

    def renames(self, i):
        return [(f, r) for f, sel, r, _, _ in self.t[i].ir["fields"] if f != "*Unknown" and sel and r and r != f]

    def select_quiet(self, i):
        ir = self.t[i].ir
        real = [f for f in ir["fields"] if f[0] != "*Unknown"]
        if self.renames(i) or any(not sel for _, sel, *_ in real) or any(sel and typ for _, sel, _, typ, _ in real):
            return False
        if ir["unknown"]:
            return True
        have = self.g.input_columns(i)
        keep = {(r or f).lower() for f, sel, r, _, _ in real if sel}
        return have is not None and have <= keep

    def direct_dyn(self, i):
        ins = self.wf.ins.get(i, [])
        return len(ins) == 1 and self.t[ins[0][0]].kind == "Directory"

    def order_matters(self, i):
        x, seen = i, 0
        while seen < 1000:
            seen += 1
            outs = self.wf.outs.get(x, [])
            if len(outs) != 1:
                return True                              # ends here, or splits: assume it matters
            x = outs[0][2]
            k = self.t[x].kind
            if k in REORDER:
                return False
            if k in NEEDS_ORDER:
                return True
        return True

    def id_used(self, i):
        name = (self.t[i].ir.get("name") or "").lower()
        after = [j for j in self.g.order if self.g.pos[j] > self.g.pos[i]]
        if any(name in [r.lower() for r in self.g.cards[j]["reads"]] for j in after):
            return True
        x = i
        for _ in range(1000):
            outs = self.wf.outs.get(x, [])
            if len(outs) != 1:
                return True
            x = outs[0][2]
            if self.t[x].kind == "Select":
                ir = self.t[x].ir
                listed = {f.lower(): sel for f, sel, *_ in ir["fields"]}
                if listed.get(name) is False or (name not in listed and not ir["unknown"]):
                    return False
            if self.t[x].kind == "Output":
                return True
        return True

    # ---- names
    def file_chip(self, i):
        t = self.t[i]
        return "{{f|%s}}" % file_text(t.ir["file"], t.ir["options"]).replace("`", "").replace(" › sheet ", " › ")

    def out_place(self, i):
        t = self.t[i]
        path, sheet, cells, _ = place(t.ir["file"], t.ir["options"])
        base = os.path.basename(path.replace("\\", "/")) or path
        return base + (" › " + sheet if sheet else "") + (" › " + cells.upper() if cells else "")

    def name(self, grp):
        t = self.t
        if grp.kind == "unused":
            grp.title, grp.anchor = "Steps that feed no output", "u%d" % self.g.cnum[grp.key]
        elif grp.kind == "pass":
            grp.title, grp.anchor = "shared housekeeping", "x%d" % self.g.cnum[grp.key]
        elif grp.kind == "story":
            end = grp.ends[0] if grp.ends else grp.tools[-1]
            grp.anchor = "o%s" % end
            if t[end].kind == "Output":
                grp.title = self.out_place(end)
            elif t[end].kind == "RunCommand":
                grp.title = "Runs " + self.program(end)[1]
            else:
                grp.title = "Check: " + _plain(self.g.short(end))[:70]
        else:
            grp.anchor = "w%s" % grp.tools[-1]
            grp.title = "Table %s" % grp.letter
            note = next((t[i].note for i in reversed(grp.ends + grp.tools) if t[i].note), "")
            grp.about = note

    def describe(self, grp):
        """One line on a working table: its source and its last action."""
        if grp.kind != "table":
            return ""
        srcs = self.sources(grp)
        last = next((r for r in reversed(grp.rows) if not r.get("sub")), None)
        text = "From %s" % (_and(srcs) or "earlier steps")
        if last is not None:
            text += "; last, " + _lower(_plain(last["text"]).rstrip(".:")) + (
                ": " + _plain(last["lines"][0]) if last.get("lines") else "")
        return text if len(text) <= 160 else text[:158].rstrip() + "…"

    # ---- how a step refers to another (the Guide calls these while hook is set)
    def where(self, i):
        """(text, anchor) naming the place a step is told."""
        g = self.g
        if i in g.fold_of:
            return self.read_chip(i), None
        grp = self.group_of.get(i)
        if grp is None:
            return "#%s" % i, None
        if grp is self.ctx and grp.kind == "unused":           # a folded list numbered by step, not by row
            return "step %s" % g.sid[i], "s" + g.sid[i].replace(".", "-")
        if grp is self.ctx:
            if i in grp.side_of and grp.side_of[i].target not in self.settings:
                return self.lookup_text(grp, grp.side_of[i]), None
            row = grp.row_of.get(i)
            if row is None:
                return "the rows it starts from", None
            return "row %s" % row["num"], row["anchor"]
        if grp.kind == "unused":
            return "steps that feed no output", grp.anchor
        if grp.kind == "pass":
            return self.through(i)
        far = "" if self.ctx is None or self.ctx.key == grp.key else " (container %d)" % g.cnum[grp.key]
        return "**%s**%s" % (grp.title, far), grp.anchor

    def users(self, i, grp):
        """The other tables and outputs that take rows from step i, seen through presumed shared steps."""
        out, todo, seen = [], [e[2] for e in self.wf.outs.get(i, [])], set()
        while todo:
            d = todo.pop(0)
            if d in seen:
                continue
            seen.add(d)
            o = self.group_of.get(d)
            if o is not None and o.kind == "pass":
                todo += [e[2] for e in self.wf.outs.get(d, [])]
            elif o is not None and o is not grp and o.kind != "unused":
                first = self.first_of(o)                 # several saves to one sheet are told as one
                if first not in out:
                    out.append(first)
        return out

    def first_of(self, o):
        if o.kind != "story":
            return o
        return next(x for x in self.groups if x.kind == "story" and x.title == o.title and x.key == o.key)

    def through(self, i):
        """(text, anchor) for a presumed shared step: the place its rows come from."""
        x, n = i, 0
        while self.group_of.get(x) is not None and self.group_of[x].kind == "pass" and n < 1000:
            t = self.t[x]
            if t.kind == "Input":
                return self.file_chip(x), None
            if t.kind == "TextInput":
                return "a typed table (%s)" % _list(t.ir["columns"], 3), None
            ins = [f for f in self.wf.ins.get(x, []) if not self.side_edge(x, f)]
            if not ins:
                break
            x, n = ins[0][0], n + 1
        if self.group_of.get(x) is not None and self.group_of[x].kind == "pass":
            return designer(self.t[x].kind), None
        return self.where(x)

    def read_chip(self, i):
        """A file read inside the step that uses it, as a file chip: {{f|Config.xlsx › Clients}}."""
        first = self.t[self.g.L.stages[self.g.fold_of[i]][0]]
        if first.kind == "TextInput":
            return "a typed table (%s)" % _list(first.ir["columns"], 3)
        return self.file_chip(first.id)

    def side_sources(self, grp, side):
        g = self.g
        mem = set(side.tools)
        srcs = []
        for x in side.tools:
            if self.t[x].kind == "Input":
                srcs.append(self.file_chip(x))
            elif self.t[x].kind == "TextInput":
                srcs.append("a typed table (%s)" % _list(self.t[x].ir["columns"], 3))
            for e in self.wf.ins.get(x, []):
                if e[0] not in mem:
                    srcs.append(self.where(e[0])[0] if e[0] in g.fold_of or self.group_of.get(e[0]) is not grp else "")
        return _and([s for s in dict.fromkeys(srcs) if s]) or "a lookup"

    def lookup_text(self, grp, side):
        """A lookup prepared by its own steps: its source, the rows that prepare it, and the filters folded into it."""
        conds = [self.cond(x) for x in side.tools if self.t[x].kind == "Filter" and grp.why.get(x) == "lookup"]
        shown = [grp.row_of[x]["num"] for x in side.tools if x in grp.row_of and grp.row_of[x].get("sub")]
        text = self.side_sources(grp, side)
        if shown:
            text += ", prepared in row%s %s" % ("s" if len(shown) > 1 else "", "–".join(
                dict.fromkeys([shown[0], shown[-1]])))
        if conds:
            text += " (only rows where %s)" % " and ".join(conds)
        return text

    def ref(self, i):
        text, anchor = self.where(i)
        if anchor is None:
            return text
        m = re.fullmatch(r"\*\*(.+?)\*\*(.*)", text)
        return "**[[%s|%s]]**%s" % (anchor, m.group(1), m.group(2)) if m else "[[%s|%s]]" % (anchor, text)

    def src(self, e):
        text, kind = self.ref(e[0]), self.t[e[0]].kind
        if e[0] in self.g.fold_of or (self.ctx is not None and e[0] in self.ctx.side_of):
            return text
        if kind in OUTS and e[1] in PLAIN:
            return "the %s %s" % (PLAIN[e[1]], ("of " if e[1] not in ("True", "False") else "") + text)
        return text

    def visible_source(self, e):
        """The edge, walked back through presumed steps, to the shown step (or other table) its rows come from."""
        x, a, n = e[0], e[1], 0
        grp = self.ctx
        while grp is not None and x in grp.why and grp.why[x] not in ("source", "dir") and n < 1000:
            ins = [f for f in self.wf.ins.get(x, []) if not self.side_edge(x, f)]
            if len(ins) != 1:
                break
            x, a, n = ins[0][0], ins[0][1], n + 1
        return (x, a) + tuple(e[2:])

    # ---- sentences
    def cond(self, i):
        t = self.t[i]
        if t.ir["mode"] == "simple":
            return simple_words(t.ir)
        return rule_text(t.ir["expr"])[0]

    def program(self, i):
        """(sentence, short name) for a Run Command, from its own settings."""
        ir = self.t[i].ir
        prog = " ".join(x for x in (ir.get("program") or ir.get("command"), ir.get("args")) if x).strip()
        write = ir.get("write") or ""
        wname = os.path.basename(write.replace("\\", "/"))
        script = next((w for w in prog.split() if re.search(r"\.(vbs|bat|cmd|ps1|py|js)$", w.strip('"'), re.I)), "")
        short = os.path.basename((script or prog.split()[0] if prog else wname or "a program").strip('"').replace("\\", "/"))
        if write and (not prog or os.path.basename(prog.split()[0].strip('"').replace("\\", "/")).lower() == wname.lower()):
            col = self.incoming_text_col(i)
            return ("Writes %s to `%s` and runs it outside Alteryx" % (
                "the text in [%s]" % col if col else "its incoming rows", wname or write), wname or "a script")
        if write:
            return "Writes its rows to `%s`, then runs `%s` outside Alteryx" % (wname, prog), short
        if prog:
            return "Runs `%s` outside Alteryx" % prog, short
        return "Runs a program outside Alteryx; its settings name none that the converter could read (see Rule and " \
               "pandas)", "a program"

    def incoming_text_col(self, i):
        ins = self.wf.ins.get(i, [])
        if len(ins) != 1:
            return None
        src = self.t[ins[0][0]]
        if src.kind == "Summarize":
            made = [r or "%s_%s" % (a, f) for f, a, r, _ in src.ir["fields"] if a != "GroupBy"]
            return made[0] if len(made) == 1 else None
        made = [w[0] for w in self.g.cards.get(src.id, {}).get("writes", [])]
        return made[0] if len(made) == 1 else None

    def sets_lines(self, i):
        out = []
        for name, _, _, e in self.t[i].ir["fields"]:
            text, _ = rule_text(e)
            out.append("**%s** = %s" % (name, text))
        return out

    def say(self, i):
        """(sentence, [lines], [columns]) for a shown step: what happens to the data, with the rule."""
        g, t = self.g, self.t[i]
        k, ir, c = t.kind, t.ir, g.cards[i]
        made = [w[0] for w in c["writes"]]
        if k == "Formula":
            n = len(ir["fields"])
            return "Sets %d column%s:" % (n, "" if n == 1 else "s"), self.sets_lines(i), [f[0] for f in ir["fields"]]
        if k == "MultiFieldFormula":
            return "Applies one rule to each of %s: %s." % (_list(ir["fields"], 6), rule_text(ir["expr"])[0]), [], made
        if k == "Filter":
            used = set(g.outs(i))
            cond = self.cond(i)
            if used >= {"True", "False"}:
                return "Splits the rows on: %s." % cond, [], []
            if used == {"False"}:
                return "Removes the rows where %s." % cond, [], []
            return "Keeps only the rows where %s." % cond, [], []
        if k == "Select":
            real = [f for f in ir["fields"] if f[0] != "*Unknown"]
            ren = self.renames(i)
            drop = [f for f, sel, *_ in real if not sel]
            keep = [r or f for f, sel, r, _, _ in real if sel]
            parts = []
            if not ir["unknown"]:
                parts.append("keeps only the columns %s" % _list(keep, 8))
            elif drop:
                parts.append("removes the columns %s" % _list(drop, 8))
            if ren:
                parts.append("renames " + ", ".join("[%s] to [%s]" % p for p in ren[:6]) + (
                    " +%d more" % (len(ren) - 6) if len(ren) > 6 else ""))
            retype = [(r or f, typ + ("(%s)" % size if size and "String" in typ else "")) for f, sel, r, typ, size in real
                      if sel and typ]
            if retype:
                parts.append("sets the type of " + ", ".join("[%s] to %s" % p for p in retype[:6]) + (
                    " +%d more" % (len(retype) - 6) if len(retype) > 6 else ""))
            cols = [b for _, b in ren] + (["−" + d for d in drop] if ir["unknown"] else [])
            return _cap("; ".join(parts) or "keeps the columns") + ".", [], cols
        if k == "Join":
            right = [e for e in self.wf.ins.get(i, []) if self.side_edge(i, e)]
            other = self.src(right[0]) if right else "nothing connected"
            keys = ", ".join("[%s]" % a if a == b else "[%s] = [%s]" % (a, b) for a, b in zip(ir["left"], ir["right"]))
            used = set(g.outs(i))
            if used == {"Join"}:
                return "Keeps the rows that have a match in %s on %s, and adds its columns." % (other, keys), [], made
            if used == {"Left"}:
                return "Keeps the rows that have no match in %s on %s." % (other, keys), [], made
            return "Matches the rows with %s on %s." % (other, keys), [], made
        if k == "Union":
            srcs = [self.src(self.visible_source(e)) for e in self.wf.ins.get(i, [])]
            srcs = [x if x.startswith("the ") else "the rows of " + x for x in dict.fromkeys(srcs)]
            return "Stacks %s into one table." % _and(srcs), [], []
        if k == "RunCommand":
            return self.program(i)[0] + ".", [], []
        if k == "Output":
            mode = OUT_MODE.get(ir["options"].get("OutputOption", ""), "")
            return "Saves the table to {{o|%s}}%s." % (self.out_place(i), " (%s)" % mode if mode else ""), [], []
        if k == "DynamicInput":
            return _cap(g.short(i)) + ".", [], []
        s = g.short(i)
        if s == "settings in the step's details":
            s = "runs %s; its settings are under Rule and pandas" % designer(k)
        if k == "Summarize":
            made = [r or "%s_%s" % (a, f) for f, a, r, _ in ir["fields"] if a != "GroupBy"]
            group = [f for f, a, _, _ in ir["fields"] if a == "GroupBy"]
            lines = ["**%s** = %s" % tuple(x.replace("\n", "\\n").replace("\t", "\\t").split(" = ", 1))
                     for x in c["rule"] if " = " in x]
            head = "Groups the rows by %s; for each group:" % _list(group, 6) if group else "Makes one row:"
            return head, lines, made
        return _cap(s) + ("" if s.endswith(".") else "."), [], made

    def brief(self, i):
        """A step as part of one path in a stacked row: short, with its rule."""
        k = self.t[i].kind
        if k == "Formula":
            return "sets " + "; ".join(self.sets_lines(i))
        text, lines, _ = self.say(i)
        return _lower(text.rstrip(".:")) + (" " + "; ".join(lines) if lines else "")

    # ---- rows
    def make_rows(self, grp):
        if grp.kind == "unused":
            rows = [self.row([i]) for i in grp.main]
            for n, r in enumerate(rows, 1):
                r["num"] = str(n)
            for r in rows:
                for x in r["tools"]:
                    grp.row_of[x] = r
            for r in rows:
                self.fill(r, grp, rows)
            return rows
        why = grp.why
        stacks, used = {}, set()
        for u in grp.main:
            if self.t[u].kind == "Union" and u not in why:
                paths = self.stack_paths(u, grp)
                if len(paths) >= 3:
                    stacks[u] = paths
                    used |= {x for p in paths for x in p["tools"]}
        rows = []
        for i in grp.main:
            if i in used or i in why:
                continue
            side = grp.sides.get(i)
            if side is not None and i not in self.settings:
                subs = [x for x in side.tools if x not in why and x not in used]
                for x in subs:
                    r = self.row([x])
                    r["sub"] = True
                    rows.append(r)
            if i in stacks:
                rows.append(self.stack_row(i, stacks[i]))
                continue
            prev = rows[-1] if rows else None
            if prev is not None and self.can_merge(prev, i, grp):
                self.merge(prev, i)
            else:
                rows.append(self.row([i]))
        n, letter = 0, 0
        for r in rows:
            if r.get("sub"):
                letter += 1
                r["num"] = "%d%s" % (n + 1, "abcdefghijklmnopqrstuvwxyz"[(letter - 1) % 26])
            else:
                n, letter = n + 1, 0
                r["num"] = str(n)
        for r in rows:
            for x in r["tools"]:
                grp.row_of[x] = r
        for r in rows:                                   # sentences need every row's number, so they come last
            self.fill(r, grp, rows)
        return rows

    def row(self, ids):
        return {"tools": list(ids), "kind": self.t[ids[-1]].kind, "anchor": "s" + self.g.sid[ids[0]].replace(".", "-")}

    def klass(self, i):
        k = self.t[i].kind
        if k in SETS:
            return "sets"
        if k == "Filter" and set(self.g.outs(i)) == {"True"}:
            return "keep"
        if k == "Select":
            return "select"
        return None

    def can_merge(self, prev, i, grp):
        if prev.get("sub") or prev.get("stack") or self.klass(i) is None:
            return False
        last = prev["tools"][-1]
        if self.klass(last) != self.klass(i):
            return False
        ins = [e for e in self.wf.ins.get(i, []) if not self.side_edge(i, e)]
        if len(ins) != 1:
            return False
        src = self.visible_source(ins[0])
        if src[0] != last or len(self.wf.outs.get(last, [])) != 1:
            return False
        x = ins[0][0]                                    # the presumed steps in between have one consumer each
        while x != last:
            if len(self.wf.outs.get(x, [])) != 1:
                return False
            x = [e for e in self.wf.ins.get(x, []) if not self.side_edge(x, e)][0][0]
        return True

    def merge(self, prev, i):
        prev["tools"].append(i)

    def fill(self, r, grp, rows):
        """The row's sentence, lines, columns and small print, now that every row has its number."""
        g = self.g
        self.ctx = grp
        ids = r["tools"]
        try:
            if r.get("stack"):
                self.fill_stack(r)
            elif r["kind"] in SETS and len(ids) > 1:
                lines = [x for i in ids for x in (self.sets_lines(i) if self.t[i].kind == "Formula" else [self.say(i)[0]])]
                r["text"], r["lines"] = "Sets %d columns:" % len(lines), lines
                r["cols"] = [w for i in ids for w in self.say(i)[2]]
            elif self.klass(ids[0]) == "keep" and len(ids) > 1:
                r["text"], r["lines"], r["cols"] = "Keeps only the rows where %s." % " and ".join(
                    self.cond(i) for i in ids), [], []
            elif r["kind"] == "Select" and len(ids) > 1:
                parts = [self.say(i) for i in ids]
                r["text"] = " ".join(p[0] for p in parts)
                r["lines"], r["cols"] = [], [c for p in parts for c in p[2]]
            else:
                r["text"], r["lines"], r["cols"] = self.say(ids[0])
        except Exception as exc:  # noqa: BLE001 - one step must not stop the page; reported in Detail
            r["text"], r["lines"], r["cols"] = "Runs %s (could not describe: %s; see Rule and pandas)." % (
                designer(self.t[ids[0]].kind), g.failed(ids[0], exc)), [], []
        first = ids[0]
        if not r.get("stack"):
            r["text"] = self.lead(r, grp, rows) + r["text"]
            r["split"] = self.split_line(ids[-1], grp, rows, r)
        r["text"] = self.with_settings(r["text"], first)
        r["lines"] = [self.with_settings(x, first) for x in r.get("lines", [])]
        if r.get("stack"):
            ids = [r["out"]]                             # the paths' own steps are listed in the row's table
        kinds = list(dict.fromkeys(designer(self.t[i].kind) for i in ids))
        r["small"] = "%s · step %s · tool %s" % (", ".join(kinds[:3]) + (" …" if len(kinds) > 3 else ""),
                                                 _span([g.sid[i] for i in ids]), ", ".join("#%s" % i for i in ids[:6]) + (
                                                     " +%d" % (len(ids) - 6) if len(ids) > 6 else ""))
        notes = [self.t[i].note for i in ids if self.t[i].note]
        r["note"] = notes[0] if len(notes) == 1 else ""
        r["hand"] = "outside Alteryx: write by hand" if any(self.t[i].kind == "RunCommand" for i in ids) else \
            "not converted: write by hand" if any(self.t[i].status == "stub" for i in ids) else ""
        ids = r["tools"]
        r["folds"] = [g.safe_fold(i) for i in ids]
        r["anchors"] = ["s" + g.sid[i].replace(".", "-") for i in ids[1:]]
        cols = list(dict.fromkeys(r.get("cols") or []))
        r["cols"] = " ".join("[%s]" % c if not c.startswith("−") else "−[%s]" % c[1:] for c in cols[:8]) + (
            " +%d more" % (len(cols) - 8) if len(cols) > 8 else "")

    def lead(self, r, grp, rows):
        """'From Table A: ' or 'On the rows that fail row 3: ' when a row does not simply go on from the row above."""
        i = r["tools"][0]
        k = self.t[i].kind
        if k in ("Union", "RunCommand", "Message") or k in SOURCES:
            return ""
        ins = [e for e in self.wf.ins.get(i, []) if not self.side_edge(i, e)]
        if len(ins) != 1 or ins[0][0] in self.g.fold_of:
            return ""
        x, a = self.visible_source(ins[0])[:2]
        pos = rows.index(r)
        above = next((p for p in reversed(rows[:pos]) if not p.get("sub")), None) if not r.get("sub") else \
            (rows[pos - 1] if pos and rows[pos - 1].get("sub") else None)
        mine = self.group_of.get(x) is grp and x not in grp.why
        plain = self.t[x].kind not in OUTS or len(self.g.outs(x)) == 1
        if above is not None and mine and x == above.get("out", above["tools"][-1]) and plain:
            return ""
        if not mine and r is next((p for p in rows if not p.get("sub")), None) and not r.get("sub"):
            return ""                                    # the story's header says where it starts
        if r.get("sub") and not mine:
            return "To prepare the lookup from %s: " % self.side_sources(grp, grp.side_of[i])
        return "On %s: " % self.src((x, a)) if self.t[x].kind in OUTS and a in PLAIN else "From %s: " % self.src((x, a))

    def split_line(self, i, grp, rows, r):
        g, t = self.g, self.t[i]
        groups = g.outs(i)
        if t.kind not in OUTS or not groups:
            far = ["**[[%s|%s]]**%s" % (o.anchor, o.title, "" if o.key == grp.key else " (container %d)" % g.cnum[o.key])
                   for o in self.users(i, grp)]
            return ("also used by " + _and(far)) if far and t.kind != "Output" else ""
        if len(groups) == 1 and t.kind == "Filter":
            dst = groups[next(iter(groups))]
            nxt = rows[rows.index(r) + 1] if rows.index(r) + 1 < len(rows) else None
            if all(self.group_of.get(d) is grp for d in dst) and (nxt is None or any(
                    grp.row_of.get(d) is nxt for d in dst)):
                return ""
        if t.kind == "Join" and set(groups) == {"Join"}:
            return ""
        parts = []
        for a in OUTS[t.kind]:
            dst = list(dict.fromkeys(self.dest(d) for d in groups.get(a, [])))
            parts.append("**%s** → %s" % (PLAIN[a], _and(dst) if dst else "dropped"))
        return " · ".join(parts)

    def dest(self, d):
        grp = self.group_of.get(d)
        if grp is not None and grp.kind == "unused":
            return "not used by any output"
        if grp is self.ctx and d in grp.why:
            row = grp.row_of.get(d) or next((grp.row_of[x] for x in self.downstream(d) if x in grp.row_of), None)
            return "[[%s|row %s]]" % (row["anchor"], row["num"]) if row else "the save"
        return self.ref(d)

    def downstream(self, i):
        out, todo = [], [i]
        while todo and len(out) < 1000:
            x = todo.pop(0)
            for e in self.wf.outs.get(x, []):
                out.append(e[2])
                todo.append(e[2])
        return out

    # ---- parallel paths stacked by a Union
    def stack_paths(self, u, grp):
        main = set(grp.main)
        lanes = []
        for e in self.wf.ins.get(u, []):
            tools, x, a = [], e[0], e[1]
            while x in main and len(self.wf.outs.get(x, [])) == 1 and self.t[x].kind != "Union":
                prim = [f for f in self.wf.ins.get(x, []) if not self.side_edge(x, f)]
                tools.insert(0, x)
                if len(prim) != 1:
                    x = None
                    break
                x, a = prim[0][0], prim[0][1]
            lanes.append({"tools": tools, "root": x, "anchor": a, "edge": e})
        by = defaultdict(list)
        for ln in lanes:
            by[ln["root"]].append(ln)
        paths = []
        for root, lns in by.items():
            outs = self.wf.outs.get(root, []) if root is not None else []
            starts = {(ln["tools"][0] if ln["tools"] else u) for ln in lns}
            if root in main and self.t[root].kind in OUTS and len(lns) > 1 and all(e[2] in starts for e in outs) \
                    and len({ln["anchor"] for ln in lns}) == len(lns) \
                    and len([f for f in self.wf.ins.get(root, []) if not self.side_edge(root, f)]) == 1:
                paths.append({"split": root, "lanes": lns, "tools": [root] + [x for ln in lns for x in ln["tools"]]})
            else:
                paths += [{"split": None, "lanes": [ln], "tools": list(ln["tools"])} for ln in lns]
        for p in paths:                                  # a lookup's preparation goes with the step that uses it
            for x in list(p["tools"]):
                side = grp.sides.get(x)
                if side is not None:
                    p["tools"] += [y for y in side.tools if y not in p["tools"]]
        return paths

    def stack_row(self, u, paths):
        r = self.row([u])
        r["stack"], r["paths"], r["out"] = True, paths, u
        r["tools"] = [u] + sorted({x for p in paths for x in p["tools"]}, key=self.rk)
        r["anchor"] = "s" + self.g.sid[u].replace(".", "-")
        return r

    def fill_stack(self, r):
        g, grp = self.g, self.ctx
        u, paths = r["tools"][0], r["paths"]
        why = grp.why
        roots, table, cols = [], [], []
        for p in paths:
            if p["split"] is not None:
                root = p["split"]
                prim = [f for f in self.wf.ins.get(root, []) if not self.side_edge(root, f)][0]
                roots.append(self.src(self.visible_source(prim)))
                what = []
                for ln in p["lanes"]:
                    acts = [self.brief(x) for x in ln["tools"] if x not in why and x not in grp.side_of]
                    what.append("%s: %s" % (PLAIN.get(ln["anchor"], ln["anchor"]).replace("rows that ", ""),
                                            "; ".join(acts) or "as they are"))
                    cols += [c for x in ln["tools"] if x not in why and x not in grp.side_of for c in self.say(x)[2]]
                cond = "where %s" % self.cond(root) if self.t[root].kind == "Filter" else self.brief(root)
            else:
                ln = p["lanes"][0]
                if ln["root"] is not None:
                    roots.append(self.src(self.visible_source((ln["root"], ln["anchor"], None, None, None))))
                conds = [("where " if set(g.outs(x)) == {"True"} else "where not: ") + self.cond(x)
                         for x in ln["tools"] if self.t[x].kind == "Filter" and x not in why]
                acts = [self.brief(x) for x in ln["tools"] if self.t[x].kind != "Filter" and x not in why
                        and x not in grp.side_of]
                cond = " and ".join(conds) or "all of them"
                what = ["; ".join(acts) or "as they are"]
                cols += [c for x in ln["tools"] if x not in why and x not in grp.side_of for c in self.say(x)[2]]
            table.append([_cap(cond), what, "{{s|%s}}" % _span([g.sid[x] for x in p["tools"]])])
        common = [a for a in table[0][1][0].split("; ")] if table and all(len(row[1]) == 1 for row in table) else []
        common = [a for a in common if a != "as they are" and all(a in row[1][0].split("; ") for row in table)]
        tail = common if common and common == table[0][1][0].split("; ")[-len(common):] else []
        for row in table:
            parts = row[1][0].split("; ") if len(row[1]) == 1 else None
            row[1] = "; ".join(parts[:len(parts) - len(tail)] or ["(only the common step)"]) if parts and tail else \
                "; ".join(row[1])
        roots = list(dict.fromkeys(roots))
        r["text"] = "Stacks %d sets of rows%s, each chosen and changed as below%s:" % (
            len(paths), " taken from " + _and(roots) if roots and len(roots) <= 3 else "",
            "; each set then %s" % "; ".join(tail) if tail else "")
        r["table"] = {"head": ["Rows", "What is done to them", "Steps"], "rows": table}
        r["lines"], r["cols"], r["split"] = [], cols, ""

    # ---- blocks
    def sources(self, grp):
        """What a story or table starts from: files and folders (as chips), typed tables, other tables."""
        out = []
        for i in grp.main:
            t = self.t[i]
            if t.kind == "Input":
                out.append(self.file_chip(i))
            elif t.kind == "TextInput":
                out.append("a typed table (%s)" % _list(t.ir["columns"], 3))
            elif t.kind == "Directory" and grp.why.get(i) == "dir":
                dyn = [e[2] for e in self.wf.outs.get(i, []) if grp.why.get(e[2]) == "source"]
                like = ", each read like {{f|%s}}" % file_text(self.t[dyn[0]].ir["file"], self.t[dyn[0]].ir["options"]).replace(
                    "`", "").replace(" › sheet ", " › ") if dyn else ""
                out.append("every file in `%s` named `%s`%s" % (t.ir["dir"], t.ir["spec"], like))
            for e in self.wf.ins.get(i, []):
                if self.side_edge(i, e) or e[0] in self.g.fold_of:
                    continue
                o = self.group_of.get(e[0])
                if o is not None and o.kind == "pass":
                    out.append(self.ref(e[0]))
                elif o is not None and o is not grp and o.kind != "unused":
                    ctx, self.ctx = self.ctx, grp
                    out.append(self.src(self.visible_source(e)) if self.t[e[0]].kind in OUTS or o.kind == "pass"
                               else self.ref(e[0]))
                    self.ctx = ctx
        return list(dict.fromkeys(out))

    def housekeeping(self, grp):
        g = self.g
        shown = {x for r in grp.rows for x in r["tools"]}
        hidden = [i for i in grp.tools if i not in shown]
        items = ["{{s|%s}} %s #%s: %s" % (g.sid[i], designer(self.t[i].kind), i, WHY.get(grp.why.get(i), "presumed"))
                 for i in hidden]
        return {"anchor": "h" + grp.anchor, "title": "%d housekeeping step%s not shown" % (
            len(hidden), "" if len(hidden) == 1 else "s"), "items": items,
                "anchors": ["s" + g.sid[i].replace(".", "-") for i in hidden],
                "folds": [g.safe_fold(i) for i in hidden]} if hidden else None

    def story(self, grp, title=None):
        g = self.g
        intro = []
        if grp.kind == "table":
            users = []
            for i in grp.tools:
                users += [o for o in self.users(i, grp) if o not in users]
            intro.append(grp.about)
            intro.append("Used by %s." % _and(["**[[%s|%s]]**%s" % (u.anchor, u.title, "" if u.key == grp.key else
                                                                    " (container %d)" % g.cnum[u.key]) for u in users]
                                               or ["later steps"]))
        else:
            srcs = self.sources(grp)
            if srcs:
                intro.append("**Starts from:** " + _and(srcs) + ".")
        sets = [(cols, src) for a, (cols, src, down) in self.settings.items() if any(i in down for i in grp.tools)
                and not any(a == i for i in grp.tools if self.group_of.get(i) is not grp)]
        for cols, src in dict.fromkeys((tuple(c), s) for c, s in sets):
            intro.append("**Uses settings** from %s%s." % (src, ": " + _list(cols, 6) if cols else ""))
        badge = "container %d · disabled in the saved file" % g.cnum[grp.key] if grp.key in g.off else ""
        kind = "output" if grp.kind == "story" else "table"
        return ("acts", grp.anchor, kind, title or grp.title, badge, intro, self.row_blocks(grp.rows),
                self.housekeeping(grp), grp.extra)

    def row_blocks(self, rows):
        out = []
        for r in rows:
            out.append({k: r.get(k) for k in ("num", "anchor", "anchors", "text", "lines", "table", "cols", "small",
                                               "note", "hand", "split", "folds", "sub")})
        return out

    def unused_block(self, grp):
        g = self.g
        kinds = Counter(designer(self.t[i].kind) for i in grp.tools)
        self.ctx = grp
        items, folds = [], []
        for r in grp.rows:
            items.append("{{s|%s}} %s #%s: %s" % (g.sid[r["tools"][0]], designer(self.t[r["tools"][0]].kind),
                                                  r["tools"][0], " ".join([r["text"]] + r.get("lines", [])) +
                                                  (" · ⚠ " + r["hand"] if r.get("hand") else "")))
            folds += r["folds"]
        self.ctx = None
        line = "**Not used by any output:** %d step%s (%s). They run, but nothing they make is saved." % (
            len(grp.tools), "" if len(grp.tools) == 1 else "s", ", ".join("%s %d" % kv for kv in kinds.most_common(6)))
        return [("p", line), ("hk", {"anchor": grp.anchor, "title": "The %d step%s" % (
            len(grp.tools), "" if len(grp.tools) == 1 else "s"), "items": items,
            "anchors": ["s" + g.sid[i].replace(".", "-") for r in grp.rows for i in r["tools"]], "folds": folds})]
    # ---- files
    def file_uses(self, key=None):
        """[(kind, path, sheet, cells, how, tool)] for the files the workflow opens and saves."""
        g, out = self.g, []
        for i in g.order:
            if key is not None and g.top_of[i] != key:
                continue
            t = self.t[i]
            if t.kind in ("Input", "Output", "DynamicInput"):
                path, sheet, cells, line = place(t.ir["file"], t.ir["options"])
                how = "saved" if t.kind == "Output" else "listed in [%s]" % t.ir["field"] if t.kind == "DynamicInput" else \
                    "lookup" if i in g.fold_of else "opened"
                out.append((t.kind, path, sheet, cells, how, i, line))
            elif t.kind == "Directory":
                out.append(("Directory", t.ir["dir"], "", "", "files named %s listed" % t.ir["spec"], i, None))
            elif t.kind == "RunCommand":
                ir = t.ir
                prog = " ".join(x for x in (ir.get("program") or ir.get("command"), ir.get("args")) if x).strip()
                out.append(("RunCommand", prog or ir.get("write") or "(no program named)", "", "",
                            "written, then run" if ir.get("write") else "program run", i, None))
        return out

    def file_lines(self, uses, saved):
        by = {}
        for kind, path, sheet, cells, how, i, line in uses:
            if (kind == "Output") != saved or kind in ("RunCommand",):
                continue
            base = os.path.basename(path.replace("\\", "/")) or path
            if kind == "Directory":
                by.setdefault(("dir", path), ("the list of files in folder `%s` (%s)" % (path, how.split(" listed")[0]), []))
                continue
            if kind == "DynamicInput":
                by.setdefault(("dyn", i), ("the files listed in column [%s] (like {{f|%s}})" % (
                    self.t[i].ir["field"], base), []))
                continue
            k = ("file", path.lower().replace("\\", "/"))
            label = "{{%s|%s}}" % ("o" if saved else "f", base)
            entry = by.setdefault(k, (label, []))
            part = sheet + (" " + cells.upper() if cells else "") if sheet else ""
            grp = self.group_of.get(i) if saved else None
            if grp is not None and grp.kind == "story":
                part = "[[%s|%s]]" % (grp.anchor, part or label)
            if part and part not in entry[1]:
                entry[1].append(part)
        return [parts[0] if len(parts) == 1 and parts[0].endswith(lab + "]]") else
                lab + (" › " + ", ".join(parts) if parts else "") for lab, parts in by.values()]

    # ---- the page
    def build(self):
        g, wf = self.g, self.wf
        g.hook = self
        try:
            return self._build()
        finally:
            g.hook = None

    def _build(self):
        g, wf = self.g, self.wf
        name = os.path.basename(wf.source)
        live = [self.t[i] for i in g.order]
        status = Counter(t.status for t in live)
        outputs = [x for x in self.groups if x.kind == "story"]
        shown = [x for x in outputs if not getattr(x, "merged", False)]
        off = g.off & set(g.tops)
        blocks = [("h", 1, "How %s works" % name, "top"),
                  ("p", "Made by ayx2py from `%s` (sha256 %s…), for someone who has never opened Alteryx Designer. "
                        "%d container%s%s, %d tools, %d output%s." % (
                            name, wf.sha256[:12], len(g.tops), "" if len(g.tops) == 1 else "s", ", %d disabled in the saved file (documented anyway)" % len(off)
                            if off else "", len(live), len(outputs), "" if len(outputs) == 1 else "s")),
                  ("p", "**Python:** steps converted exactly %d, with a stated assumption %d, not converted %d. "
                        "Before relying on it, read [[check|what to check]] in the Detail section." % (
                            status["exact"], status["caveat"], status["stub"]))]
        toc = [{"text": "[[does|1. What this workflow does]]", "children": [
                   {"text": "[[c%d|%d. %s]]%s" % (g.cnum[k], g.cnum[k], g.cname(k), " {{b|disabled}}" if k in g.off else "")}
                   for k in g.tops]},
               {"text": "[[made|2. How each output is made]]", "children": [
                   {"text": "Container %d: %s" % (g.cnum[k], ", ".join("[[%s|%s]]" % (x.anchor, x.title)
                                                                      for x in shown if x.key == k) or "no output") +
                    self.table_count(k)} for k in g.tops]},
               {"text": "[[order|3. Run order]]"}, {"text": "[[files|4. Files, sheets and scripts]]"},
               {"text": "[[detail|5. Detail]]: how to read the pandas, what to check, columns, every step by number"}]
        blocks.append(("nav", toc))
        blocks += self.section_does()
        blocks += self.section_made()
        blocks += self.section_order()
        blocks += self.section_files()
        blocks += self.section_detail(live)
        return blocks

    def table_count(self, key):
        n = sum(1 for x in self.groups if x.kind == "table" and x.key == key)
        return " (and %d working table%s)" % (n, "" if n == 1 else "s") if n else ""

    def links(self):
        """[(from container, to container, words)]: a connection, or a file one saves and another opens."""
        g, out = self.g, []
        for e in self.wf.edges:
            a, b = e[0], e[2]
            if a in g.top_of and b in g.top_of and g.top_of[a] != g.top_of[b] and a not in g.fold_of:
                grp = self.group_of.get(a)
                out.append((g.top_of[a], g.top_of[b], "takes rows from %s" % (
                    "**[[%s|%s]]**" % (grp.anchor, grp.title) if grp else "#%s" % a)))
        g.hook = None
        try:
            raw = g.links()
        finally:
            g.hook = self
        out += [(a, b, "opens `%s`, which container %d saves" % (os.path.basename(how[6:-1].replace("\\", "/")), g.cnum[a]))
                for a, b, how in raw if how.startswith("file ")]
        return list(dict.fromkeys(out))

    def section_does(self):
        g = self.g
        links = self.links()
        blocks = [("h", 2, "1. What this workflow does", "does")]
        for key in g.tops:
            tools = [i for i in g.order if g.top_of[i] == key]
            n = g.cnum[key]
            badge = "disabled in the saved file" if key in g.off else ""
            if not tools:
                blocks.append(("box", "c%d" % n, "%d. %s" % (n, g.cname(key)), badge, [
                    "Left out: the guide was made with --enabled-only."], []))
                continue
            uses = self.file_uses(key)
            opens, saves = self.file_lines(uses, False), self.file_lines(uses, True)
            runs = ["`%s`" % u[1] for u in uses if u[0] == "RunCommand"]
            kinds = Counter(self.t[i].kind for i in tools)
            verbs = list(dict.fromkeys(v for k, v in VERB if kinds.get(k)))
            n_saved = sum(1 for u in uses if u[0] == "Output")
            n_open = len(opens)
            parts = []
            if n_open:
                parts.append("opens %d source%s" % (n_open, "" if n_open == 1 else "s"))
            parts += verbs[:4]
            if n_saved:
                parts.append("saves %d table%s" % (n_saved, "" if n_saved == 1 else "s"))
            if runs:
                parts.append("runs %d program%s outside Alteryx" % (len(runs), "" if len(runs) == 1 else "s"))
            lead = ("It " + _and(parts) + ", in %d steps." % len(tools)) if parts else "It has %d steps." % len(tools)
            paras = [lead]
            fed = [w + " (container %d)" % g.cnum[a] if "takes rows" in w else w for a, b, w in links if b == key]
            if fed:
                paras.append("It " + "; it ".join(dict.fromkeys(fed)) + ".")
            if key in g.off:
                paras.append("Disabled in the saved file: Alteryx skips it until someone enables it (teams often run "
                             "one container at a time this way). It is explained here anyway; in the generated code "
                             "run it with `only=%d`." % n)
            meta = [("Opens", opens or ["—"]), ("Saves", saves or ["—"])]
            if runs:
                meta.append(("Runs", ", ".join(runs)))
            feeds = sorted({"container %d" % g.cnum[b] for a, b, _ in links if a == key}, key=lambda x: int(x.split()[1]))
            if feeds:
                meta.append(("Used by", ", ".join("[[c%s|%s]]" % (x.split()[1], x) for x in feeds)))
            blocks.append(("box", "c%d" % n, "%d. %s" % (n, g.cname(key)), badge, paras, meta))
        return blocks

    def section_made(self):
        g = self.g
        blocks = [("h", 2, "2. How each output is made", "made"),
                  ("p", "One table per output: the steps that decide which rows and which values reach it, in the "
                        "order they run, each with its rule. Steps that only open files, keep or retype columns, or set "
                        "the run order are presumed; each table lists them, folded, at its end. Work that several "
                        "outputs share is told once as Table A, B…, and the tables that use it start from it. Open "
                        "**Rule and pandas** on a row for its exact rule and the same step in pandas.")]
        for key in g.tops:
            grps = sorted((x for x in self.groups if x.key == key), key=lambda x: self.rk(x.tools[-1]))
            if not grps:
                continue
            badge = " {{b|disabled in the saved file}}" if key in g.off else ""
            blocks.append(("h", 3, "Container %d. %s%s" % (g.cnum[key], g.cname(key), badge), "m%d" % g.cnum[key]))
            by_place = defaultdict(list)
            for grp in grps:
                if grp.kind == "story" and grp.ends and self.t[grp.ends[0]].kind == "Output":
                    by_place[grp.title].append(grp)
            done = set()
            for grp in grps:
                if grp.kind in ("unused", "pass") or id(grp) in done:
                    continue
                same = by_place.get(grp.title, [grp]) if grp.kind == "story" else [grp]
                if len(same) > 1:
                    done |= {id(x) for x in same}
                    blocks += self.saves(same)
                else:
                    blocks.append(self.story(grp))
            shared = [i for grp in grps if grp.kind == "pass" for i in grp.tools]
            if shared:
                blocks.append(("p", "**Shared housekeeping:** %d step%s that several outputs pass through without any "
                                    "change to the data." % (len(shared), "" if len(shared) == 1 else "s")))
                blocks.append(("hk", {"anchor": "x%d" % g.cnum[key], "title": "The %d step%s" % (
                    len(shared), "" if len(shared) == 1 else "s"), "items": [
                    "{{s|%s}} %s #%s: %s" % (g.sid[i], designer(self.t[i].kind), i,
                                             WHY.get(self.group_of[i].why.get(i), "presumed")) for i in shared],
                    "anchors": ["s" + g.sid[i].replace(".", "-") for i in shared],
                    "folds": [g.safe_fold(i) for i in shared]}))
            for grp in grps:
                if grp.kind == "unused":
                    blocks += self.unused_block(grp)
        return blocks

    def saves(self, same):
        """Several saves to one file › sheet in one container."""
        g = self.g
        quiet = all(all(self.t[x].kind == "Output" for r in grp.rows for x in r["tools"]) for grp in same)
        if not quiet:
            out = []
            for n, grp in enumerate(same):
                out.append(self.story(grp, "%s — %s save" % (grp.title, ORDINAL[n] if n < len(ORDINAL) else "%dth" % (n + 1))))
            return out
        first = same[0]
        for grp in same[1:]:
            grp.merged = True
        append = all(self.t[grp.ends[0]].ir["options"].get("OutputOption") == "Append" for grp in same)
        rows, folds, hk = [], [], []
        self.ctx = None
        for n, grp in enumerate(same, 1):
            o = grp.ends[0]
            srcs = self.sources(grp) or ["earlier steps"]
            r = {"num": str(n), "anchor": "s" + g.sid[o].replace(".", "-"), "anchors": [grp.anchor] if n > 1 else [],
                 "text": "Save %d: the rows of %s." % (n, _and(srcs)), "lines": [], "cols": "", "split": "",
                 "small": "Output Data · step %s · tool #%s" % (g.sid[o], o), "note": self.t[o].note, "hand": "",
                 "folds": [g.safe_fold(o)]}
            rows.append(r)
            h = self.housekeeping(grp)
            if h:
                hk.append(h)
        intro = ["Saved %d times in this container. %s" % (len(same), "Each save adds its rows to the sheet." if append else
                                                            "Each save replaces the last, so all but the last are most "
                                                            "likely there only to control the run order.")]
        merged_hk = None
        if hk:
            merged_hk = {"anchor": "h" + first.anchor, "title": "%d housekeeping step%s not shown" % (
                sum(len(h["items"]) for h in hk), "" if sum(len(h["items"]) for h in hk) == 1 else "s"),
                "items": [x for h in hk for x in h["items"]], "anchors": [x for h in hk for x in h["anchors"]],
                "folds": [x for h in hk for x in h["folds"]]}
        badge = "container %d · disabled in the saved file" % g.cnum[first.key] if first.key in g.off else ""
        return [("acts", first.anchor, "output", first.title, badge, intro, rows, merged_hk, [])]

    def group_name(self, i):
        grp = self.group_of.get(i)
        if grp is None:
            return "#%s" % i, None
        return grp.title, grp.anchor

    def section_order(self):
        g, wf = self.g, self.wf
        items = []
        links = self.links()
        for a, b, w in links:
            items.append("Container %d runs after container %d: it %s." % (
                g.cnum[b], g.cnum[a], w.replace(", which container %d saves" % g.cnum[a], ", which container %d saves"
                                                                                       % g.cnum[a])))
        pairs = []
        for before, after in wf.order_edges:
            gb, ga = self.group_of.get(before), self.group_of.get(after)
            if gb is None or ga is None:
                continue
            bs = self.users(before, None) if gb.kind == "pass" else [self.first_of(gb)]
            as_ = self.users(after, None) if ga.kind == "pass" else [self.first_of(ga)]
            pairs += [(b, a) for b in bs for a in as_ if b is not a and b.kind != "unused" and a.kind != "unused"]
        pairs = list(dict.fromkeys(pairs))
        for gb, ga in pairs[:40]:
            items.append("**[[%s|%s]]** finishes before **[[%s|%s]]** starts (a Block Until Done tool forces this)." % (
                gb.anchor, gb.title, ga.anchor, ga.title))
        if len(pairs) > 40:
            items.append("… and %d more orders forced the same way." % (len(pairs) - 40))
        for x in (x for x in self.groups if x.kind == "story"):
            for i in x.ends:
                if self.t[i].kind == "RunCommand" and x.ends[0] != i:
                    prog = self.program(i)[1]
                    items.append("In **[[%s|%s]]**, `%s` runs after the file is saved." % (x.anchor, x.title, prog))
        many = Counter((x.key, x.title) for x in self.groups if x.kind == "story")
        for (key, title), n in many.items():
            if n > 1:
                first = next(x for x in self.groups if x.kind == "story" and x.title == title and x.key == key)
                items.append("**[[%s|%s]]** is saved %d times, one after another%s." % (
                    first.anchor, title, n, " (Block Until Done tools set the order)" if any(
                        self.t[i].kind in BLOCKERS for grp in self.groups if grp.key == key for i in grp.tools) else ""))
        for b, a in wf.dropped[:20]:
            nb, na = self.group_name(b), self.group_name(a)
            items.append("Could not keep: **%s** before **%s**, because the connections need the opposite order "
                         "(details in [[check|what to check]])." % (nb[0], na[0]))
        off = [k for k in g.tops if k in g.off]
        if off:
            items.append("Container%s %s %s disabled in the saved file: Alteryx skips %s until someone enables %s." % (
                "s" if len(off) > 1 else "", _and(["[[c%d|%d]]" % (g.cnum[k], g.cnum[k]) for k in off]),
                "are" if len(off) > 1 else "is", "them" if len(off) > 1 else "it", "them" if len(off) > 1 else "it"))
        if not items:
            items.append("Nothing forces an order beyond the connections: each story's steps run in the order shown.")
        return [("h", 2, "3. Run order", "order"),
                ("p", "Inside a table, the rows run in the order shown. What must finish before what:"),
                ("steps", [{"text": x} for x in items], True)]

    def section_files(self):
        g = self.g
        rows = {}
        for kind, path, sheet, cells, how, i, line in self.file_uses():
            base = os.path.basename(path.replace("\\", "/")) or path
            label = "`%s`" % path if kind in ("RunCommand", "Directory") else "{{%s|%s}}" % (
                "o" if kind == "Output" else "f", base)
            key = (path.lower().replace("\\", "/"), sheet.lower(), cells.upper(), kind == "RunCommand")
            row = rows.setdefault(key, [label, sheet or "—", "—" if kind in ("RunCommand", "Directory") else (
                cells.upper() or "all") + (" from row %d" % line if line else ""), [], [], []])
            row[3].append(how)
            if i in g.cards:
                cols = [w[0] for w in g.cards[i]["writes"]] if kind != "Output" else []
                row[4] += cols
            text, anchor = self.group_name(self.g.folded[self.g.fold_of[i]][0]) if i in g.fold_of else self.group_name(i)
            row[5].append(("[[%s|%s]]" % (anchor, text) if anchor else text) + " {{s|%s}}" % g.sid.get(i, "#" + i))
        first = {}
        for k in rows:                                    # the sheets of one file together, files in order of first use
            first.setdefault(k[0], len(first))
        body = [[r[0], r[1], r[2], ", ".join(dict.fromkeys(r[3])), _list(r[4], 6) if r[4] else "—",
                 "; ".join(dict.fromkeys(r[5]))] for k, r in sorted(rows.items(), key=lambda kv: first[kv[0][0]])]
        return [("h", 2, "4. Files, sheets and scripts", "files"),
                ("table", ["File", "Sheet", "Cells", "Used", "Columns", "Where"], body)]

    def section_detail(self, live):
        g = self.g
        g.hook = None
        try:
            checks = g.checks(live)[1:]
            cols = [g.columns(key, [i for i in g.order if g.top_of[i] == key]) for key in g.tops]
        finally:
            g.hook = self
        index = []
        for i in g.order:
            if i in g.fold_of:
                continue
            text, anchor = self.group_name(i)
            index.append(["[[s%s|%s]]" % (g.sid[i].replace(".", "-"), g.sid[i]), designer(self.t[i].kind),
                          "[[%s|%s]]" % (anchor, text) if anchor else text, "#%s" % i])
        legend = [
            "**Rule and pandas**, on each row, holds the step's exact rule (`when … → …` lines are read top down; "
            "the first true condition gives the value) and the same step in pandas, to paste into the steps "
            "notebook. Its first line gives the step number, the Designer tool and its ID, and whether the generated "
            "Python converts it exactly.",
            "In the pandas, `df` is the table flowing through the step; `s2_7` is step 2.7's output and `s2_7_true` "
            "its rows that pass; `src_…` is a file as loaded in the notebook's Load cell, and `FOLDER` is the "
            "workflow's folder. The steps notebook is laid out by Designer's branches; find a step here by its "
            "number (the index below lists them all).",
            "Rows show only the steps that change which rows or which values reach the output. The others (opening "
            "a file, a Select that only keeps or retypes columns, Block Until Done, a lookup's preparation, a settings "
            "value attached to every row) are listed under **housekeeping steps not shown** at the end of each "
            "table, with their pandas.",
            "**A1, A2…** in a status are Alteryx behaviours the Python assumes; they are listed in what to check."]
        inner = [("steps", [{"text": x} for x in legend], False),
                 ("p", "The pandas uses two helpers, because Alteryx compares text ignoring case and treats null and "
                       "the empty string separately:"), ("pre", HELPERS)]
        return [("h", 2, "5. Detail", "detail"),
                ("details", 3, "How to read the pandas", "legend", inner, False),
                ("details", 2, "Before relying on this", "check", checks, False)] + \
            [c for c in cols if c[0] == "details"] + \
            [("details", 3, "Every step by number: %d" % len(index), "index",
              [("table", ["Step", "Tool", "Told in", "Tool ID"], index)], False)]


def _and(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _letters(n):
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _span(sids):
    """Step numbers in small print: 1.3, or 1.3–1.5, or 1.3, 1.7 +4."""
    if len(sids) == 1:
        return sids[0]
    if len(sids) <= 3 and len({x.split(".")[0] for x in sids}) == 1:
        return "%s–%s" % (sids[0], sids[-1].split(".", 1)[1]) if len(sids) > 1 else sids[0]
    return "%s–%s (%d steps)" % (sids[0], sids[-1], len(sids))


def _cap(s):
    return s[:1].upper() + s[1:] if s else s


def _lower(s):
    return s[:1].lower() + s[1:] if s and not s[:2].isupper() else s


def _plain(text):
    return re.sub(r"\{\{\w\|(.+?)\}\}", r"\1", _md_plain(text))


def build(g):
    return Layout(g).build()
