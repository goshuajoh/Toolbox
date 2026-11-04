"""Maker-checker in Excel: robot proposes, a named human approves, robot executes only approved + untampered rows."""
import hashlib
import json
from datetime import date, datetime

import numpy as np
import pandas as pd

from .excel_write import add_dropdown, protect_sheet

_SHEET = "Approvals"


def _canon(v):
    """Normalise a value so it hashes the same before and after an Excel round trip (99 == 99.0, date == Timestamp)."""
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, float, np.integer, np.floating)):
        return repr(round(float(v), 6))
    if isinstance(v, (datetime, date, pd.Timestamp, np.datetime64)):
        return pd.Timestamp(v).isoformat()
    s = str(v).strip()
    return None if s.lower() in ("", "nan", "none", "nat") else s


def fingerprint(values):
    """Short SHA-256 of a dict of proposed values; detects edits by the checker."""
    canon = {k: _canon(v) for k, v in values.items()}
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:16]


def propose(path, actions, protect_password=None):
    """Maker: write actions to an Excel sheet with decision=PENDING and a dropdown for the checker.
    actions = [{"action_id": "T1:AFFIRM", "action": "AFFIRM", "trade_id": "T1", "price": 101.25}, ...]"""
    rows = []
    for a in actions:
        proposed = {k: v for k, v in a.items() if k not in ("action_id", "action")}
        rows.append({"action_id": a["action_id"], "action": a["action"], "decision": "PENDING", "checker": "",
                     "comment": "", "fingerprint": fingerprint(proposed),
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


def read_decisions(path):
    """Checker read-back: DataFrame with an 'ok' column (APPROVED + checker id + fingerprint intact) and 'why'."""
    d = pd.read_excel(path, sheet_name=_SHEET, dtype={"action_id": str, "decision": str, "checker": str,
                                                      "fingerprint": str})
    oks, whys = [], []
    for _, r in d.iterrows():
        proposed = {k[len("proposed_"):]: v for k, v in r.items() if str(k).startswith("proposed_")}
        decision = (_canon(r.get("decision")) or "PENDING").upper()
        checker = _canon(r.get("checker"))
        intact = _canon(r.get("fingerprint")) == fingerprint(proposed)
        why = ("TAMPERED" if not intact else "NOT_APPROVED" if decision != "APPROVED"
               else "NO_CHECKER_ID" if not checker else "OK")
        oks.append(why == "OK"); whys.append(why)
    return d.assign(ok=oks, why=whys)


def approved_actions(path):
    """(approved action_ids, rejected/pending action_ids) — execute only the first list."""
    d = read_decisions(path)
    return d.loc[d["ok"], "action_id"].tolist(), d.loc[~d["ok"], "action_id"].tolist()
