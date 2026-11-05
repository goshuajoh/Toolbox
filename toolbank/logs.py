"""JSON-lines logging with PII masking."""
import json
import logging
import sys
from datetime import datetime, timezone

from .privacy import redact

_EXTRA_KEYS = ("event", "rows", "code", "item_id", "path", "count", "status", "ms")
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


class _Console(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def get_logger(log_path, run_id=None, name="robot", console=True):
    """Logger writing one JSON object per line to log_path (redacted, tagged with run_id); optional console.
    Every key passed in extra={...} lands in the JSON line (the usual ones first: event, rows, code, item_id,
    path, count, status, ms); an exception logged with exc_info adds 'exc'."""
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")

    class _JsonLine(logging.Formatter):
        def format(self, r):
            d = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": r.levelname,
                 "run_id": run_id, "msg": redact(r.getMessage())}
            extras = [k for k in _EXTRA_KEYS if k in r.__dict__] + \
                     [k for k in r.__dict__ if k not in _STANDARD and k not in _EXTRA_KEYS and not k.startswith("_")]
            d.update({k: redact(v) if isinstance(v, str) else v for k, v in ((k, r.__dict__[k]) for k in extras)})
            if r.exc_info and r.exc_info[0] is not None:
                d["exc"] = redact(self.formatException(r.exc_info))[-2000:]
            return json.dumps(d, default=str)

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
