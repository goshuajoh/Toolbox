"""JSON-lines logging with PII masking."""
import json
import logging
import sys
from datetime import datetime, timezone

from .privacy import redact

_EXTRA_KEYS = ("event", "rows", "code", "item_id", "path", "count", "status", "ms")


class _Console(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def get_logger(log_path, run_id=None, name="robot", console=True):
    """Logger writing one JSON object per line to log_path (redacted, tagged with run_id); optional console."""
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")

    class _JsonLine(logging.Formatter):
        def format(self, r):
            d = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": r.levelname,
                 "run_id": run_id, "msg": redact(r.getMessage())}
            d.update({k: getattr(r, k) for k in _EXTRA_KEYS if hasattr(r, k)})
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
