"""JSON-lines logging with PII masking."""
import json
import logging
import math
import sys
from datetime import datetime, timezone

from .privacy import redact

_EXTRA_KEYS = ("event", "rows", "code", "item_id", "path", "count", "status", "ms")
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}
_RESERVED = ("ts", "level", "run_id", "msg", "exc")
_COUNTS = ("rows", "count", "ms")                       # numbers by name: never masked as account numbers


def _clean(v, mask_ints=True):
    """Extra values as JSON-safe, redacted data: text is masked (also inside dicts / lists), numpy and pandas
    scalars become plain numbers, NaN / inf become null, anything else is masked text."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, int):
        return redact(str(v)) if mask_ints and abs(v) >= 10_000_000 else v   # 8+ digits: an account number
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_clean(x) for x in v]
    if hasattr(v, "item") and not hasattr(v, "shape") or getattr(v, "shape", None) == ():
        try:
            return _clean(v.item())                       # numpy / pandas scalars
        except (ValueError, TypeError, AttributeError):
            pass
    return redact(str(v))


class _Console(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def get_logger(log_path, run_id=None, name="robot", console=True):
    """Logger writing one JSON object per line to log_path (redacted, tagged with run_id); optional console.
    Every key passed in extra={...} lands in the JSON line (the usual ones first: event, rows, code, item_id,
    path, count, status, ms), redacted also inside dicts and lists; ts, level, run_id, msg and exc are the
    line's own keys and cannot be overridden. An exception logged with exc_info adds 'exc'."""
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")

    class _JsonLine(logging.Formatter):
        def format(self, r):
            d = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": r.levelname,
                 "run_id": run_id, "msg": redact(r.getMessage())}
            extras = [k for k in _EXTRA_KEYS if k in r.__dict__] + \
                     [k for k in r.__dict__ if k not in _STANDARD and k not in _EXTRA_KEYS and not k.startswith("_")]
            d.update({k: _clean(r.__dict__[k], mask_ints=k not in _COUNTS) for k in extras if k not in _RESERVED})
            if r.exc_info and r.exc_info[0] is not None:
                d["exc"] = redact(self.formatException(r.exc_info))[-2000:]
            return json.dumps(d, default=lambda o: redact(str(o)))

    log = logging.getLogger(name)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    log.setLevel(logging.INFO)
    log.propagate = False
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(_JsonLine())
    log.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(_Console("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        log.addHandler(ch)
    return log
