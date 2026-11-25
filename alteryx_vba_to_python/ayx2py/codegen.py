"""Generate a Python/pandas module from a workflow.

One function per distinct tool (tools with identical settings share one function; its comment
lists every tool ID that uses it), then run(), which calls them in dependency order.
A tool that cannot be converted becomes a function that raises rt.NotConverted: nothing is
ever passed through silently.
"""
import textwrap

from . import expr
from .spec import describe, signature
from .workflow import _anchor_no

MULTI_OUT = {"Filter": ("True", "False"), "Join": ("Left", "Join", "Right"), "Unique": ("Unique", "Duplicates")}
TWO_IN = {"Join": ("l", "r"), "FindReplace": ("t", "s"), "AppendFields": ("t", "s")}
SOURCES = ("Input", "Directory", "TextInput")


def _comment(text, indent="    "):
    return textwrap.wrap(text, 116, initial_indent=indent + "# ", subsequent_indent=indent + "#   ",
                         break_on_hyphens=False, break_long_words=False) or [indent + "#"]


def _fn(e, name, args="r"):
    return _comment(e["text"], "") + expr.to_function(e["ast"], name, args)


def _call(fn, *args, **kw):
    parts = list(args) + ["%s=%s" % (k, v) for k, v in kw.items()]
    one = "    return rt.%s(%s)" % (fn, ", ".join(parts))
    if len(one) <= 118:
        return [one]
    return ["    return rt.%s(" % fn] + ["        %s," % p for p in parts] + ["    )"]


