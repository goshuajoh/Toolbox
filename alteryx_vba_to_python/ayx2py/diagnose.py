"""python -m ayx2py WORKFLOW --diagnose report.txt: the workflow's structure, and what the converter made of it,
with no expressions, file names, field names, captions or notes, so it can be shared or photographed. Summary first,
details after. Tool IDs, tool kinds (plugin and macro names), anchors and Dynamic Input mode names are kept.
"""
import re
from collections import Counter, defaultdict

from .workflow import IGNORED, STATUS, _id_key

_HIDE = re.compile(r"\[[^\]]*\]|'[^']*'|\"[^\"]*\"|`[^`]*`|[^\s,;:()]+\.(?:csv|txt|xlsx|xlsm|xls|yxdb|yxmd|yxmc|vbs|bat|exe|json|xml)\b"
                   r"|[A-Za-z]:[\\/][^\s,;]*|\\\\[^\s,;]*", re.I)


def _clean(text):
    """A reason with every quoted, bracketed or file-like part replaced by "…"."""
    return re.sub(r"\s+", " ", _HIDE.sub("…", text)).strip()


def report(wf, guide=None, fatal=None):
    """-> lines. guide: the Guide object (for the exceptions it caught); fatal: an exception that stopped the run."""
    t = wf.tools
    run = [i for i in wf.ordered()]
    tops = [(n, p, off) for n, (p, off) in enumerate([c for c in wf.containers if len(c[0]) == 1], 1)]
    top_index = {p[0]: n for n, p, _ in tops}
    kinds = Counter(t[i].kind for i in t)
    stub_kinds = sorted({t[i].kind for i in run if t[i].kind not in STATUS})
    failures = list(getattr(guide, "errors", []) or [])
    bud = [i for i in run if t[i].kind in ("BlockUntilDone", "ParallelBlockUntilDone")]
    lines = ["ayx2py structure report (no rules, names, files or notes)", "",
             "SUMMARY",
             "tools %d: run %d, in disabled containers %d, notes/browse %d" % (
                 len(t), sum(1 for i in run if not t[i].disabled), sum(1 for i in t if t[i].disabled and t[i].kind not in IGNORED),
                 sum(1 for i in t if t[i].kind in IGNORED)),
             "top-level containers %d (disabled %d); all containers %d" % (len(tops), sum(1 for *_, off in tops if off),
                                                                         len(wf.containers)),
             "connections %d; to a missing tool %d" % (len(wf.edges), len(wf.missing_ends)),
             "real loops: %d connections" % len(wf.cycle_edges),
             "block until done tools %d; order preferences %d, dropped %d" % (len(bud), len(wf.order_edges),
                                                                              len(wf.dropped)),
             "kinds with no converter: %s" % (", ".join(stub_kinds) or "none"),
             "steps not converted: %d" % sum(1 for i in run if t[i].status == "stub"),
             "converter failures: %d" % len(failures),
             "stopped by: %s" % ("%s in %s" % fatal if fatal else "nothing (finished)"), ""]

    lines += ["CONTAINERS (index, state, tools, deepest nesting)"]
    count = Counter(top_index.get(t[i].path[0], 0) if t[i].path else 0 for i in t if t[i].kind not in IGNORED)
    depth = defaultdict(int)
    for p, _ in wf.containers:
        depth[top_index.get(p[0], 0)] = max(depth[top_index.get(p[0], 0)], len(p))
    for n, p, off in tops:
        lines.append("C%d %s tools %d depth %d" % (n, "disabled" if off else "enabled", count[n], depth[n]))
    if count[0]:
        lines.append("C0 (outside any container) tools %d" % count[0])

    lines += ["", "KINDS (count; status)"]
    for k, n in kinds.most_common():
        lines.append("%s %d; %s" % (k, n, "ignored" if k in IGNORED else STATUS[k][0] if k in STATUS else "no converter"))

    anchors = defaultdict(lambda: (set(), set()))
    for e in wf.edges:
        if e[0] in t:
            anchors[t[e[0]].kind][0].add(e[1])
        if e[2] in t:
            anchors[t[e[2]].kind][1].add(e[3])
    lines += ["", "ANCHORS (kind: out | in)"]
    for k in sorted(anchors):
        o, i = anchors[k]
        lines.append("%s: %s | %s" % (k, " ".join(sorted(o)) or "-", " ".join(sorted(i)) or "-"))

    dyn = Counter("%s/%s" % (t[i].ir.get("mode"), t[i].ir.get("how")) for i in run if t[i].kind == "DynamicInput")
    if dyn:
        lines += ["", "DYNAMIC INPUT MODES"] + ["%s %d" % kv for kv in dyn.most_common()]

    why = Counter((t[i].kind, _clean("; ".join(t[i].reasons()))) for i in run if t[i].status == "stub")
    if why:
        lines += ["", "NOT CONVERTED (kind: reason, quoted parts hidden)"] + ["%s %d: %s" % (k, n, r) for (k, r), n in
                                                                              why.most_common()]
    if failures:
        lines += ["", "CONVERTER FAILURES (type, where, tool, kind)"] + ["%s %s #%s %s" % f for f in failures]
    if wf.cycle_edges:
        lines += ["", "REAL LOOPS (from -> to)"] + ["#%s %s -> #%s %s" % (e[0], t[e[0]].kind, e[2], t[e[2]].kind)
                                                     for e in wf.cycle_edges]
    if wf.dropped:
        lines += ["", "DROPPED ORDER PREFERENCES (before -> after)"] + [
            "#%s %s -> #%s %s" % (b, t[b].kind, a, t[a].kind) for b, a in wf.dropped[:60]]
        if len(wf.dropped) > 60:
            lines.append("... %d more" % (len(wf.dropped) - 60))
    if wf.missing_ends:
        lines += ["", "CONNECTIONS TO MISSING TOOLS"] + ["#%s -> #%s" % (e[0], e[2]) for e in wf.missing_ends[:60]]
    macros = sorted({k for k in kinds if k.startswith("Macro:")})
    if macros:
        lines += ["", "MACROS USED"] + macros
    return lines


def ids(lines):
    """Tool IDs in a report, for tests."""
    return sorted({m for line in lines for m in re.findall(r"#(\d+)", line)}, key=_id_key)
