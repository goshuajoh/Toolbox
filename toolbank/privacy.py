"""Mask PII, describe data without values (safe for AI), generate fake data."""
import random
import re
import string
from collections import Counter
from datetime import date, timedelta

import pandas as pd

PII_PATTERNS = [
    ("nric", r"\b[STFGM]\d{7}[A-Z]\b"),
    ("email", r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    ("iban", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    ("account", r"(?<![\w-])\d(?: ?\d){7,18}(?![\w-])"),   # 8-19 digits, spaces allowed (cards)
]


def redact(text, patterns=PII_PATTERNS):
    """Mask NRIC/FIN, emails, IBANs, 8-19 digit numbers. Reference codes like GL-00012345 survive."""
    text = str(text)
    for name, rx in patterns:
        text = re.sub(rx, f"<{name}:***>", text)
    return text


def redact_frame(df, cols):
    """Replace whole columns with '***' (before sharing a sample)."""
    out = df.copy()
    for c in cols:
        out[c] = out[c].where(out[c].isna(), "***")
    return out


def shape_of(v):
    """'GL-000123' -> 'AA-999999' (A upper, a lower, 9 digit)."""
    return "".join("9" if c.isdigit() else "A" if c.isupper() else "a" if c.isalpha() else c for c in str(v))


def _number_shape(nn):
    """'up to 11 digits, decimals, negatives': the size of the numbers, not their values."""
    big = float(nn.abs().max())
    digits = len(str(int(big))) if big >= 1 else 1
    parts = [f"up to {digits} digit{'s' if digits > 1 else ''}"]
    if (nn != nn.round()).any():
        parts.append("decimals")
    if (nn < 0).any():
        parts.append("negatives")
    return ", ".join(parts)


def describe_shape(df):
    """Column type, null %, uniqueness and a shape per column: a text pattern ('AA-999999'), a length range, or for
    numbers only the size ('11 digits, decimals, negatives'), never a value. Review it before you share it and follow
    your bank's data and AI-use policy: column names and rare patterns can still identify things."""
    rows = []
    for c in df.columns:
        s = df[c]
        nn = s.dropna()
        info = {"column": c, "nulls": f"{1 - len(nn) / max(len(s), 1):.0%}",
                "unique": bool(len(nn)) and nn.nunique() == len(nn), "type": "", "shape": ""}
        if pd.api.types.is_bool_dtype(s):
            info.update(type="boolean")
        elif pd.api.types.is_numeric_dtype(s):
            info.update(type="number", shape=_number_shape(nn) if len(nn) else "")
        elif pd.api.types.is_datetime64_any_dtype(s):
            info.update(type="date")
        else:
            txt = nn.astype(str)
            shapes = Counter(shape_of(v) for v in txt.head(500))
            top, n = shapes.most_common(1)[0] if shapes else ("", 0)
            info.update(type="text", shape=top if n / max(len(txt.head(500)), 1) > 0.8
                        else (f"{txt.str.len().min()}-{txt.str.len().max()} chars" if len(txt) else ""))
        rows.append(info)
    return pd.DataFrame(rows)


def fake_from_pattern(pattern, rng):
    """'GL-999999' -> 'GL-482913' style fake value."""
    return "".join(str(rng.randint(0, 9)) if c == "9" else rng.choice(string.ascii_uppercase) if c == "A"
                   else rng.choice(string.ascii_lowercase) if c == "a" else c for c in pattern)


def synth(n, spec, seed=42):
    """Fake rows. spec = {"ref": ("pattern", "GL-999999"), "amt": ("number", -1e4, 1e4),
    "d": ("date", "2026-01-01", "2026-12-31"), "ccy": ("choice", ["SGD", "USD"]), "id": ("seq", 1)}."""
    rng = random.Random(seed)
    out = {k: [] for k in spec}
    for i in range(n):
        for col, s in spec.items():
            kind = s[0]
            if kind == "pattern":
                out[col].append(fake_from_pattern(s[1], rng))
            elif kind == "number":
                out[col].append(round(rng.uniform(s[1], s[2]), 2))
            elif kind == "int":
                out[col].append(rng.randint(s[1], s[2]))
            elif kind == "date":
                start = date.fromisoformat(s[1]) if len(s) > 1 else date(2026, 1, 1)
                end = date.fromisoformat(s[2]) if len(s) > 2 else start + timedelta(days=365)
                out[col].append(start + timedelta(days=rng.randint(0, (end - start).days)))
            elif kind == "choice":
                out[col].append(rng.choice(s[1]))
            elif kind == "seq":
                out[col].append((s[1] if len(s) > 1 else 1) + i)
            else:
                raise ValueError(f"unknown spec kind {kind!r} for {col}")
    return pd.DataFrame(out)
