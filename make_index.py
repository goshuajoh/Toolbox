"""Regenerate everything that is derived from the docstrings: the function index in README.md and the GitHub
Pages site in docs/ (index.html + toolbox.json).

    python make_index.py            # rewrite README.md index and docs/
    python make_index.py --check    # exit 1 if README.md or docs/ are out of date (CI)
"""
import ast
import importlib
import inspect
import json
import re
import sys
import textwrap
from pathlib import Path

import toolbank

ROOT = Path(__file__).parent
REPO = "https://github.com/goshuajoh/Toolbox"
TITLES = {
    "robust": "Robust robots (errors, retry, checkpoint, dry run)", "clean": "Clean & normalise (Data Cleansing / Auto Field)",
    "readers": "Read (Input / Directory)", "parse": "Parse JSON / XML / text / SWIFT / camt.053 / FIX / amounts / tenors",
    "transform": "Sort / Filter / Formula / Multi-Row / Text To Columns / RegEx / Unique / Sample",
    "combine": "Combine (Join / Lookup / Union / Fuzzy Match / Reconcile / Diff)",
    "reshape": "Reshape (Summarize / Cross Tab / Transpose)", "formula": "Excel formulas with Excel's behaviour",
    "rules": "Rules, conditions & flags (rules kept in Excel)", "quality": "Data quality & audit",
    "identifiers": "Identifiers (ISIN / CUSIP / SEDOL / LEI / IBAN / BIC / currency)", "regexkit": "Regex toolkit",
    "excel_write": "Write Excel / CSV (openpyxl)", "excel_app": "Real Excel via COM (Windows) — macros, pivots, VBA",
    "dates": "Dates, business days & cut-offs", "files": "Files", "logs": "Logging",
    "privacy": "Privacy / AI-safe data", "approval": "Maker-checker", "docs": "Word & PDF", "ocr": "OCR",
    "config": "Config & secrets", "notify": "Notify (Teams)", "api": "APIs, webhooks, DevTools cURL / HAR",
    "htmlparse": "HTML without a browser", "web": "Browser (Playwright): actions, JavaScript, network",
    "webdriver": "Browser (Selenium WebDriver)", "desktop": "Desktop (pywinauto / pyautogui)",
    "outlook": "Outlook (COM)", "mail": "Email files (.eml / .msg) & SMTP", "db": "SQL databases (SQL Server / Fabric / SQLite)",
    "lake": "Fabric OneLake & Delta tables", "triggers": "Triggers & scheduling (folder, timer, cron, webhook, queue)",
    "flow": "Workflows (Alteryx-style)", "perf": "Speed & size",
    "recipe": "Recipes (spec -> validated workflow -> generated script)",
}
# import name -> pip package, for the "needs" hint on the site (libraries imported lazily inside a function)
PIP = {"docx": "python-docx", "bs4": "beautifulsoup4", "PIL": "Pillow", "win32com": "pywin32", "pythoncom": "pywin32",
       "cv2": "opencv-python", "requests_negotiate_sspi": "requests-negotiate-sspi", "extract_msg": "extract-msg",
       "azure": "azure-identity", "urllib3": "requests", "msvcrt": None, "fcntl": None}
CORE = {"pandas", "numpy", "openpyxl", "yaml", "toolbank"}
EXIT_CODES = [(0, "OK"), (2, "business exceptions or bad input (send the report to the owner; if the input was "
                  "rejected before the report exists, run.log.jsonl says why)"),
              (3, "system failure (re-run: the checkpoint resumes)"),
              (4, "config error"), (5, "robot error (needs a fix)"), (6, "already running (a second copy was started)")]


def first_line(obj):
    doc = inspect.getdoc(obj) or ""
    text = re.sub(r"\s+", " ", doc.split("\n\n")[0]).strip().replace("|", "\\|")
    parts = re.split(r"(`[^`]*`)", text)                 # leave `code` alone, escape markdown/HTML elsewhere
    return "".join(p if p.startswith("`") else p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                   .replace("*", "\\*").replace("__", "\\_\\_") for p in parts)


def _summary(obj):
    """First paragraph of the docstring as plain text (for the site)."""
    return re.sub(r"\s+", " ", (inspect.getdoc(obj) or "").split("\n\n")[0]).strip()


