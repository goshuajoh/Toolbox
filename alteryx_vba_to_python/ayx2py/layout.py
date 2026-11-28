"""The guide's page, for a reader who has never opened Alteryx Designer.

1. What this workflow does: one block per container (a short summary, then the files it opens and saves, and the
   programs it runs), with a contents list above it.
2. How each output is made: one numbered story per output, from its source files to the saved sheet, in run order.
   Work that several outputs share is told once, as a named working table, and each story links to it by name. A
   lookup brought into the main flow is described in the step that uses it; its own preparation sits folded just
   before that step. Steps that feed no output get one line at the end of their container.
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
from .guide import BLOCKERS, HELPERS, OUT_MODE, SOURCES, _list, _md_plain, designer, file_text, place

SIDE = {"Join": "R", "FindReplace": "S", "AppendFields": "S"}       # inputs that bring a lookup into the main flow
OUTS = {"Filter": ("True", "False"), "Join": ("Join", "Left", "Right"), "Unique": ("Unique", "Duplicates")}
PLAIN = {"True": "rows that pass", "False": "rows that fail", "Join": "matched rows",
         "Left": "rows only in the first input", "Right": "rows only in the second input",
         "Unique": "first row of each", "Duplicates": "duplicate rows"}
ENDS = ("Output",)                                   # what a story is told for
ATTACH = ("RunCommand", "Message")                    # joins the story whose rows it shares, or is a story itself
PLUMBING = ("Select", "Sort", "BlockUntilDone", "ParallelBlockUntilDone", "RecordID", "Cleanse")
VERB = [("Filter", "filters rows"), ("Join", "matches tables"), ("FindReplace", "looks values up"),
        ("AppendFields", "matches tables"), ("Summarize", "totals rows"), ("Formula", "calculates columns"),
        ("MultiFieldFormula", "calculates columns"), ("Union", "stacks tables"), ("Unique", "removes duplicates"),
        ("Transpose", "reshapes tables"), ("CrossTab", "reshapes tables")]


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


def worked_from(node, text=""):
    cols = expr.fields(node) if node is not None else re.findall(r"\[([^\[\]]+)\]", text)
    cols = list(dict.fromkeys(c for c in cols if c.lower() != "_currentfield_"))
    return "worked out from %s (rule in the details)" % _list(cols, 4) if cols else "a fixed value (rule in the details)"


# ----------------------------------------------------------------------------- groups of steps
class Group:
    """A story (one output), a working table (shared by several), or the steps of a container that feed no output."""

    def __init__(self, kind, key, tools, rank):
        self.kind, self.key = kind, key              # kind: "story", "table" or "unused"; key: top container
        self.tools = sorted(tools, key=rank)
        self.ends, self.main, self.sides, self.side_of = [], [], {}, {}
        self.title, self.anchor, self.num = "", "", {}


class Side:
    def __init__(self, tools, target):
        self.tools, self.target = tools, target
        self.title, self.anchor, self.num = "", "", {}


class Layout:
    def __init__(self, g):
        self.g, self.wf, self.t = g, g.wf, g.t
        self.rk = lambda i: (self.wf.rank.get(i, 10 ** 9), g.pos.get(i, 0))
        self.ctx = None                                  # (group, side or None) whose step numbers are local
        self.groups, self.group_of, self.names = [], {}, Counter()
        for key in g.tops:
            self.split(key)
        for grp in self.groups:
            self.shape(grp)
        for grp in sorted(self.groups, key=lambda x: self.rk(x.tools[-1]) if x.tools else (0, 0)):
            self.name(grp)

    # ---- which steps go into which story
    def steps_of(self, key):
        g = self.g
        return [i for i in g.order if g.top_of[i] == key and i not in g.fold_of]

    def same(self, i, j):
        return self.g.top_of.get(i) == self.g.top_of.get(j)

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
            best = max(anc, key=lambda o: (len(a & anc[o]), -self.rk(o)[0]), default=None)
            token[m] = best if best is not None and a & anc[best] else m
        sig = {}
        for i in sorted(tools, key=self.rk, reverse=True):
            s = {token[i]} if i in token else set()
            for e in self.wf.outs.get(i, []):
                c = e[2]
                if c in inside:
                    s |= sig.get(c, set())
                elif c in g.top_of and c not in g.fold_of:
                    s.add(("exit", g.top_of[c]))
            sig[i] = frozenset(s)
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
                    if y in inside and y not in seen and y in sig and sig[y] == sig[i] and y not in stories.get(y, ()):
                        if y in rest:
                            seen.add(y)
                            todo.append(y)
            tables.append(comp)
        made = [Group("story", key, ids, self.rk) for _, ids in sorted(stories.items(), key=lambda kv: self.rk(kv[0]))]
        for grp, (end, _) in zip(made, sorted(stories.items(), key=lambda kv: self.rk(kv[0]))):
            grp.ends = [i for i in grp.tools if token.get(i) == end and (i == end or t[i].kind in ATTACH)]
        made += [Group("table", key, ids, self.rk) for ids in tables]
        if unused:
            made.append(Group("unused", key, unused, self.rk))
        for grp in made:
            for i in grp.tools:
                self.group_of[i] = grp
        self.groups += made

    def shape(self, grp):
        """Main steps (on the way to the result) and side preparations (a lookup's own steps), in run order."""
        t, mem = self.t, set(grp.tools)
        if grp.kind == "unused":
            grp.main = list(grp.tools)
            grp.num = {i: n for n, i in enumerate(grp.main, 1)}
            return
        if grp.kind == "table":
            grp.ends = [i for i in grp.tools if any(e[2] not in mem for e in self.wf.outs.get(i, []))] or grp.tools[-1:]
        main, todo = set(grp.ends), list(grp.ends)
        while todo:
            x = todo.pop()
            letter = SIDE.get(t[x].kind)
            for e in self.wf.ins.get(x, []):
                if e[0] in mem and e[0] not in main and not (letter and e[3][:1].upper() == letter):
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
        grp.num = {i: n for n, i in enumerate(grp.main, 1)}
        for side in grp.sides.values():
            side.num = {i: n for n, i in enumerate(side.tools, 1)}

    # ---- names
    def unique(self, text):
        self.names[text] += 1
        return text if self.names[text] == 1 else "%s (%d)" % (text, self.names[text])

    def source_name(self, i, seen=None):
        """What a chain starts from, in a few words."""
        t, g = self.t[i], self.g
        if t.kind == "Input":
            return file_text(t.ir["file"], t.ir["options"]).replace("`", "").replace(" › sheet ", " › ").split(" › from row")[0]
        if t.kind == "TextInput":
            return "a typed table"
        if t.kind == "Directory":
            return "the file list of %s" % t.ir["dir"]
        if t.kind == "DynamicInput":
            return "the files listed in [%s]" % t.ir["field"]
        e = g.L.primary(i) or (self.wf.ins.get(i) or [None])[0]
        if e is None:
            return designer(t.kind)
        if e[0] in g.fold_of:
            return _md_plain(g.read_label(e[0]))
        other = self.group_of.get(e[0])
        if other is not None and other is not self.group_of.get(i) and other.title:
            return other.title
        seen = seen or set()
        if e[0] in seen or len(seen) > 400:
            return designer(t.kind)
        seen.add(e[0])
        return self.source_name(e[0], seen)

    def phrase(self, i):
        t, ir = self.t[i], self.t[i].ir
        c = self.g.cards.get(i, {})
        cols = lambda xs: _list(xs, 2).replace("[", "").replace("]", "")       # names carry no [column] marks
        k = t.kind
        try:
            if k == "Filter":
                return "split on " + (cols(c.get("reads", [])) or "a test")
            if k == "Join":
                return "matched on " + cols(ir["left"])
            if k == "Summarize":
                group = [f for f, a, _, _ in ir["fields"] if a == "GroupBy"]
                return "totals by " + cols(group) if group else "totals"
            if k == "Union":
                return "stacked"
            if k == "Unique":
                return "duplicates removed on " + cols(ir["fields"])
            if k in ("Formula", "MultiFieldFormula"):
                return "with " + cols([w[0] for w in c.get("writes", [])]) + " calculated"
            if k == "FindReplace":
                return "with " + cols(ir.get("append") or [ir["find"]]) + " looked up"
            if k == "AppendFields":
                return "with columns added"
            if k in ("Transpose", "CrossTab"):
                return "reshaped"
            if k == "Sample":
                return "first rows only"
        except (KeyError, TypeError):
            pass
        return designer(k).lower()

    def derived(self, ids, last):
        first = next((i for i in ids if not any(e[0] in ids for e in self.wf.ins.get(i, []))), ids[0])
        base = self.source_name(first)
        mean = next((i for i in reversed(ids) if self.t[i].kind not in PLUMBING and self.t[i].kind not in SOURCES), None)
        text = base if mean is None or self.t[mean].kind in SOURCES else "%s, %s" % (base, self.phrase(mean))
        return text if len(text) <= 80 else text[:78].rstrip() + "…"

    def name(self, grp):
        t = self.t
        if grp.kind == "unused":
            grp.title, grp.anchor = "Steps that feed no output", "u%d" % self.g.cnum[grp.key]
        elif grp.kind == "story":
            end = grp.ends[0] if grp.ends else grp.tools[-1]
            grp.anchor = "o%s" % end
            if t[end].kind == "Output":
                path, sheet, cells, _ = place(t[end].ir["file"], t[end].ir["options"])
                base = os.path.basename(path.replace("\\", "/")) or path
                title = base + (" › " + sheet if sheet else "") + (" › " + cells.upper() if cells else "")
            elif t[end].kind == "RunCommand":
                title = "Runs " + os.path.basename((t[end].ir.get("command") or "a program").replace("\\", "/"))
            else:
                title = "Check: " + self.derived(grp.tools, end)
            grp.title = self.unique(title)
        else:
            note = next((t[i].note for i in reversed(grp.ends + grp.tools) if t[i].note), "")
            grp.anchor = "w%s" % grp.tools[-1]
            grp.title = self.unique(note if note and len(note) <= 80 else note[:78] + "…" if note else
                                    self.derived(grp.tools, grp.tools[-1]))
        for side in grp.sides.values():
            last = side.tools[-1]
            note = next((t[i].note for i in reversed(side.tools) if t[i].note), "")
            side.anchor = "p%s" % last
            side.title = self.unique(note if note else "%s, prepared" % self.derived(side.tools, last).split(", ")[0])

    # ---- how one step refers to another (the Guide calls these while hook is set)
    def where(self, i):
        """(text, anchor) naming the place a step is told."""
        g = self.g
        if i in g.fold_of:
            return self.read_chip(i), None
        grp = self.group_of.get(i)
        if grp is None:
            return "#%s" % i, None
        cgrp, cside = self.ctx if self.ctx else (None, None)
        side = grp.side_of.get(i)
        if grp is cgrp:
            if side is None:
                return "step %d%s" % (grp.num[i], " of the story" if cside else ""), "s" + g.sid[i].replace(".", "-")
            if side is cside:
                return "step %d" % side.num[i], "s" + g.sid[i].replace(".", "-")
            return "**%s**" % side.title, side.anchor
        if grp.kind == "unused":
            return "steps that feed no output (container %d)" % g.cnum[grp.key], grp.anchor
        far = "" if cgrp is None or cgrp.key == grp.key else " (container %d)" % g.cnum[grp.key]
        return "**%s**%s" % (grp.title, far), grp.anchor

    def read_chip(self, i):
        """A file read inside the step that uses it, as a file chip: {{f|Config.xlsx › Clients}}."""
        first = self.t[self.g.L.stages[self.g.fold_of[i]][0]]
        if first.kind == "TextInput":
            return _md_plain(self.g.read_label(i))
        return "{{f|%s}}" % file_text(first.ir["file"], first.ir["options"]).replace("`", "").replace(" › sheet ", " › ")

    def ref(self, i):
        text, anchor = self.where(i)
        if anchor is None:
            return text
        m = re.fullmatch(r"\*\*(.+?)\*\*(.*)", text)
        return "**[[%s|%s]]**%s" % (anchor, m.group(1), m.group(2)) if m else "[[%s|%s]]" % (anchor, text)

    def src(self, e):
        text, kind = self.ref(e[0]), self.t[e[0]].kind
        if kind in OUTS and e[1] in PLAIN:
            return "%s (%s)" % (text, PLAIN[e[1]])
        if kind in BLOCKERS:
            return "%s (%s)" % (text, re.sub(r"(\D+)(\d+)", r"\1 \2", e[1]).lower())
        return text

    # ---- one step, as the reader sees it
    def step(self, i, prev=None):
        try:
            return self._step(i, prev)
        except Exception as exc:  # noqa: BLE001 - one step must not stop the page; reported in Detail
            g, t = self.g, self.t[i]
            return {"anchor": "s" + g.sid[i].replace(".", "-"), "tool": designer(t.kind), "note": t.note,
                    "small": "step %s · tool #%s" % (g.sid[i], i), "meta": [], "split": "", "hand": "",
                    "text": "Runs %s (could not describe: %s; its settings are in the details)." % (
                        designer(t.kind), g.failed(i, exc)), "fold": g.safe_fold(i)}

    def _step(self, i, prev=None):
        g, t = self.g, self.t[i]
        c = g.cards[i]
        try:
            text, meta = self.say(i)
        except Exception as exc:  # noqa: BLE001 - one step must not stop the page; reported in Detail
            text, meta = "Runs %s (could not describe: %s; its settings are in the details)." % (
                designer(t.kind), g.failed(i, exc)), []
        grp, side = self.ctx
        mem = set(side.tools if side else grp.main)
        e = g.L.primary(i) or ((self.wf.ins.get(i) or [None])[0] if t.kind != "Union" and len(self.wf.ins.get(i, [])) == 1
                               else None)
        if e is not None and t.kind not in SOURCES and e[0] not in g.fold_of:
            if e[0] not in mem:
                meta.insert(0, ("Starts from", self.src(e)))
            elif e[0] != prev or (self.t[e[0]].kind in OUTS and len(set(x[1] for x in self.wf.outs.get(e[0], []))) > 1):
                meta.insert(0, ("Takes", self.src(e)))
        split = self.split_line(i, mem)
        hand = "outside Alteryx: write by hand" if t.kind == "RunCommand" else \
            "not converted: write by hand" if t.status == "stub" else ""
        return {"anchor": "s" + g.sid[i].replace(".", "-"), "tool": designer(t.kind), "note": t.note,
                "small": "step %s · tool #%s" % (g.sid[i], i), "text": text, "meta": meta, "split": split,
                "hand": hand, "fold": g.safe_fold(i), "warn": bool(c.get("notes")) and t.kind == "FindReplace"}

    def split_line(self, i, mem):
        g, t = self.g, self.t[i]
        groups = g.outs(i)
        side = self.ctx[1] if self.ctx else None
        if side is not None:                             # the step a side feeds is the one right after the fold-out
            groups = {a: [d for d in v if d != side.target] for a, v in groups.items()}
            groups = {a: v for a, v in groups.items() if v}
        if len(groups) == 1 and all(d in mem for v in groups.values() for d in v) and (
                t.kind == "Filter" or t.kind not in OUTS):
            return ""                                    # the sentence says it: keeps only these rows, for the next step
        if t.kind in OUTS and len(groups):
            parts = []
            for a in OUTS[t.kind]:
                dst = list(dict.fromkeys(self.ref(d) for d in groups.get(a, [])))
                parts.append("**%s** → %s" % (PLAIN[a], ", ".join(dst) if dst else "dropped"))
            return " · ".join(parts)
        far = list(dict.fromkeys(self.ref(d) for v in groups.values() for d in v if d not in mem))
        if far and t.kind not in ("Output",):
            near = [d for v in groups.values() for d in v if d in mem]
            return ("also used by " if near else "used by ") + ", ".join(far)
        return ""

    def say(self, i):
        """(sentence, [(label, text)]) for a step in the main view."""
        g, t, ir, k = self.g, self.t[i], self.t[i].ir, self.t[i].kind
        c = g.cards[i]
        meta = []
        cols = lambda xs, n=8: " ".join("[%s]" % x for x in list(dict.fromkeys(xs))[:n]) + (
            " +%d more" % (len(set(xs)) - n) if len(set(xs)) > n else "")
        if k == "Input":
            path, sheet, cells, line = place(ir["file"], ir["options"])
            chip = file_text(ir["file"], ir["options"]).replace("`", "").replace(" › sheet ", " › ")
            text = "Opens {{f|%s}}." % chip
            names = [w[0] for w in c["writes"]]
            if names:
                meta.append(("Columns", cols(names, 6)))
        elif k == "Output":
            mode = OUT_MODE.get(ir["options"].get("OutputOption", ""), "")
            chip = file_text(ir["file"], ir["options"]).replace("`", "").replace(" › sheet ", " › ")
            text = "Saves the table to {{o|%s}}%s." % (chip, " (%s)" % mode if mode else "")
        elif k == "Formula":
            have = g.input_columns(i)
            adds = []
            for name, _, _, e in ir["fields"]:
                said = words(e["ast"]) if e["ast"] is not None else None
                adds.append("**%s** = %s" % (name, said) if said else "**%s**: %s" % (name, worked_from(e["ast"], e["text"])))
            new = [f[0] for f in ir["fields"] if have is None or f[0].lower() not in have]
            old = [f[0] for f in ir["fields"] if have is not None and f[0].lower() in have]
            text = "Adds %s%s." % ("%d column%s" % (len(new), "" if len(new) == 1 else "s") if new else "",
                                   (" and changes %d" % len(old) if new else "Changes %d column%s" % (
                                       len(old), "" if len(old) == 1 else "s")) if old else "")
            if not new:
                text = text.replace("Adds ", "", 1)
            if have is None:
                text = "Sets %d column%s." % (len(adds), "" if len(adds) == 1 else "s")
            meta += [("Sets" if n == 0 else "", a) for n, a in enumerate(adds[:8])]
            if len(adds) > 8:
                meta.append(("", "+%d more columns (in the details)" % (len(adds) - 8)))
        elif k == "Filter":
            if ir["mode"] == "simple":
                cond = re.sub(r"\] is ", "] is ", g.cond_text(t))
            else:
                e = ir["expr"]
                cond = words(e["ast"]) if e["ast"] is not None else None
                cond = cond or "a test on %s holds (rule in the details)" % (_list(c["reads"], 4) or "the row")
            used = set(g.outs(i))
            if used >= {"True", "False"}:
                text = "Splits the rows: those where %s pass, the others fail." % cond
            elif used == {"False"}:
                text = "Removes the rows where %s." % cond
            else:
                text = "Keeps only the rows where %s." % cond
            if c["reads"]:
                meta.append(("Looks at", cols(c["reads"])))
        elif k == "Select":
            real = [f for f in ir["fields"] if f[0] != "*Unknown"]
            ren = [(f, r) for f, sel, r, _, _ in real if sel and r and r != f]
            drop = [f for f, sel, *_ in real if not sel]
            keep = [r or f for f, sel, r, _, _ in real if sel]
            text = g.short(i)[0].upper() + g.short(i)[1:] + "."
            if ren:
                meta.append(("Renames", ", ".join("[%s] → [%s]" % p for p in ren[:6]) + (" +%d more" % (len(ren) - 6)
                                                                                       if len(ren) > 6 else "")))
            if drop and ir["unknown"]:
                meta.append(("Removes", cols(drop)))
            if not ir["unknown"]:
                text = "Keeps only %d column%s%s." % (len(keep), "" if len(keep) == 1 else "s",
                                                      ", renaming %d" % len(ren) if ren else "")
                meta.append(("Keeps", cols(keep)))
        else:
            s = g.short(i)
            text = s[0].upper() + s[1:] + ("" if s.endswith(".") else ".")
            if c["reads"] and k not in ("DynamicInput", "Directory", "TextInput", "Message", "RunCommand"):
                meta.append(("Looks at", cols(c["reads"])))
            new = [w[0] for w in c["writes"]]
            if new and k not in ("DynamicInput", "Directory", "TextInput", "Join", "AppendFields"):
                meta.append(("Adds", cols(new)))
            elif new and k in ("Join", "AppendFields"):
                meta.append(("Renames", cols(new)))
            elif new:
                meta.append(("Columns", cols(new, 6)))
        if c.get("notes") and k == "FindReplace":
            meta.append(("Note", c["notes"][0]))
        return text, meta

    # ---- blocks
    def story(self, grp):
        g = self.g
        items, prev = [], None
        for i in grp.main:
            side = grp.sides.get(i)
            if side is not None:
                self.ctx = (grp, side)
                steps, p = [], None
                for x in side.tools:
                    steps.append(self.step(x, p))
                    p = x
                items.append({"side": True, "anchor": side.anchor, "title": "How %s is prepared (%d step%s)" % (
                    side.title, len(side.tools), "" if len(side.tools) == 1 else "s"), "steps": steps})
            self.ctx = (grp, None)
            items.append(self.step(i, prev))
            prev = i
        self.ctx = None
        intro = self.intro(grp)
        badge = "container %d · disabled in the saved file" % g.cnum[grp.key] if grp.key in g.off else ""
        kind = {"story": "output", "table": "table", "unused": "unused"}[grp.kind]
        return ("story", grp.anchor, kind, grp.title, badge, intro, items)

    def intro(self, grp):
        if grp.kind == "table":
            users = []
            for i in grp.tools:
                for e in self.wf.outs.get(i, []):
                    other = self.group_of.get(e[2])
                    if other is not None and other is not grp and other not in users:
                        users.append(other)
            names = ", ".join("**[[%s|%s]]**%s" % (u.anchor, u.title, "" if u.key == grp.key else
                                                   " (container %d)" % self.g.cnum[u.key]) for u in users)
            return "A working table: its rows are used by %s. Told once here; those stories start from it." % (
                names or "later steps")
        if grp.kind == "unused":
            return "These steps run, but nothing they make reaches a saved file."
        srcs = []
        for i in grp.tools:
            t = self.t[i]
            if t.kind in SOURCES or t.kind == "DynamicInput":
                srcs.append(self.source_name(i))
            for e in self.wf.ins.get(i, []):
                if e[0] in self.g.fold_of:
                    srcs.append(self.read_chip(e[0]))
                elif self.group_of.get(e[0]) not in (grp, None):
                    o = self.group_of[e[0]]
                    srcs.append("[[%s|%s]]" % (o.anchor, o.title))
        srcs = list(dict.fromkeys(srcs))
        n = len(grp.main) + sum(len(s.tools) for s in grp.sides.values())
        return "%d step%s, from %s." % (n, "" if n == 1 else "s", ", ".join(srcs[:6]) + (" and %d more" % (len(srcs) - 6)
                                                                                     if len(srcs) > 6 else "") or "—")

    def unused_block(self, grp):
        kinds = Counter(designer(self.t[i].kind) for i in grp.tools)
        self.ctx = (grp, None)
        steps, prev = [], None
        for i in grp.main:
            steps.append(self.step(i, prev))
            prev = i
        self.ctx = None
        line = "**Not used by any output:** %d step%s (%s). They run, but nothing they make is saved." % (
            len(grp.tools), "" if len(grp.tools) == 1 else "s", ", ".join("%s %d" % kv for kv in kinds.most_common(6)))
        return [("p", line), ("side", {"anchor": grp.anchor, "title": "The %d step%s" % (
            len(grp.tools), "" if len(grp.tools) == 1 else "s"), "steps": steps})]

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
                out.append(("RunCommand", " ".join(x for x in (t.ir.get("command"), t.ir.get("args")) if x), "", "",
                            "program run", i, None))
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
                                                                      for x in outputs if x.key == k) or "no output") +
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
                  ("p", "One story per output: only the steps on its path, in the order they run, from the files it "
                        "starts from to the saved sheet. Work that several outputs share is told once as a working "
                        "table, and the stories start from it by name. Click **Rule and pandas** under a step for its "
                        "exact rule and the same step in pandas.")]
        for key in g.tops:
            grps = sorted((x for x in self.groups if x.key == key), key=lambda x: self.rk(x.tools[-1]))
            if not grps:
                continue
            badge = " {{b|disabled in the saved file}}" if key in g.off else ""
            blocks.append(("h", 3, "Container %d. %s%s" % (g.cnum[key], g.cname(key), badge), "m%d" % g.cnum[key]))
            for grp in grps:
                if grp.kind == "unused":
                    continue
                blocks.append(self.story(grp))
            for grp in grps:
                if grp.kind == "unused":
                    blocks += self.unused_block(grp)
        return blocks

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
            if gb is ga:
                continue
            pairs.append((gb, ga))
        pairs = list(dict.fromkeys(pairs))
        for gb, ga in pairs[:40]:
            items.append("**[[%s|%s]]** finishes before **[[%s|%s]]** starts (a Block Until Done tool forces this)." % (
                gb.anchor, gb.title, ga.anchor, ga.title))
        if len(pairs) > 40:
            items.append("… and %d more orders forced the same way." % (len(pairs) - 40))
        for x in (x for x in self.groups if x.kind == "story"):
            for i in x.ends:
                if self.t[i].kind == "RunCommand" and x.ends[0] != i:
                    ir = self.t[i].ir
                    words_ = ("%s %s" % (ir.get("command") or "", ir.get("args") or "")).split()
                    prog = next((w for w in words_ if re.search(r"\.(vbs|bat|cmd|ps1|py|js)$", w, re.I)),
                                (words_ or ["the program"])[0])
                    prog = os.path.basename(prog.strip('"').replace("\\", "/"))
                    items.append("In **[[%s|%s]]**, `%s` runs after the file is saved." % (x.anchor, x.title, prog))
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
                ("p", "Inside a story, steps run in the order shown. What must finish before what:"),
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
            "**Rule and pandas**, under each step, holds the step's exact rule (`when … → …` lines are read top down; "
            "the first true condition gives the value) and the same step in pandas, to paste into the steps "
            "notebook. Its first line gives the step number, the Designer tool and its ID, and whether the generated "
            "Python converts it exactly.",
            "In the pandas, `df` is the table flowing through the step; `s2_7` is step 2.7's output and `s2_7_true` "
            "its rows that pass; `src_…` is a file as loaded in the notebook's Load cell, and `FOLDER` is the "
            "workflow's folder. The steps notebook is laid out by Designer's branches; find a step here by its "
            "number (the index below lists them all).",
            "**A1, A2…** in a status are Alteryx behaviours the Python assumes; they are listed in what to check."]
        inner = [("steps", [{"text": x} for x in legend], False),
                 ("p", "The pandas uses two helpers, because Alteryx compares text ignoring case and treats null and "
                       "the empty string separately:"), ("pre", HELPERS)]
        return [("h", 2, "5. Detail", "detail"),
                ("details", 3, "How to read the pandas", "legend", inner, False),
                ("details", 2, "Before relying on this", "check", checks, False)] + \
            [c for c in cols if c[0] == "details"] + \
            [("details", 3, "Every step by number (%d)" % len(index), "index",
              [("table", ["Step", "Tool", "Told in", "Tool ID"], index)], False)]


def _and(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def build(g):
    return Layout(g).build()
