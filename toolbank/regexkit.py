"""Regex toolkit: a pattern library for bank data, a tester for building patterns, find/replace/split/mask."""
import re

import pandas as pd

from .identifiers import bic_valid, cusip_valid, iban_valid, isin_valid, lei_valid, sedol_valid

PATTERNS = {
    "isin": r"\b[A-Z]{2}[A-Z0-9]{9}\d\b",
    "cusip": r"\b\d{3}[0-9A-Z]{5}\d\b",
    "sedol": r"\b[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}\d\b",
    "lei": r"\b[A-Z0-9]{18}\d{2}\b",
    "iban": r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b",
    "bic": r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b",
    "email": r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b",
    "url": r"https?://[^\s<>\"')]+",
    "date_iso": r"\b\d{4}-\d{2}-\d{2}\b",
    "date_dmy": r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})\b",
    "date_text": r"\b\d{1,2}[ -](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?[ -]\d{2,4}\b",
    "time": r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b",
    "ccy_amount": r"\b[A-Z]{3} ?-?\d{1,3}(?:,\d{3})*(?:\.\d+)?\b",
    "amount": r"(?<![\w.,])\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?(?![\w,])|(?<![\w.,])-?\d+\.\d{2}(?![\w.,])",
    "percent": r"-?\d+(?:\.\d+)?\s?%",
    "sg_nric": r"\b[STFGM]\d{7}[A-Z]\b",
    "sg_phone": r"(?:\+65[ -]?)?(?<!\d)[689]\d{3}[ -]?\d{4}(?!\d)",
    "ipv4": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
    "swift_tag": r"(?m)^:\d{2}[A-Z]?:",
}

_CHECKS = {"isin": isin_valid, "cusip": cusip_valid, "sedol": sedol_valid, "lei": lei_valid, "iban": iban_valid,
           "bic": bic_valid}


def regex_test(pattern, samples, flags=0):
    """Try a pattern on sample strings -> table of sample, matched, first match, named/numbered groups, all matches.
    Use it to build and debug patterns before running them on real data."""
    rx = re.compile(pattern, flags)
    rows = []
    for s in samples:
        m = rx.search(str(s))
        rows.append({"sample": s, "matched": bool(m), "match": m.group(0) if m else None,
                     "groups": (m.groupdict() or m.groups()) if m else None,
                     "all": [x.group(0) for x in rx.finditer(str(s))]})
    return pd.DataFrame(rows)


def regex_replace(series, pattern, repl, flags=0):
    """Regex replace on a column; repl may use \\1 or \\g<name>."""
    return series.astype("string").str.replace(pattern, repl, regex=True, flags=flags)


def regex_extract_all(series, pattern, flags=0):
    """All matches per value -> Series of lists (explode() for one row per match)."""
    rx = re.compile(pattern, flags)
    return pd.Series([[m.group(0) for m in rx.finditer(str(v))] if pd.notna(v) else [] for v in series],
                     index=series.index, dtype=object)


def regex_split(series, pattern, maxsplit=0):
    """Split each value on a regex -> Series of lists."""
    rx = re.compile(pattern)
    return pd.Series([rx.split(str(v), maxsplit=maxsplit) if pd.notna(v) else [] for v in series],
                     index=series.index, dtype=object)


def find_patterns(text, kinds=None, validate=True):
    """Scan text for known patterns (PATTERNS keys) -> DataFrame kind, value, start, end.
    validate=True drops ISIN/CUSIP/SEDOL/LEI/IBAN hits with a wrong check digit and BIC hits with no real country."""
    rows = []
    for kind in kinds or PATTERNS:
        for m in re.finditer(PATTERNS[kind], str(text)):
            value = m.group(0)
            if validate and kind in _CHECKS and not _CHECKS[kind](value):
                continue
            rows.append({"kind": kind, "value": value, "start": m.start(), "end": m.end()})
    return pd.DataFrame(rows, columns=["kind", "value", "start", "end"]).sort_values("start", ignore_index=True)


def mask_patterns(text, kinds=("email", "sg_nric", "iban", "sg_phone"), mask="***"):
    """Replace matches of the given pattern kinds with <kind:***>."""
    out = str(text)
    for kind in kinds:
        out = re.sub(PATTERNS[kind], f"<{kind}:{mask}>", out)
    return out
