"""Logic specification of a workflow: every rule in full, each said once.

Three levels of de-duplication keep it short enough to read on screen:
  1. a long rule line used by several tools is printed once as R<n> and referred to by name;
  2. a sub-chain of tools repeated with identical rules and wiring is a block B<n>: its first copy
     is printed in full (lines tagged B<n>), every further copy is one line;
  3. field lists are part of the rule lines, so a repeated list is also named once.
Nothing is shortened or cut.
"""
import hashlib
import textwrap
from collections import Counter, defaultdict

from .workflow import IGNORED, STATUS, _id_key

ANCHOR = {"True": "T", "False": "F", "Left": "L", "Join": "J", "Right": "R", "Unique": "U", "Duplicates": "D",
          "Output": "", "Input": "", "Targets": "T", "Source": "S"}


# ----------------------------------------------------------------------------- one tool -> rule lines
def _names(items):
    return ", ".join(i for i in items if i)


def _select_lines(fields, unknown):
    real = [f for f in fields if f[0] != "*Unknown"]
    out = []
    ren = ["%s→%s" % (f, r) for f, sel, r, _, _ in real if sel and r and r != f]
    if ren:
        out.append("rename: " + _prefix_groups([(f, r) for f, sel, r, _, _ in real if sel and r and r != f]))
    by_type = defaultdict(list)
    for f, sel, r, typ, size in real:
        if sel and typ:
            by_type[typ + ("(%s)" % size if size and typ.endswith("String") else "")].append(r or f)
    for typ, names in by_type.items():
        out.append("type %s: %s" % (typ, _names(names)))
    keep = [f for f, sel, *_ in real if sel]
    drop = [f for f, sel, *_ in real if not sel]
    if not unknown:
        out.append("keeps only, in this order: " + _names(keep))
    elif drop:
        out.append("drops: " + _names(drop))
    return out


def _prefix_groups(pairs):
    """'Right_X→SYSB_X' for many X becomes 'Right_* → SYSB_* for: X, Y, Z' (nothing is lost)."""
    groups, rest = defaultdict(list), []
    for old, new in pairs:
        if "_" in old:
            pre, suf = old.split("_", 1)
            if new.endswith(suf) and new != old:
                groups[(pre + "_", new[:len(new) - len(suf)])].append(suf)
                continue
        rest.append("%s→%s" % (old, new))
    out = []
    for (q, p), sufs in groups.items():
        if len(sufs) >= 4:
            out.append("%s* → %s* for: %s" % (q, p, ", ".join(sufs)))
        else:
            rest += ["%s%s→%s%s" % (q, s, p, s) for s in sufs]
    return "; ".join(out + [", ".join(rest)] if rest else out)


