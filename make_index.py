"""Regenerate the function index in README.md from docstrings: python make_index.py"""
import importlib
import inspect
import re
from pathlib import Path

import toolbank

ROOT = Path(__file__).parent
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
}


def first_line(obj):
    doc = inspect.getdoc(obj) or ""
    text = re.sub(r"\s+", " ", doc.split("\n\n")[0]).strip().replace("|", "\\|")
    parts = re.split(r"(`[^`]*`)", text)                 # leave `code` alone, escape markdown/HTML elsewhere
    return "".join(p if p.startswith("`") else p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                   .replace("*", "\\*").replace("__", "\\_\\_") for p in parts)


def build():
    out = []
    for m in toolbank._MODULES:
        mod = importlib.import_module(f"toolbank.{m}")
        names = [n for n, o in vars(mod).items() if not n.startswith("_") and getattr(o, "__module__", "") == mod.__name__
                 and (inspect.isfunction(o) or inspect.isclass(o))]
        out.append(f"\n### {TITLES.get(m, m)} — `toolbank/{m}.py`\n\n| function | what it does |\n|---|---|")
        for n in names:
            o = getattr(mod, n)
            sig = "(…)" if inspect.isclass(o) else re.sub(r"\s+", " ", str(inspect.signature(o)))
            sig = re.sub(r"<class '(?:[\w.]+\.)?(\w+)'>", r"\1", sig)
            sig = re.sub(r"<(?:built-in )?function (\w+)[^>]*>", r"\1", sig)
            if len(sig) > 70:
                sig = sig[:67] + "…)"
            sig = sig.replace("|", "\\|")
            out.append(f"| `{n}{sig}` | {first_line(o)} |")
            if inspect.isclass(o):
                for mn, mo in vars(o).items():
                    if inspect.isfunction(mo) and not mn.startswith("_") and first_line(mo):
                        out.append(f"| `  .{mn}()` | {first_line(mo)} |")
    out.append("\n### VBA — `vba/*.bas`\n\n| module | what it does |\n|---|---|")
    for f in sorted((ROOT / "vba").glob("*.bas")):
        desc = next((ln.lstrip("' ").strip() for ln in f.read_text().splitlines() if ln.startswith("'")), "")
        out.append(f"| `{f.name}` | {desc} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start, end = "<!-- INDEX:START -->", "<!-- INDEX:END -->"
    new = readme.split(start)[0] + start + "\n" + build() + end + readme.split(end)[1]
    (ROOT / "README.md").write_text(new, encoding="utf-8")
    print("README index updated")
