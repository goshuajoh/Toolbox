"""python -m vba2py SOURCE [SOURCE ...] [-o OUT_DIR] [--name NAME] [--no-code]

SOURCE is VBA text (.bas / .cls / .txt, one or several modules) or a workbook with macros
(.xlsm / .xlam / .xls / vbaProject.bin), which is read without Excel. Writes into OUT_DIR:
    NAME_logic.md          what each Sub reads, opens and reports, then its steps (plain text)
    NAME_logic.html        the same as a page for a browser (no scripts, no network)
    NAME_generated.py      Python on openpyxl, one function per Sub      (not with --no-code)
    xl_runtime.py          the library the generated code calls          (not with --no-code)
    NAME_vba_source.txt    the VBA text, when it was read out of a workbook
The sources are only read. Standard library only.
"""
import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logicdoc  # noqa: E402

from . import analyse, ovba, transpile, vbaparse  # noqa: E402


def read_sources(paths):
    """-> (parsed modules, extracted text or '')"""
    modules, extracted = [], []
    for path in paths:
        if os.path.splitext(path)[1].lower() in (".xlsm", ".xlam", ".xlsb", ".xls", ".docm", ".doc", ".pptm", ".bin"):
            for name, text in ovba.read_vba(path):
                extracted.append("'===== module %s =====\n%s" % (name, text))
                modules.append(vbaparse.parse_module(text, name))
        else:
            with open(path, "rb") as fh:
                raw = fh.read()
            text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "replace") \
                if b"\x00" not in raw[:64] else raw.decode("utf-16-le", "replace")
            base = os.path.splitext(os.path.basename(path))[0]
            for name, part in vbaparse.split_modules(text, base):
                modules.append(vbaparse.parse_module(part, name))
    return [m for m in modules if m["procs"] or m["decls"]], "\n\n".join(extracted)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m vba2py", description="VBA -> specification + Python on openpyxl.")
    ap.add_argument("sources", nargs="+")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--no-code", action="store_true", help="write only the logic document")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")        # a Windows console may not have every character
    try:
        modules, extracted = read_sources(a.sources)
    except (ovba.OVBAError, OSError) as e:
        print("ERROR: %s" % e)
        return 2
    if not modules:
        print("ERROR: no VBA procedures found")
        return 2
    base = a.name or re.sub(r"\W+", "_", os.path.splitext(os.path.basename(a.sources[0]))[0])
    out_dir = a.out or os.path.join(os.path.dirname(os.path.abspath(a.sources[0])), "vba2py_out")
    os.makedirs(out_dir, exist_ok=True)
    label = ", ".join(os.path.basename(s) for s in a.sources)
    code, report = transpile.generate(modules, label)
    doc = analyse.build(modules, report, label)
    files = [(base + "_logic.md", logicdoc.to_markdown(doc), "utf-8"),
             (base + "_logic.html", logicdoc.to_html(doc, "VBA logic of " + label), "utf-8")]
    if not a.no_code:
        files.append((base + "_generated.py", "\n".join(code) + "\n", "utf-8"))
        shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "xl_runtime.py"),
                        os.path.join(out_dir, "xl_runtime.py"))
    if extracted:
        files.append((base + "_vba_source.txt", extracted + "\n", "utf-8-sig"))
    for name, text, enc in files:
        with open(os.path.join(out_dir, name), "w", encoding=enc, newline="\n") as fh:
            fh.write(text)
        print("wrote %s: %d lines, %d characters" % (os.path.join(out_dir, name), text.count("\n"), len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