def describe(t, canonical=False):
    """Rule lines of one tool. canonical=True gives the text used to recognise identical tools."""
    k, ir = t.kind, t.ir
    ex = (lambda e: e["canon"]) if canonical else (lambda e: e["text"])
    out = []
    if k in ("Input", "Output", "DynamicInput"):
        opts = "; ".join("%s=%s" % kv for kv in sorted(ir["options"].items()) if kv[1] != "")
        if k == "DynamicInput":
            out.append("one file per row, path in [%s]; read like: %s" % (ir["field"], ir["file"]))
        else:
            out.append("file: " + ir["file"])
        if opts:
            out.append("options: " + opts)
        if ir.get("types"):
            by = defaultdict(list)
            for n, ty in ir["types"].items():
                by[ty].append(n)
            out.append("fields recorded: " + "; ".join("%s: %s" % (ty, _names(ns)) for ty, ns in by.items()))
    elif k == "Directory":
        out.append("folder: %s; files: %s" % (ir["dir"], ir["spec"]))
    elif k == "TextInput":
        out.append("fields: " + _names(ir["columns"]))
        out += ["row: " + " | ".join("" if c is None else c for c in r) for r in ir["rows"]]
    elif k == "Select":
        out += _select_lines(ir["fields"], ir["unknown"])
    elif k == "Formula":
        out += ["%s [%s] = %s" % (n, typ, ex(e)) for n, typ, _, e in ir["fields"]]
    elif k == "Filter":
        out.append("%s %s %s" % (ir["field"], ir["operator"], ir["operand"]) if ir["mode"] == "simple" else ex(ir["expr"]))
    elif k == "Join":
        out.append("keys: Left %s = Right %s" % (_names(ir["left"]), _names(ir["right"])))
        out += _select_lines(ir["fields"], ir["unknown"])
    elif k == "AppendFields":
        out += _select_lines(ir["fields"], ir["unknown"]) or ["every Target row × every Source row"]
    elif k == "Union":
        out.append("by %s%s" % ("name" if ir["mode"] == "ByName" else ir["mode"],
                                "; output order " + " ".join(ir["order"]) if ir["order"] else ""))
    elif k == "Summarize":
        group = [f for f, a, r, _ in ir["fields"] if a == "GroupBy" and r in (None, f)]
        other = []
        for f, a, r, o in ir["fields"]:
            if a == "GroupBy" and r in (None, f):
                continue
            extra = "; ".join("%s=%r" % kv for kv in sorted(o.items()) if kv[1])
            other.append("%s(%s)→%s" % (a, f, r) + (" {%s}" % extra if extra else ""))
        if group:
            out.append("group by: " + _names(group))
        if other:
            out.append("; ".join(other))
    elif k == "Sort":
        out.append("by: " + ", ".join("%s %s" % (f, "asc" if a else "desc") for f, a in ir["fields"]))
    elif k == "Unique":
        out.append("on: " + _names(ir["fields"]))
    elif k == "Sample":
        out.append("%s %s%s" % (ir["mode"], ir["n"], " per " + _names(ir["group"]) if ir["group"] else ""))
    elif k == "RecordID":
        out.append("%s from %s, %s%s, %s" % (ir["name"], ir["start"], ir["type"],
                                             "(%s)" % ir["size"] if ir["size"] else "", "last" if ir["last"] else "first"))
    elif k == "Transpose":
        out.append("key fields: " + _names(ir["keys"]))
        out.append("data fields: " + _names(ir["data"]) + (" + all other fields" if ir["unknown"] else ""))
    elif k == "MultiFieldFormula":
        how = ("new field %s%s" % (ir["addon"], " as prefix" if ir["prefix"] else " as suffix")) if ir["copy"] else "in place"
        out.append("fields (%s%s; %s%s): %s" % (ir["group"], " + all other" if ir["unknown"] else "", how,
                                                 "; as %s" % ir["out_type"] if ir["out_type"] else "", _names(ir["fields"])))
        out.append("each = " + ex(ir["expr"]))
    elif k == "FindReplace":
        how = {"FindBegin": "starts with", "FindAny": "contains", "FindWhole": "equals"}.get(ir["mode"], ir["mode"])
        line = "Target.[%s] %s Source.[%s]%s%s; " % (ir["find"], how, ir["search"], ", ignoring case" if ir["nocase"] else "",
                                                     ", whole words" if ir["whole"] else "")
        line += ("append: " + _names(ir["append"])) if ir["replace_mode"] == "Append" else \
            "replace found text with Source.[%s]%s" % (ir["replace"], " (every match)" if ir["multiple"] else "")
        out.append(line)
    elif k == "DateTime":
        out.append("[%s] %s format %s → [%s]" % (ir["field"], "written with" if ir["to_string"] else "parsed with",
                                                  ir["format"], ir["out"]))
    elif k == "Cleanse":
        on = [o if v is True else "%s=%s" % (o, v) for o, v in ir["options"].items() if v]
        out.append("fields: " + _names(ir["fields"]))
        out.append("options: " + (_names(on) or "none") + "   [raw: " +
                   "; ".join("%s=%s" % kv for kv in sorted(ir["raw"].items()) if not kv[0].startswith("List Box")) + "]")
    elif k == "Message":
        out.append("%s, %s: %s%s" % (ir["when"], ir["type"], ex(ir["text"]),
                                     " when " + ex(ir["filter"]) if ir["filter"] else ""))
    elif "raw" in ir:
        out.append(ir["raw"])
    return [line for line in out if line]