def tool_code(t, name):
    """-> (helper lines, def lines) for one converted tool."""
    k, ir, pre = t.kind, t.ir, []
    x = "x" + name[1:]
    if k == "Input":
        sig, body = "tool_id", _call("read_file", repr(ir["file"]), repr(ir["options"]), repr(ir["types"]), tool_id="tool_id")
    elif k == "Directory":
        sig, body = "tool_id", _call("directory", repr(ir["dir"]), repr(ir["spec"]), tool_id="tool_id")
    elif k == "TextInput":
        sig, body = "tool_id", _call("text_input", repr(ir["columns"]), repr(ir["rows"]), repr(ir["types"]), tool_id="tool_id")
    elif k == "DynamicInput":
        sig, body = "df, tool_id", _call("dynamic_input", "df", repr(ir["file"]), repr(ir["options"]), repr(ir["field"]),
                                         repr(ir["how"]), repr(ir["types"]), tool_id="tool_id")
    elif k == "Output":
        sig, body = "df, tool_id", _call("write_output", "df", repr(ir["file"]), repr(ir["options"]), tool_id="tool_id")
    elif k == "Select":
        sig, body = "df", _call("select", "df", repr(ir["fields"]), unknown=ir["unknown"])
    elif k == "Formula":
        items = []
        for n, (field, typ, size, e) in enumerate(ir["fields"], 1):
            fname = "%s_%d" % (x, n)
            pre += _comment("[%s] =" % field, "") + _fn(e, fname) + [""]
            items.append("(%r, %r, %r, %s)" % (field, typ, size, fname))
        sig, body = "df", _call("formula", "df", "[%s]" % ", ".join(items))
    elif k == "Filter":
        if ir["mode"] == "simple":
            sig, body = "df", _call("filter_", "df", "rt.simple_filter(%r, %r, %r, df)" % (ir["field"], ir["operator"], ir["operand"]))
        else:
            pre += _fn(ir["expr"], x) + [""]
            sig, body = "df", _call("filter_", "df", x)
    elif k == "Join":
        sig, body = "left, right", _call("join", "left", "right", repr(ir["left"]), repr(ir["right"]),
                                         repr(ir["fields"]), unknown=ir["unknown"])
    elif k == "AppendFields":
        sig, body = "targets, source", _call("append_fields", "targets", "source", repr(ir["fields"]), unknown=ir["unknown"])
    elif k == "Union":
        sig, body = "*frames", _call("union", "*frames", mode=repr(ir["mode"]))
    elif k == "Summarize":
        sig, body = "df", _call("summarize", "df", repr(ir["fields"]))
    elif k == "Sort":
        sig, body = "df", _call("sort", "df", repr(ir["fields"]))
    elif k == "Unique":
        sig, body = "df", _call("unique", "df", repr(ir["fields"]))
    elif k == "Sample":
        sig, body = "df", _call("sample", "df", repr(ir["mode"]), repr(ir["n"]), repr(ir["group"]))
    elif k == "RecordID":
        sig, body = "df", _call("record_id", "df", repr(ir["name"]), repr(ir["start"]), repr(ir["type"]),
                                repr(ir["size"]), last=ir["last"])
    elif k == "Transpose":
        sig, body = "df", _call("transpose", "df", repr(ir["keys"]), repr(ir["data"]), unknown=ir["unknown"])
    elif k == "MultiFieldFormula":
        pre += _fn(ir["expr"], x, "r, cur, cur_name, cur_type") + [""]
        sig, body = "df", _call("multi_field_formula", "df", repr(ir["fields"]), x, group=repr(ir["group"]),
                                unknown=ir["unknown"], copy=ir["copy"], addon=repr(ir["addon"]), prefix=ir["prefix"],
                                out_type=repr(ir["out_type"]), out_size=repr(ir["out_size"]))
    elif k == "FindReplace":
        sig, body = "targets, source", _call(
            "find_replace", "targets", "source", repr(ir["find"]), repr(ir["search"]), mode=repr(ir["mode"]),
            nocase=ir["nocase"], whole_word=ir["whole"], append=repr(ir["append"] if ir["replace_mode"] == "Append" else []),
            replace_field=repr(ir["replace"] or None), multiple=ir["multiple"])
    elif k == "DateTime":
        sig, body = "df", _call("datetime_tool", "df", repr(ir["field"]), repr(ir["format"]), repr(ir["out"]),
                                to_string_=ir["to_string"])
    elif k == "Cleanse":
        sig, body = "df", _call("cleanse", "df", repr(ir["fields"]), **{o: repr(v) for o, v in ir["options"].items()})
    elif k == "Message":
        pre += _fn(ir["text"], x + "_text") + [""]
        flt = "None"
        if ir["filter"]:
            pre += _fn(ir["filter"], x + "_when") + [""]
            flt = x + "_when"
        sig, body = "df, tool_id", _call("message", "df", "tool_id", repr(ir["when"]), repr(ir["type"]), x + "_text", flt)
    else:
        raise KeyError(k)
    return pre, ["def %s(%s):" % (name, sig)] + body


def check_compiles(wf):
    """Generate and compile each converted tool's function once. A tool whose rule is nested too deeply for Python
    (or for this converter) becomes not converted, with the reason, and the rest of the workflow carries on."""
    if getattr(wf, "compile_checked", False):
        return
    wf.compile_checked = True
    for i in wf.ordered():
        t = wf.tools[i]
        if t.status == "stub" or t.kind in ("ParallelBlockUntilDone", "BlockUntilDone"):
            continue
        try:
            pre, body = tool_code(t, "t_check")
            compile("\n".join(["import ayx_runtime as rt"] + pre + body), "<tool %s>" % i, "exec")
        except (RecursionError, MemoryError, SyntaxError, ValueError) as e:
            texts = [f[3]["text"] for f in t.ir.get("fields", []) if isinstance(f, tuple) and len(f) == 4 and
                     isinstance(f[3], dict)] + [x["text"] for x in (t.ir.get("expr"), t.ir.get("filter")) if isinstance(x, dict)]
            levels = max([expr.text_nesting(x) for x in texts] or [0])
            t.problems.append("expression nested too deeply (%d levels)" % levels if levels > 100 else
                              "the generated Python does not compile: %s" % type(e).__name__)


