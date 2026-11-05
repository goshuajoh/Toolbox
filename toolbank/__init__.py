"""toolbank — generic automation functions (data prep, Excel, OCR, browsers, APIs, email, SQL, Fabric, triggers).
`from toolbank import *` gives you all of them; README.md has the index."""
import importlib as _importlib
import inspect as _inspect

_MODULES = ["robust", "clean", "readers", "parse", "transform", "combine", "reshape", "formula", "rules", "quality",
            "identifiers", "regexkit", "excel_write", "excel_app", "dates", "files", "logs", "privacy", "approval",
            "docs", "ocr", "config", "notify", "api", "htmlparse", "web", "webdriver", "desktop", "outlook", "mail",
            "db", "lake", "triggers", "flow", "perf", "recipe"]
_CONST = (dict, list, tuple, set, frozenset, str, int, float)

__all__ = []
for _m in _MODULES:
    _mod = _importlib.import_module(f".{_m}", __name__)
    for _name, _obj in vars(_mod).items():
        if _name.startswith("_"):
            continue
        _own = (_inspect.isfunction(_obj) or _inspect.isclass(_obj)) and _obj.__module__ == _mod.__name__
        _constant = _name.isupper() and isinstance(_obj, _CONST) and not isinstance(_obj, bool)
        if (_own or _constant) and (_name not in globals() or _inspect.ismodule(globals()[_name])):
            globals()[_name] = _obj
            __all__.append(_name)

__version__ = "0.2.0"