def signature(t):
    return hashlib.sha1((t.kind + "\n" + "\n".join(describe(t, True))).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- repeated sub-chains
def find_blocks(wf, sig, min_size=3, rounds=4):
    """Groups of tools that repeat with the same rules and the same wiring.
    -> list of blocks; a block is a list of copies; a copy is a list of tool ids (aligned by position)."""
    ids = wf.ordered()
    nbrs = defaultdict(list)
    for i in ids:
        for s, sa, d, da, _ in wf.outs.get(i, ()):
            nbrs[s].append(((">", sa, da), d))
            nbrs[d].append((("<", sa, da), s))
    colours = [dict(sig)]
    for _ in range(rounds):
        prev = colours[-1]
        colours.append({i: hashlib.sha1(repr((prev[i], sorted((key, prev[n]) for key, n in nbrs[i]))).encode()).hexdigest()
                        for i in ids})          # hashlib, not hash(): the result must not change between runs
    deep, assigned, blocks = colours[-1], set(), []

    def grow(seeds):
        copies, used, at = [[s] for s in seeds], set(seeds), 0
        while at < len(copies[0]):
            cands = []
            for c in copies:
                d = defaultdict(list)
                for key, n in nbrs[c[at]]:
                    if n not in assigned and n not in used:
                        d[(key, sig[n])].append(n)
                cands.append(d)
            for key2 in sorted(cands[0], key=repr):
                lists = [sorted(set(d.get(key2, [])), key=lambda n: (deep[n], wf.rank[n])) for d in cands]
                if len({len(x) for x in lists}) != 1:
                    continue
                for tup in zip(*lists):
                    if len(set(tup)) == len(tup) and not used & set(tup):
                        used |= set(tup)
                        for c, n in zip(copies, tup):
                            c.append(n)
            at += 1
        return copies

    for level in range(rounds, -1, -1):
        groups = defaultdict(list)
        for i in ids:
            if i not in assigned:
                groups[colours[level][i]].append(i)
        # widest groups first: four copies of 48 tools beat two pairs of 49
        for g in sorted(groups.values(), key=lambda g: (-len(g), wf.rank[g[0]])):
            g = [i for i in g if i not in assigned]
            if len(g) < 2:
                continue
            copies = grow(g)
            if len(copies[0]) >= min_size:
                blocks.append(copies)
                assigned.update(i for c in copies for i in c)
    blocks.sort(key=lambda b: min(wf.rank[i] for i in b[0]))
    return blocks


# ----------------------------------------------------------------------------- the document
def _ref(wf, e, two_inputs):
    src = "#" + e[0]
    a = ANCHOR.get(e[1], e[1])
    if a and wf.tools[e[0]].kind in ("Filter", "Join", "Unique", "ParallelBlockUntilDone", "BlockUntilDone"):
        src += "." + a
    if two_inputs:
        src = ANCHOR.get(e[3], e[3]) + "←" + src
    return src


def build(wf, width=150, include_disabled=False):
    tools = wf.tools
    ordered = wf.ordered()
    sig = {i: signature(tools[i]) for i in ordered}
    lines_of = {i: describe(tools[i]) for i in ordered}

    # level 2: blocks
    blocks = find_blocks(wf, sig)
    block_of = {}                              # tool id -> (block no, copy no)
    for b, copies in enumerate(blocks, 1):
        for c, ids in enumerate(copies):
            for i in ids:
                block_of[i] = (b, c)
    printed = [i for i in ordered if block_of.get(i, (0, 0))[1] == 0]      # tools whose rules are printed

    # level 1: rule lines used by several printed tools
    use = defaultdict(list)
    for i in printed:
        for n, line in enumerate(describe(tools[i], True)):
            if len(line) > 60:
                use[line].append((i, n))
    rule_name, rule_text = {}, []
    for line, places in use.items():
        if len({i for i, _ in places}) > 1:
            rule_name[line] = "R%d" % (len(rule_text) + 1)
            i, n = places[0]
            rule_text.append((rule_name[line], lines_of[i][n], [p[0] for p in places]))

    def wrap(s, indent, hang="    "):
        if not width:
            return [indent + s]
        return textwrap.wrap(s, width, initial_indent=indent, subsequent_indent=indent + hang,
                             break_on_hyphens=False, break_long_words=False) or [indent]

    def tool_lines(i):
        t = tools[i]
        two = len({e[3] for e in wf.ins.get(i, ())}) > 1
        head = "#%s %s" % (i, t.kind)
        if wf.ins.get(i):
            first = sorted(wf.ins[i], key=lambda e: e[3][:1] not in ("L", "T")) if two else wf.ins[i]
            head += " <" + ", ".join(_ref(wf, e, two) for e in first)
        if t.status == "stub":
            head += "  !! NOT CONVERTED: " + "; ".join(t.reasons())
        body = []
        for n, (line, canon) in enumerate(zip(lines_of[i], describe(t, True))):
            if canon in rule_name:
                lead = line.split(" = ")[0] + " = " if t.kind == "Formula" else ""
                body.append(lead + rule_name[canon])
            else:
                body.append(line)
        if t.note and t.note not in " ".join(lines_of[i]):
            body.append('note: "%s"' % t.note)
        tag = "B%d " % block_of[i][0] if i in block_of else ""
        one = head + "  : " + "  |  ".join(body)
        if body and (len(body) == 1 or len(tag) + len(one) <= (width or 10 ** 9)):
            return wrap(one, tag)
        out = wrap(head, tag)
        for line in body:
            out += wrap(line, tag + "    ")
        return out

    def copy_line(b, c):
        ids, first = blocks[b - 1][c], blocks[b - 1][0]
        inside = set(ids)
        feeds = sorted({"#%s→#%s" % (e[0], e[2]) for i in ids for e in wf.ins.get(i, ()) if e[0] not in inside})
        to = sorted({"#%s→#%s" % (e[0], e[2]) for i in ids for e in wf.outs.get(i, ()) if e[2] not in inside})
        deltas = {int(x) - int(y) for x, y in zip(ids, first)} if all(x.isdigit() for x in ids + first) else {None, 0}
        how = "each tool ID = B%d's %+d" % (b, deltas.pop()) if len(deltas) == 1 else \
            "tools " + " ".join("#%s=#%s" % (y, x) for x, y in zip(ids, first))
        return "[B%d copy %d of %d] %d tools, same rules and wiring as block B%d; %s; fed by %s; feeds %s" % (
            b, c + 1, len(blocks[b - 1]), len(ids), b, how, ", ".join(feeds) or "nothing", ", ".join(to) or "nothing")

    # ---- flows, by container, in dependency order
    sections, sect_order = defaultdict(list), []
    done_copy = set()
    for i in ordered:
        t = tools[i]
        if t.where not in sections:
            sect_order.append(t.where)
        if i in block_of and block_of[i][1] > 0:
            if block_of[i] not in done_copy:
                done_copy.add(block_of[i])
                sections[t.where] += wrap(copy_line(*block_of[i]), "", "        ")
            else:
                sections[t.where]            # keeps the container listed
            continue
        sections[t.where] += tool_lines(i)

    # ---- header
    live = [tools[i] for i in ordered]
    status = Counter((t.kind, t.status) for t in live)
    kinds = Counter(t.kind for t in tools.values())
    out = ["LOGIC SPECIFICATION   source: %s" % wf.source, "sha256: %s" % wf.sha256,
           "tools: %d (%d run, %d in disabled containers, %d notes/browses)   connections: %d" % (
               len(tools), len(live), sum(1 for t in tools.values() if t.disabled and t.kind not in IGNORED),
               sum(1 for t in tools.values() if t.kind in IGNORED and not t.disabled), len(wf.edges)),
           "kinds: " + ", ".join("%s %d" % kv for kv in kinds.most_common()), ""]
    tot = Counter(t.status for t in live)
    out += ["CONVERSION TO PYTHON: %d exact, %d with caveats, %d not converted (stubs that raise)" % (
        tot["exact"], tot["caveat"], tot["stub"])]
    for st, label in (("stub", "not converted"), ("caveat", "with caveats"), ("exact", "exact")):
        ks = sorted({k for (k, s) in status if s == st})
        if ks:
            out += wrap("%s: %s" % (label, ", ".join("%s %d" % (k, status[(k, st)]) for k in ks)), "  ")
    by_kind, by_tool = defaultdict(int), defaultdict(list)
    for t in live:
        own = t.problems if t.status == "stub" else t.caveats
        if t.status == "stub" and not own:
            by_kind["!! %s: no converter for this tool kind; a stub that raises is generated" % t.kind] += 1
        elif t.status != "stub" and t.kind in STATUS and STATUS[t.kind][1]:
            by_kind["%s: %s" % (t.kind, STATUS[t.kind][1])] += 1
        for r in own:
            by_tool[("!! " if t.status == "stub" else "") + "%s: %s" % (t.kind, r)].append(t.id)
    if by_kind or by_tool:
        out += ["", "WHAT TO CHECK:"]
        for note, n in sorted(by_kind.items(), key=lambda kv: (not kv[0].startswith("!!"), kv[0])):
            out += wrap("%s   [%d tools]" % (note, n), "  ")
        for note, ids in sorted(by_tool.items(), key=lambda kv: (not kv[0].startswith("!!"), kv[0])):
            out += wrap("%s   [%d: %s]" % (note, len(ids), " ".join("#" + i for i in ids)), "  ")
    if wf.cyclic:
        out += ["", "!! tools in a loop (order not determined): " + " ".join("#" + i for i in wf.cyclic)]

    out += ["", "INPUTS:"]
    for i in ordered:
        if tools[i].kind in ("Input", "DynamicInput", "Directory", "TextInput") and not wf.ins.get(i) or \
                tools[i].kind == "DynamicInput":
            what = tools[i].ir.get("file") or tools[i].ir.get("dir") or "%d typed rows" % len(tools[i].ir.get("rows", []))
            out += wrap("#%s %s  %s   [%s]" % (i, tools[i].kind, what, tools[i].where), "  ")
    out += ["OUTPUTS AND SIDE EFFECTS:"]
    for i in ordered:
        if tools[i].kind in ("Output", "RunCommand", "Message") or tools[i].kind.startswith("Macro:"):
            what = tools[i].ir.get("file") or "; ".join(lines_of[i])
            out += wrap("#%s %s  %s   [%s]" % (i, tools[i].kind, what, tools[i].where), "  ")
    dead = [i for i in ordered if not wf.outs.get(i) and tools[i].kind not in ("Output", "RunCommand", "Message")
            and not tools[i].kind.startswith("Macro:")]
    if dead:
        out += wrap("streams that end without an output (only browsed, or unused): " + " ".join("#" + i for i in dead), "")
    if wf.order_edges:
        out += ["RUN ORDER forced by Block Until Done (before → after):"]
        out += wrap(", ".join("#%s→#%s" % p for p in wf.order_edges), "  ")
    off = [(p, o) for p, o in wf.containers if o]
    if off:
        out += ["DISABLED CONTAINERS (their tools do not run and are not listed):"]
        for p, _ in off:
            inside = [t.id for t in tools.values() if t.disabled and t.path[:len(p)] == p]
            out += wrap("%s: %d tools %s" % (" > ".join(p), len(inside), " ".join("#" + i for i in sorted(inside, key=_id_key))), "  ")

    if rule_text:
        out += ["", "RULES USED MORE THAN ONCE (referred to below by name):"]
        for name, text, ids in rule_text:
            out += wrap("%s = %s" % (name, text), "  ", "       ")
            out += wrap("used by %d tools: %s" % (len(ids), " ".join("#" + i for i in ids)), "       ")
    if blocks:
        out += ["", "REPEATED SUB-CHAINS (first copy is printed in the flows with its lines tagged; other copies are one line):"]
        for b, copies in enumerate(blocks, 1):
            span = sorted(copies[0], key=_id_key)
            out += wrap("B%d: %d tools × %d copies; first copy #%s … #%s; kinds: %s" % (
                b, len(copies[0]), len(copies), span[0], span[-1],
                ", ".join("%s %d" % kv for kv in Counter(tools[i].kind for i in copies[0]).most_common())), "  ")

    out += ["", "FLOWS (each tool: #id Kind <inputs : rule;  .T/.F filter outputs, .L/.J/.R join outputs, .U/.D unique)"]
    for where in sect_order:
        if sections[where]:
            out += ["", "=== %s ===" % where] + sections[where]
    return out, {"blocks": blocks, "rules": rule_text, "printed": printed, "status": tot}