def deep_tools(wf):
    """Tools left unconverted because a rule nests too deeply: [(tool id, reason)]."""
    return [(i, p) for i in wf.ordered() for p in wf.tools[i].problems if "nested too deeply" in p]


def function_names(wf):
    """-> ({tool id: name of its function in the generated module}, {signature: [tool ids]}).
    Tools with identical settings share one function, named after the first of them."""
    check_compiles(wf)
    by_sig, name_of = {}, {}
    for i in wf.ordered():
        s = signature(wf.tools[i]) + ("|" + "|".join(wf.tools[i].problems) if wf.tools[i].problems else "")
        by_sig.setdefault(s, []).append(i)
        name_of[i] = "t" + "".join(c if c.isalnum() else "_" for c in by_sig[s][0])
    return name_of, by_sig


def generate(wf, module_doc=""):
    tools, ordered = wf.tools, wf.ordered()
    name_of, by_sig = function_names(wf)

    out = ['"""%s' % (module_doc or "Generated by ayx2py."), "",
           "Generated code: change the workflow and generate again rather than editing this file.",
           "    import <this module>, ayx_runtime as rt",
           "    ctx = rt.Context(workflow_dir=r'...', inputs={tool_id: DataFrame or path}, write=False)",
           "    streams = <this module>.run(ctx)      # streams[tool_id] = that tool's output; ctx.outputs = what Output tools got",
           "    s = <this module>.run(ctx, only=2, s=s, keep_going=True, report=True)   # one container at a time",
           '"""', "import ayx_runtime as rt", "", ""]
    counts = {"functions": 0, "stubs": 0}
    for s, ids in by_sig.items():
        t = tools[ids[0]]
        if t.kind in ("ParallelBlockUntilDone", "BlockUntilDone"):
            continue
        head = "#%s %s   [%s]" % (ids[0], t.kind, t.where)
        if len(ids) > 1:
            head += "   also: " + " ".join("#" + i for i in ids[1:])
        out += _comment(head, "")
        name = name_of[ids[0]]
        if t.status == "stub":
            counts["stubs"] += 1
            why = "%s is not converted: %s" % (t.kind, "; ".join(t.reasons()))
            for line in describe(t):
                out += _comment("setting: " + line, "")
            out += ["def %s(tool_id, *inputs):" % name, "    return rt.stub(tool_id, %r, *inputs)" % why, "", ""]
            continue
        for r in t.reasons():
            out += _comment("caveat: " + r, "")
        pre, body = tool_code(t, name)
        counts["functions"] += 1
        out += pre + body + ["", ""]

    from .guide import Guide, designer                              # numbering as the guide shows it
    g = Guide(wf)
    containers = {g.cnum[k]: g.cname(k) for k in g.tops if any(g.top_of[i] == k for i in g.order)}
    links = file_links(wf, g)
    out += ["# The containers, numbered as in the migration guide.",
            "CONTAINERS = {%s}" % ", ".join("%d: %r" % kv for kv in containers.items()), "",
            "# An Input that reads a file an earlier Output writes: {input tool: output tool}. Used by run(chain=True).",
            "FILE_LINKS = {%s}" % ", ".join("%r: %r" % kv for kv in links.items()), "",
            "# Every tool in run order: (tool id, container, step, tool, the tools it reads from, what it runs).",
            "STEPS = ["]
    for i in ordered:
        t = tools[i]
        ins = wf.ins.get(i, [])
        deps = []

        def src(e):
            deps.append(e[0])
            kind = tools[e[0]].kind
            if kind in MULTI_OUT or kind in ("ParallelBlockUntilDone", "BlockUntilDone"):
                return "s[%r][%r]" % (e[0], e[1])
            return "s[%r]" % e[0]

        if t.kind in ("ParallelBlockUntilDone", "BlockUntilDone"):
            in_anchors = sorted({e[3] for e in ins}, key=_anchor_no)
            out_anchors = sorted({e[1] for e in wf.outs.get(i, ())}, key=_anchor_no)
            pairs = {}
            for n, oa in enumerate(out_anchors):
                same_no = [a for a in in_anchors if _anchor_no(a)[0] == _anchor_no(oa)[0]]
                ia = same_no[0] if len(same_no) == 1 else (in_anchors[n] if len(in_anchors) == len(out_anchors) else
                                                           in_anchors[0] if len(in_anchors) == 1 else None)
                pairs[oa] = next((src(e) for e in ins if e[3] == ia), None)
            if None in pairs.values():
                call, deps = "rt.stub(%r, 'Block Until Done: cannot tell which input feeds which output')" % i, []
            else:
                call = "{%s}" % ", ".join("%r: %s" % kv for kv in pairs.items())
        elif t.status == "stub":
            call = "%s(%r%s)" % (name_of[i], i, "".join(", " + src(e) for e in ins))
        else:
            args, missing = [], ""
            if t.kind in TWO_IN:
                for letter in TWO_IN[t.kind]:
                    es = [e for e in ins if e[3][:1].lower() == letter]
                    if len(es) != 1:
                        missing = "input %r is not connected (or its source is in a disabled container)" % letter
                    args += [src(e) for e in es]
            elif t.kind in SOURCES:
                args = []
            elif t.kind == "Union":
                es = list(ins)
                if t.ir["order"]:
                    pos = {n: k for k, n in enumerate(t.ir["order"])}
                    es.sort(key=lambda e: pos.get(e[4], len(pos)))
                args = [src(e) for e in es]
                if not args:
                    missing = "no input is connected"
            else:
                if len(ins) != 1:
                    missing = "expects one input, has %d (a source may be in a disabled container)" % len(ins)
                args = [src(e) for e in ins]
            if t.kind in SOURCES + ("DynamicInput", "Output", "Message"):
                args.append(repr(i))
            if missing:
                call, deps = "rt.stub(%r, %r)" % (i, t.kind + ": " + missing), []
            else:
                call = "%s(%s)" % (name_of[i], ", ".join(args))
        out.append("    (%r, %d, %r, %r, %r, lambda s: %s)," % (
            i, g.cnum[g.top_of[i]], g.sid.get(i, "?"), designer(t.kind),
            tuple(dict.fromkeys(deps)), call))
    out += ["]", "", "",
            "def run(ctx=None, only=None, s=None, keep_going=False, report=False, chain=False):",
            '    """Run the tools in dependency order. Returns {tool id: output}; ctx.outputs holds what Output tools got.',
            "    only: a container's number or name: run its steps, plus any earlier step they need that s does not hold.",
            "    s: the dictionary an earlier run returned, to go on from it.",
            "    keep_going: a failing step goes to ctx.errors; only the steps that need it are skipped.",
            "    report: print one line per step (rows and columns out, or the error).",
            '    chain: an Input that reads a file an earlier Output wrote in this run gets that Output\'s rows."""',
            "    return rt.run_steps(STEPS, ctx, only, s, keep_going, report, FILE_LINKS if chain else None, CONTAINERS)",
            "", ""]
    out += ['if __name__ == "__main__":', "    import sys",
            "    _ctx = rt.Context(workflow_dir=sys.argv[1] if len(sys.argv) > 1 else '.')",
            "    run(_ctx)", "    print('\\n'.join(_ctx.log))", ""]
    return out, counts


def file_links(wf, g=None):
    """{Input tool: Output tool} when an Input reads the file (and sheet) an earlier Output writes."""
    from .guide import Guide, place, _same_file
    g = g or Guide(wf)
    outs = [i for i in g.order if wf.tools[i].kind == "Output"]
    links = {}
    for i in g.order:
        t = wf.tools[i]
        if t.kind != "Input":
            continue
        path, sheet, _, _ = place(t.ir["file"])
        for o in outs:
            opath, osheet, _, _ = place(wf.tools[o].ir["file"])
            if wf.rank[o] < wf.rank[i] and _same_file(path, opath) and (sheet or "").lower() in ("", (osheet or "").lower()):
                links[i] = o
    return links
