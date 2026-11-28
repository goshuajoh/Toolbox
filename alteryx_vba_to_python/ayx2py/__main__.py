"""python -m ayx2py WORKFLOW [OUT_DIR] [--name NAME] [--no-code] [--brief] [--tool-listing] [--and-before-or]

Reads an Alteryx workflow (.yxmd, or the same XML saved as .txt) and writes, into OUT_DIR:
    NAME_guide.md        migration guide: what each container does, one story per output (its steps in
                         run order, each with its rule and pandas), run order, files, and detail
    NAME_guide.html      the same, with folding sections; opens in any browser, no network
    NAME_logic.md/.html  the shorter stage-by-stage summary                  (only with --brief)
    NAME_generated.py    Python/pandas code, one function per tool          (not with --no-code)
    NAME_steps.ipynb     plain pandas template: settings, load every file, one cell per branch with
                         its steps as comments to paste the guide's pandas lines under
    NAME_test.ipynb      notebook that runs the generated code one container at a time on your
                         files and compares each output       (only with --runner-notebook, not with --no-code)
    ayx_runtime.py       the library the generated code calls               (not with --no-code)
    NAME_tools.txt       every tool with every setting, in flow order        (only with --tool-listing)
The workflow file is only read. Standard library only.
"""
import argparse
import os
import re
import shutil
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logicdoc  # noqa: E402

from . import codegen, expr, guide, logic, notebook, spec, workflow  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m ayx2py", description="Alteryx workflow -> logic document + Python.")
    ap.add_argument("workflow")
    ap.add_argument("out_dir", nargs="?", default=None)
    ap.add_argument("--name", default=None, help="prefix of the output files (default: the workflow's name)")
    ap.add_argument("--no-code", action="store_true", help="write only the documents")
    ap.add_argument("--brief", action="store_true", help="also write the shorter stage-by-stage summary")
    ap.add_argument("--runner-notebook", action="store_true",
                    help="also write NAME_test.ipynb, which runs the generated code container by container")
    ap.add_argument("--tool-listing", action="store_true", help="also write every tool's settings, one tool at a time")
    ap.add_argument("--width", type=int, default=150, help="line width of the tool listing")
    ap.add_argument("--enabled-only", action="store_true",
                    help="leave out containers disabled in the saved file (by default they are documented and converted)")
    ap.add_argument("--diagnose", metavar="REPORT.txt", default=None,
                    help="also write a structure-only report (no rules, names, files or notes) that can be shared")
    ap.add_argument("--and-before-or", action="store_true",
                    help="parse AND before OR instead of Alteryx's documented left-to-right rule")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")        # a Windows console may not have every character
    expr.AND_BINDS_TIGHTER = a.and_before_or
    try:
        wf = workflow.Workflow(a.workflow, enabled_only=a.enabled_only)
    except (workflow.WorkflowError, OSError) as e:
        print("ERROR: %s" % e)
        return 2
    base = a.name or re.sub(r"\W+", "_", os.path.splitext(os.path.basename(a.workflow))[0])
    out_dir = a.out_dir or os.path.join(os.path.dirname(os.path.abspath(a.workflow)), "ayx2py_out")
    if os.path.abspath(out_dir) == os.path.dirname(os.path.abspath(a.workflow)):
        print("ERROR: choose an output folder other than the workflow's own folder")
        return 2
    os.makedirs(out_dir, exist_ok=True)
    try:
        return _write(a, wf, base, out_dir)
    except Exception as exc:
        if a.diagnose:
            _diagnose(a.diagnose, wf, _LAST.get("guide"), "%s in %s" % (type(exc).__name__, guide.where_raised(exc)))
        raise


_LAST = {}


def _diagnose(path, wf, g, fatal=None):
    from . import diagnose
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(diagnose.report(wf, g, fatal)) + "\n")
    print("wrote %s (structure only: safe to share)" % path)


def _write(a, wf, base, out_dir):
    g = guide.Guide(wf)
    _LAST["guide"] = g
    doc = g.build()
    workflow_dir = os.path.dirname(os.path.abspath(a.workflow))
    files = [(base + "_guide.md", logicdoc.to_markdown(doc), "utf-8"),
             (base + "_guide.html", logicdoc.to_html(doc, "Migration guide: " + os.path.basename(a.workflow)), "utf-8")]
    files.append((base + "_steps.ipynb", notebook.build_steps(wf, workflow_dir, g), "utf-8"))
    if a.brief:
        brief = logic.build(wf)
        files += [(base + "_logic.md", logicdoc.to_markdown(brief), "utf-8"),
                  (base + "_logic.html", logicdoc.to_html(brief, "Logic of " + os.path.basename(a.workflow)), "utf-8")]
    if not a.no_code:
        code, _ = codegen.generate(wf, "Generated by ayx2py from %s (sha256 %s)." % (os.path.basename(a.workflow), wf.sha256))
        files.append((base + "_generated.py", "\n".join(code) + "\n", "utf-8"))
        if a.runner_notebook:
            files.append((base + "_test.ipynb", notebook.build_runner(wf, base + "_generated", workflow_dir), "utf-8"))
        shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ayx_runtime.py"),
                        os.path.join(out_dir, "ayx_runtime.py"))
    if a.tool_listing:
        lines, _ = spec.build(wf, a.width)
        files.append((base + "_tools.txt", "\n".join(lines) + "\n", "utf-8-sig"))
    for name, text, enc in files:
        with open(os.path.join(out_dir, name), "w", encoding=enc, newline="\n") as fh:
            fh.write(text)
        print("wrote %s: %d lines, %d characters" % (os.path.join(out_dir, name), text.count("\n"), len(text)))
    ext = [i for i in wf.ordered() if wf.tools[i].kind == "RunCommand"]
    if ext:
        print("%d external program%s to replace (Run Command; the generated code only logs them): %s" % (
            len(ext), "" if len(ext) == 1 else "s", ", ".join("#%s" % i for i in ext)))
    if g.errors:
        print("the converter failed on %d step%s (listed in the guide's last section)" % (
            len({e[2] for e in g.errors}), "" if len({e[2] for e in g.errors}) == 1 else "s"))
    if a.diagnose:
        _diagnose(a.diagnose, wf, g)
    deep = codegen.deep_tools(wf)
    if deep:
        print("%d tool%s not converted because a rule nests too deeply (see the guide's last section): %s" % (
            len(deep), "" if len(deep) == 1 else "s", ", ".join("#%s (%s)" % (i, re.sub(r".*\((\d+ levels)\).*", r"\1", p))
                                                         for i, p in deep)))
    return 0


STACK = 256 * 1024 * 1024       # a rule nested hundreds of levels deep needs more than a thread's usual 1-8 MB


def run(argv=None):
    """main() on a thread with a large stack and a high recursion limit, so a deeply nested rule is read (or, past
    that, reported for its tool) instead of stopping the whole run."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), expr.DEEP_LIMIT))
    result = {}

    def target():
        try:
            result["rc"] = main(argv)
        except BaseException as e:  # noqa: BLE001 - raised again on the calling thread
            result["error"] = e

    try:
        threading.stack_size(STACK)
    except (ValueError, RuntimeError):
        pass
    worker = threading.Thread(target=target, name="ayx2py")
    worker.start()
    worker.join()
    threading.stack_size(0)
    if "error" in result:
        raise result["error"]
    return result["rc"]


if __name__ == "__main__":
    sys.exit(run())
