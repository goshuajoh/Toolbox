"""Maker-checker in Excel: robot proposes, a named human approves, robot executes only approved + untampered rows."""
import hashlib
import json
import numbers
from datetime import date, datetime

import numpy as np
import pandas as pd

from .excel_write import add_dropdown, protect_sheet

_SHEET = "Approvals"


def _canon(v):
    """Normalise a value so it hashes the same before and after an Excel round trip: 99 == 99.0 == Decimal('99'),
    -0.0 == 0, date == Timestamp (to the millisecond Excel keeps), NA / NaT / '' are blank, CR LF == LF."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    try:
        if pd.isna(v) is True:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (numbers.Number, np.number)):
        return repr(round(float(v), 6) + 0.0)
    if isinstance(v, (datetime, date, pd.Timestamp, np.datetime64)):
        return pd.Timestamp(v).round("ms").isoformat()
    s = str(v).replace("_x000D_", "\r").replace("\r\n", "\n").replace("\r", "\n").strip()
    return None if s.lower() in ("", "nan", "none", "nat", "<na>") else s


def fingerprint(values):
    """Short SHA-256 of a dict of proposed values; detects edits by the checker. Blank values are left out, so a
    field another proposal has (an empty cell in this row) does not change it; blanking a filled value does."""
    canon = {k: c for k, c in ((k, _canon(v)) for k, v in values.items()) if c is not None}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:16]


def _proposal(action_id, action, proposed):
    """What the fingerprint covers: the id, the action and every proposed value, so a changed action or a row
    copied under a new id no longer matches."""
    return {"action_id": str(action_id), "action": action, **{f"proposed_{k}": v for k, v in proposed.items()}}


def propose(path, actions, protect_password=None):
    """Maker: write actions to an Excel sheet with decision=PENDING and a dropdown for the checker.
    actions = [{"action_id": "T1:AFFIRM", "action": "AFFIRM", "trade_id": "T1", "price": 101.25}, ...]"""
    rows = []
    for a in actions:
        proposed = {k: v for k, v in a.items() if k not in ("action_id", "action")}
        rows.append({"action_id": str(a["action_id"]), "action": a["action"], "decision": "PENDING", "checker": "",
                     "comment": "", "fingerprint": fingerprint(_proposal(a["action_id"], a["action"], proposed)),
                     **{f"proposed_{k}": v for k, v in proposed.items()}})
    df = pd.DataFrame(rows)
    if df["action_id"].duplicated().any():
        raise ValueError("duplicate action_id in proposals")
    df.to_excel(path, sheet_name=_SHEET, index=False)
    n = len(df) + 1
    add_dropdown(path, _SHEET, f"C2:C{n}", ["PENDING", "APPROVED", "REJECTED"])
    if protect_password:
        protect_sheet(path, _SHEET, protect_password, editable_cols=["C", "D", "E"])
    return path


def read_decisions(path, maker=None, approvers=None):
    """Checker read-back: DataFrame with an 'ok' column and 'why': OK only if APPROVED, a checker id is filled in,
    the row is untouched (id, action and values match the fingerprint) and its action_id appears once. Four eyes:
    maker = the robot's / maker's id(s), which may not approve (SAME_PERSON); approvers = the ids allowed to approve
    (NOT_AUTHORISED otherwise). Without both this is not a four-eyes control on its own. Ids come back as text."""
    d = pd.read_excel(path, sheet_name=_SHEET, dtype=object)          # '00123' stays text, as proposed
    d["action_id"] = d["action_id"].map(lambda v: v if v is None or isinstance(v, str) or pd.isna(v) else str(v))
    makers = {str(m).strip().lower() for m in ([maker] if isinstance(maker, str) else maker or [])}
    allowed = None if approvers is None else {str(a).strip().lower() for a in approvers}
    dup = d["action_id"].duplicated(keep=False)
    oks, whys = [], []
    for i, r in d.iterrows():
        proposed = {k[len("proposed_"):]: v for k, v in r.items() if str(k).startswith("proposed_")}
        decision = (_canon(r.get("decision")) or "PENDING").upper()
        checker = (_canon(r.get("checker")) or "").lower()
        intact = _canon(r.get("fingerprint")) == fingerprint(_proposal(r["action_id"], r.get("action"), proposed))
        why = ("DUPLICATE" if dup[i] else "TAMPERED" if not intact else "NOT_APPROVED" if decision != "APPROVED"
               else "NO_CHECKER_ID" if not checker else "SAME_PERSON" if checker in makers
               else "NOT_AUTHORISED" if allowed is not None and checker not in allowed else "OK")
        oks.append(why == "OK"); whys.append(why)
    return d.assign(ok=oks, why=whys)


def approved_actions(path, maker=None, approvers=None):
    """(approved action_ids, rejected/pending action_ids) — execute only the first list. Pass maker= and approvers=
    for a four-eyes check (see read_decisions)."""
    d = read_decisions(path, maker, approvers)
    return d.loc[d["ok"], "action_id"].tolist(), d.loc[~d["ok"], "action_id"].tolist()