def _signature(obj, short=True, method=False):
    if method:
        params = list(inspect.signature(obj).parameters.values())[1:]       # drop self
        return re.sub(r"\s+", " ", str(inspect.Signature(params)))
    if inspect.isclass(obj):
        if short:
            return "(…)"
        try:
            params = list(inspect.signature(obj.__init__).parameters.values())[1:]
            sig = str(inspect.Signature(params))
        except (TypeError, ValueError):
            sig = "()"
    else:
        sig = str(inspect.signature(obj))
    sig = re.sub(r"\s+", " ", sig)
    sig = re.sub(r"<class '(?:[\w.]+\.)?(\w+)'>", r"\1", sig)
    sig = re.sub(r"<(?:built-in )?function (\w+)[^>]*>", r"\1", sig)
    sig = re.sub(r"<(?:built-in )?method (\w+) of [^>]*>", r"\1", sig)
    return sig


def _needs(obj):
    """pip packages a function (or a class's methods) imports lazily, i.e. the optional libraries it needs."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    except (OSError, TypeError, SyntaxError):
        return []
    found = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        for n in names:
            top = n.split(".")[0]
            if top in CORE or top in sys.stdlib_module_names:
                continue
            pkg = PIP.get(top, top)
            if pkg and pkg not in found:
                found.append(pkg)
    return found


def _default_text(v):
    """A default value as Python text the builder can paste back: classes and functions by name, no addresses."""
    if inspect.isclass(v):
        return v.__name__
    if inspect.isfunction(v) or inspect.isbuiltin(v) or inspect.ismethod(v):
        return getattr(v, "__name__", "…")
    r = repr(v)
    r = re.sub(r"<class '(?:[\w.]+\.)?(\w+)'>", r"\1", r)
    r = re.sub(r"<(?:built-in )?(?:function|method) (\w+)[^>]*>", r"\1", r)
    return re.sub(r" at 0x[0-9a-fA-F]+", "", r)


def _params(obj):
    """Parameter list for the script builder: name, kind, default (as Python text) or None when required."""
    target = obj.__init__ if inspect.isclass(obj) else obj
    try:
        params = list(inspect.signature(target).parameters.values())
    except (TypeError, ValueError):
        return []
    if inspect.isclass(obj):
        params = params[1:]
    out = []
    for p in params:
        kind = {p.VAR_POSITIONAL: "varargs", p.VAR_KEYWORD: "varkw", p.KEYWORD_ONLY: "kwonly"}.get(p.kind, "pos")
        out.append({"name": p.name, "kind": kind, "default": None if p.default is p.empty else _default_text(p.default)})
    return out


def _returns_tuple(obj):
    """True when the docstring says the function returns a tuple ('Returns (a, b)', '-> (a, b)', '(a, b)...'), so
    the script builder adds [0] / pick: 0."""
    doc = inspect.getdoc(obj) or ""
    return not inspect.isclass(obj) and bool(re.search(r"(?:Returns|->)\s*\(|^\(", doc))


def _public(mod):
    return [(n, o) for n, o in vars(mod).items() if not n.startswith("_") and getattr(o, "__module__", "") == mod.__name__
            and (inspect.isfunction(o) or inspect.isclass(o))]


def _line(obj):
    try:
        return inspect.getsourcelines(obj)[1]
    except (OSError, TypeError):
        return None


def catalog():
    """Everything the README index and the site are built from, as plain data (one dict per module)."""
    mods = []
    for m in toolbank._MODULES:
        mod = importlib.import_module(f"toolbank.{m}")
        funcs = []
        for n, o in _public(mod):
            item = {"name": n, "kind": "class" if inspect.isclass(o) else "function", "signature": _signature(o, short=False),
                    "summary": _summary(o), "doc": inspect.getdoc(o) or "", "line": _line(o), "needs": _needs(o),
                    "params": _params(o), "methods": [], "tuple": _returns_tuple(o)}
            if inspect.isclass(o):
                for mn, mo in vars(o).items():
                    if inspect.isfunction(mo) and not mn.startswith("_") and _summary(mo):
                        item["methods"].append({"name": mn, "signature": _signature(mo, method=True), "summary": _summary(mo),
                                                "doc": inspect.getdoc(mo) or "", "line": _line(mo)})
            funcs.append(item)
        mods.append({"name": m, "title": TITLES.get(m, m), "file": f"toolbank/{m}.py",
                     "doc": _summary(mod), "functions": funcs})
    return mods


def vba_modules():
    out = []
    for f in sorted((ROOT / "vba").glob("*.bas")):
        text = f.read_text(encoding="utf-8", errors="replace")
        desc = next((ln.lstrip("' ").strip() for ln in text.splitlines() if ln.startswith("'")), "")
        out.append({"file": f.name, "summary": desc})
    return out


def needs_table(readme_text):
    """The 'I need to… | module' rows of README.md (one source of truth for the site's finder)."""
    rows = []
    section = readme_text.split("## Which module?", 1)[-1].split("\n\n| folder", 1)[0]
    for line in section.splitlines():
        m = re.match(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|$", line)
        if m and not m.group(1).startswith(("I need", "---")):
            found = re.findall(r"`([\w/.]+)`", m.group(2))
            rows.append({"need": m.group(1), "modules": [x for x in found if x in toolbank._MODULES]})
    return rows


def build(mods=None):
    """Markdown for the README index."""
    out = []
    for mod in mods or catalog():
        out.append(f"\n### {mod['title']} — `{mod['file']}`\n\n| function | what it does |\n|---|---|")
        for fn in mod["functions"]:
            obj = getattr(importlib.import_module(f"toolbank.{mod['name']}"), fn["name"])
            sig = _signature(obj, short=True)
            if len(sig) > 70:
                sig = sig[:67] + "…)"
            sig = sig.replace("|", "\\|")
            out.append(f"| `{fn['name']}{sig}` | {first_line(obj)} |")
            for me in fn["methods"]:
                out.append(f"| `  .{me['name']}()` | {first_line(getattr(obj, me['name']))} |")
    out.append("\n### VBA — `vba/*.bas`\n\n| module | what it does |\n|---|---|")
    for v in vba_modules():
        out.append(f"| `{v['file']}` | {v['summary']} |")
    return "\n".join(out) + "\n"


def site_data(mods, readme_text):
    n_fn = sum(len(m["functions"]) for m in mods)
    n_me = sum(len(f["methods"]) for m in mods for f in m["functions"])
    return {"version": toolbank.__version__, "repo": REPO, "modules": mods, "vba": vba_modules(),
            "needs": needs_table(readme_text), "exit_codes": [{"code": c, "meaning": m} for c, m in EXIT_CODES],
            "counts": {"modules": len(mods), "functions": n_fn, "methods": n_me}}


def render(readme_text=None):
    """(readme_text, index_html, toolbox_json) with the index regenerated."""
    readme_text = readme_text if readme_text is not None else (ROOT / "README.md").read_text(encoding="utf-8")
    mods = catalog()
    start, end = "<!-- INDEX:START -->", "<!-- INDEX:END -->"
    readme = readme_text.split(start)[0] + start + "\n" + build(mods) + end + readme_text.split(end)[1]
    data = site_data(mods, readme)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    template = (ROOT / "site" / "index.template.html").read_text(encoding="utf-8")
    html = (template.replace("__TOOLBOX_DATA__", payload.replace("</", "<\\/")).replace("__VERSION__", data["version"])
            .replace("__REPO__", REPO))
    return readme, html, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


OUTPUTS = ("README.md", "docs/index.html", "docs/toolbox.json")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    outputs = dict(zip(OUTPUTS, render()))
    if "--check" in argv:
        stale = [n for n, text in outputs.items()
                 if not (ROOT / n).exists() or (ROOT / n).read_text(encoding="utf-8") != text]
        if stale:
            print(f"out of date: {stale} -> run: python make_index.py")
            return 1
        print("README index and docs/ are up to date")
        return 0
    for n, text in outputs.items():
        (ROOT / n).parent.mkdir(parents=True, exist_ok=True)
        (ROOT / n).write_text(text, encoding="utf-8")
    (ROOT / "docs" / ".nojekyll").touch()
    print("README index and docs/ updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
