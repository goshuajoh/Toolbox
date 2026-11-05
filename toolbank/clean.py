"""Type conversion, text cleansing, normalising keys/names, column selection and tolerant column matching."""
import difflib
import re
import unicodedata

import numpy as np
import pandas as pd

from .identifiers import CURRENCIES

_INVISIBLE = "[ ​‌‍﻿\t\r\n]+"   # not raw: pyarrow (RE2) needs real characters
_DASHES = "[−–—‐‑]"              # unicode minus, en/em dash, hyphens typed by Word
# letters glued to digits ('USD1,200', '100DR') are still a code, so letter look-arounds instead of \b
_CCY_SYMBOLS = r"US\$|S\$|HK\$|A\$|C\$|NZ\$|NT\$|(?<![A-Z])RM(?![A-Z])|[$€£¥₹₩]"
_CCY_CODES = "(?<![A-Z])(?:" + "|".join(sorted(CURRENCIES)) + ")(?![A-Z])"
_DEBIT = r"(?<![A-Z])(?:DR|DEBIT)(?![A-Z])\.?"
_DEBIT_CREDIT = r"(?<![A-Z])(?:DR|CR|DEBIT|CREDIT)(?![A-Z])\.?"


def _text(series):
    """Values as Python text in an object Series ('' for blanks), so .str methods use Python's re (look-arounds
    and unicode classes), never pyarrow's RE2."""
    def one(v):
        if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and np.isnan(v)):
            return ""
        return str(v)
    return pd.Series([one(v) for v in series], index=series.index, dtype=object)


def to_number(series):
    """Lenient number parse for bank exports -> float64; unparsable -> NaN. Handles '1,234.50', '(1,000)',
    '100.00-' (trailing minus), '1,000 DR' / 'CR' (DR = negative, a sign is applied once), 'S$ 12', 'USD1,200',
    NBSP and unicode minus signs; 'inf' -> NaN. Decimal mark as in parse_amount: '1,234' is thousands,
    '1.234,56' / '10,5' decimal comma."""
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return series.astype("float64")
    s = _text(series).str.replace(_INVISIBLE, " ", regex=True).str.strip().str.upper()
    s = s.str.replace(_DASHES, "-", regex=True)
    s = s.str.replace(rf"{_CCY_SYMBOLS}|{_CCY_CODES}", " ", regex=True)
    dr = s.str.contains(_DEBIT, regex=True)
    s = s.str.replace(_DEBIT_CREDIT, " ", regex=True)
    s = s.str.replace(r"['\s]", "", regex=True)
    paren = s.str.match(r"^\(.*\)$")
    s = s.str.replace(r"^\((.*)\)$", r"\1", regex=True)
    trailing = s.str.match(r"^.+-$")
    s = s.str.replace(r"^(.+)-$", r"\1", regex=True)
    leading = s.str.match(r"^[-+]?-")
    s = s.str.replace(r"^[-+]+", "", regex=True)                    # sign applied once, at the end
    both = s.str.contains(".", regex=False) & s.str.contains(",", regex=False)
    dec_comma = ((both & (s.str.rfind(",") > s.str.rfind("."))) |                  # 1.234,56
                 (~both & (s.str.count(",") == 1) & ~s.str.contains(r",\d{3}$")) |  # 10,5 (not 1,234)
                 (~both & (s.str.count(r"\.") > 1)))                                 # 1.234.567
    s = s.where(~dec_comma, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    s = s.str.replace(",", "", regex=False)
    out = pd.to_numeric(s, errors="coerce").astype("float64")
    out = out.mask(np.isinf(out))
    neg = (dr | paren | trailing | leading).to_numpy(dtype=bool)
    return out.where(~neg, -out)


def _naive(values):
    """Datetime values as timezone-naive datetime64[us] (zone-aware text is converted to UTC first)."""
    if getattr(values.dt, "tz", None) is not None:
        values = values.dt.tz_convert(None)
    return values.astype("datetime64[us]")


def to_date(series, dayfirst=True):
    """Dates from whatever the file holds -> datetime64[us]; unparsable -> NaT. ISO / year-first text
    (2026-3-6, 2026/08/09) is read year-first, everything else day-first (06/03/2026 = 6 March) value by value.
    Also 20260306 (yyyymmdd, text or number), Excel serial numbers (46087, 46087.5) that leak into CSV exports,
    and ISO text with a zone ('...T14:30:00Z', '+08:00' -> UTC wall clock). 9999-12-31 'open-ended' dates are
    kept where pandas supports them (pandas 3), a date without a year -> NaT."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[us]")
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        n = pd.to_numeric(series, errors="coerce")
        serial, ymd = n.between(10_000, 99_999), n.between(19_000_101, 99_991_231)
        if serial.any():
            out[serial] = _naive(excel_serial_to_date(n[serial]))
        if ymd.any():
            out[ymd] = _naive(yyyymmdd_to_date(n[ymd].astype("int64")))
        return out
    s = series.astype("string").str.strip().fillna("")
    iso = s.str.match(r"^\d{4}-\d{1,2}-\d{1,2}").astype(bool)
    zoned = iso & s.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True).astype(bool)
    ymd = (~iso) & s.str.match(r"^\d{4}[/.]\d{1,2}[/.]\d{1,2}").astype(bool)     # 2026/08/09 is year-first
    digits8 = (~iso) & s.str.match(r"^\d{8}(\.0+)?$").astype(bool)               # 20260306
    serial = (~iso) & (~digits8) & s.str.match(r"^\d{5}(\.\d+)?$").astype(bool)   # 46087 / 46087.5
    rest = (~iso) & (~ymd) & (~digits8) & (~serial) & (s != "").astype(bool)
    if (iso & ~zoned).any():
        out[iso & ~zoned] = _naive(pd.to_datetime(s[iso & ~zoned], errors="coerce", format="ISO8601"))
    if zoned.any():
        out[zoned] = _naive(pd.to_datetime(s[zoned], errors="coerce", format="ISO8601", utc=True))
    if ymd.any():
        out[ymd] = _naive(pd.to_datetime(s[ymd], errors="coerce", yearfirst=True, dayfirst=False, format="mixed"))
    if digits8.any():
        out[digits8] = _naive(yyyymmdd_to_date(s[digits8]))
    if serial.any():
        out[serial] = _naive(excel_serial_to_date(s[serial]))
    if rest.any():
        parsed = pd.to_datetime(s[rest], errors="coerce", dayfirst=dayfirst, format="mixed")
        out[rest] = _naive(parsed.where(parsed.dt.year >= 1900))          # '6 Mar' (no year) is not a date
    return out


def excel_serial_to_date(series):
    """46087 -> 2026-03-06 (Excel serial numbers that leak into CSV exports)."""
    return pd.to_datetime(pd.to_numeric(series, errors="coerce"), unit="D", origin="1899-12-30")


def yyyymmdd_to_date(series):
    """20260306 / '20260306' -> date (mainframe style)."""
    return pd.to_datetime(series.astype("string").str.strip().str[:8], format="%Y%m%d", errors="coerce")


def cents(x):
    """Money as integer cents; compare these, never floats."""
    return int(round(float(x) * 100))


def snake_columns(df):
    """Column names -> snake_case ('Amount (SGD)' -> 'amount_sgd')."""
    df = df.copy()
    df.columns = [re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_") for c in df.columns]
    return df


def select(df, keep=None, drop=None, rename=None, dtypes=None, order=None):
    """Keep / drop / rename / retype / reorder columns in one call (Alteryx Select)."""
    out = df.copy()
    if keep:   out = out[list(keep)]
    if drop:   out = out.drop(columns=list(drop), errors="ignore")
    if rename: out = out.rename(columns=rename)
    if dtypes: out = out.astype(dtypes)
    if order:  out = out[list(order) + [c for c in out.columns if c not in order]]
    return out


def dynamic_rename(df, pattern=None, repl="", func=None):
    """Rename every column by regex (strip ' (SGD)') or by a function (str.upper)."""
    f = func or (lambda c: re.sub(pattern, repl, str(c)).strip())
    return df.rename(columns={c: f(c) for c in df.columns})


def auto_types(df, threshold=0.95):
    """Text columns >=95% numeric -> numbers, >=95% date-like -> dates. Zero-padded ids stay text."""
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        nn = s.dropna().astype(str).str.strip()
        nn = nn[nn != ""]
        if nn.empty or nn.str.match(r"^0\d+$").any():
            continue
        if pd.to_numeric(nn.str.replace(",", "", regex=False), errors="coerce").notna().mean() >= threshold:
            out[c] = to_number(s)
        elif nn.str.contains(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", regex=True).mean() >= threshold:
            out[c] = to_date(s)
    return out


_CASES = ("upper", "lower", "title", "capitalize", "casefold")


def cleanse(df, cols=None, case=None, remove_punct=False, blanks_to_nan=True):
    """Text columns: NBSP/zero-width/tabs -> space, collapse spaces, trim; optional case
    ('upper'/'lower'/'title') and punctuation removal; '' -> NaN."""
    if case is not None and case not in _CASES:
        raise ValueError(f"case must be one of {_CASES}, not {case!r}")
    out = df.copy()
    cols = [cols] if isinstance(cols, str) else cols
    cols = cols or [c for c in out.columns if out[c].dtype == object or pd.api.types.is_string_dtype(out[c])]
    for c in cols:
        mask = out[c].notna()
        t = (out.loc[mask, c].astype(str).str.replace(_INVISIBLE, " ", regex=True)
             .str.replace(r"\s+", " ", regex=True).str.strip())
        if remove_punct:
            t = t.str.replace(r"[^\w\s]", "", regex=True)
        if case:
            t = getattr(t.str, case)()
        out[c] = out[c].astype(object)
        out.loc[mask, c] = t
        if blanks_to_nan:
            out[c] = out[c].replace("", np.nan)
    return out


def drop_empty(df):
    """Remove rows and columns that are entirely blank."""
    return df.dropna(how="all").dropna(axis=1, how="all")


def fill_down(df, cols, by=None):
    """Fill blanks from the row above (group label printed once in report exports)."""
    out = df.copy()
    cols = [cols] if isinstance(cols, str) else list(cols)
    out[cols] = out.groupby(by)[cols].ffill() if by else out[cols].ffill()
    return out


def impute(df, col, method="median", value=None):
    """Fill nulls with 'mean' / 'median' / 'mode', or method='value' with value."""
    out = df.copy()
    if method == "value":
        fill = value
    elif method == "mode":
        fill = out[col].mode().iloc[0]
    else:
        fill = getattr(out[col], method)()
    out[col] = out[col].fillna(fill)
    return out


def map_values(series, mapping, case_insensitive=True, default=None):
    """Standardise labels via dict ('U.S.A.' -> 'US'); unmapped values kept (or set to default)."""
    if case_insensitive:
        m = {str(k).strip().upper(): v for k, v in mapping.items()}
        out = series.astype(str).str.strip().str.upper().map(m)
    else:
        out = series.map(mapping)
    out = out.astype(object)
    return out.where(out.notna(), series if default is None else default)


def find_replace(series, table, find_col, replace_col, mode="exact"):
    """Lookup-table replace: 'exact' = whole value, 'contains' = substrings (longest first)."""
    if mode == "exact":
        out = series.map(dict(zip(table[find_col], table[replace_col]))).astype(object)
        return out.where(out.notna(), series)
    s = series.astype("string")
    for f, r in sorted(zip(table[find_col].astype(str), table[replace_col].astype(str)), key=lambda x: -len(x[0])):
        s = s.str.replace(f, r, regex=False)
    return s


def normalise_key(series, width=None):
    """Matching key: upper, letters/digits only, optional zero-pad (fixes '123' vs ' 0123 ')."""
    s = series.astype("string").str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    return s.str.zfill(width) if width else s


_SUFFIXES = r"\b(PTE|PRIVATE|LTD|LIMITED|LLC|INC|CORP|CORPORATION|CO|COMPANY|GMBH|AG|SA|NV|BV|PLC|BHD|SDN|THE)\b"


def normalise_name(series):
    """Company names for matching: upper, & -> AND, drop punctuation + legal suffixes, collapse spaces."""
    s = series.astype("string").str.upper().str.replace("&", " AND ", regex=False)
    s = s.str.replace(r"[^\w\s]", " ", regex=True).str.replace(_SUFFIXES, " ", regex=True)
    return s.str.replace(r"\s+", " ", regex=True).str.strip()


def composite_key(df, cols, sep="|"):
    """One key column from several (for lookups/joins on multiple fields)."""
    return df[cols].astype("string").fillna("").agg(sep.join, axis=1)


# ==================== COLUMN NAMES (case / space / punctuation tolerant) ====================

class MissingColumns(KeyError, ValueError):
    """Raised when required columns are not in a table; the message names the near misses."""

    def __str__(self):
        return str(self.args[0]) if self.args else ""


def norm_label(text):
    """Canonical form of a header or label for tolerant matching: accents stripped, case folded, NBSP / tabs /
    punctuation -> single spaces, trimmed; letters of any script are kept ('金額' stays '金額').
    'Trade  Date:' == 'TRADE_DATE' == 'trade date'; blank -> ''."""
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return ""
    s = "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", s).strip().casefold()


def clean_headers(df):
    """Tidy column names as exports deliver them: text, NBSP / tabs / newlines -> one space, trimmed,
    blank or 'Unnamed: 3' -> col_4, duplicates numbered ('Amount', 'Amount' -> 'Amount', 'Amount_2'; a number
    already taken is skipped, so names are always unique)."""
    out = df.copy()
    used, names = set(), []
    for i, c in enumerate(out.columns, start=1):
        name = "" if c is None or (not isinstance(c, str) and pd.isna(c)) else str(c)
        name = re.sub(r"\s+", " ", re.sub(_INVISIBLE, " ", name)).strip()
        if not name or name.startswith("Unnamed:"):
            name = f"col_{i}"
        cand, k = name, 1
        while cand in used:
            k += 1
            cand = f"{name}_{k}"
        used.add(cand)
        names.append(cand)
    out.columns = names
    return out


def _suggest(wanted, columns, n=3):
    by_norm = {norm_label(c): c for c in columns if norm_label(c)}
    hits = difflib.get_close_matches(norm_label(wanted), list(by_norm), n=n, cutoff=0.6)
    return [by_norm[h] for h in hits]


def find_column(df, name, required=True):
    """The actual column name for `name`, matched exactly first, then tolerant of case / spaces / punctuation
    (norm_label). Two columns that both match -> MissingColumns (ambiguous). Not found -> MissingColumns naming
    the near misses, or None when required=False."""
    if name in df.columns:
        return name
    want = norm_label(name)
    hits = [c for c in df.columns if norm_label(c) == want] if want else []
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise MissingColumns(f"column {name!r} is ambiguous: {hits}")
    if not required:
        return None
    near = _suggest(name, df.columns)
    hint = f"; did you mean {near}?" if near else ""
    raise MissingColumns(f"missing column {name!r}{hint}; found {list(df.columns)}")


def resolve_columns(df, names):
    """{wanted: actual} for several names (see find_column); every missing name is reported in one error, and
    two wanted names that land on the same column are an error too."""
    names = [names] if isinstance(names, str) else list(names)
    found, missing = {}, []
    for n in names:
        actual = find_column(df, n, required=False)
        if actual is None:
            missing.append(n)
        else:
            found[n] = actual
    if missing:
        hints = {m: _suggest(m, df.columns) for m in missing}
        near = "; ".join(f"{m!r} -> {h}" for m, h in hints.items() if h)
        raise MissingColumns(f"missing columns {missing}" + (f"; did you mean {near}?" if near else "")
                             + f"; found {list(df.columns)}")
    clashes = {a: [w for w, x in found.items() if x == a] for a in set(found.values())}
    clashes = {a: ws for a, ws in clashes.items() if len(ws) > 1}
    if clashes:
        raise MissingColumns(f"several wanted names resolve to the same column: {clashes}")
    return found


def require_columns(df, names, source=""):
    """Check that columns exist (tolerant of case / spaces / punctuation) and rename them to the spelling you
    asked for, so the rest of the robot can use df['Trade Date'] whatever the file called it.
    Missing -> MissingColumns (a BusinessError once classified) listing near misses and the columns found."""
    names = [names] if isinstance(names, str) else list(names or [])
    if not names:
        return df
    try:
        found = resolve_columns(df, names)
    except MissingColumns as e:
        raise MissingColumns(f"{source}: {e}" if source else str(e)) from None
    return df.rename(columns={actual: str(wanted) for wanted, actual in found.items() if actual != str(wanted)})


def standard_columns(df, aliases, strict=False, keep_others=True):
    """Rename columns to your standard names from a list of aliases, tolerant of case / spaces / punctuation:
    aliases = {'trade_date': ['Trade Date', 'TradeDt', 'Trd Dt'], 'amount': ['Amount (SGD)', 'Amt']}.
    The standard name itself always counts as an alias. strict=True raises MissingColumns when a standard
    column is not found; keep_others=False drops columns that are not in the mapping."""
    rename, missing = {}, []
    for std, alts in aliases.items():
        alts = [alts] if isinstance(alts, str) else list(alts)
        hits = [c for a in [std, *alts] for c in [find_column(df, a, required=False)] if c is not None]
        hits = list(dict.fromkeys(hits))
        if len(hits) > 1:
            raise MissingColumns(f"several columns match {std!r}: {hits}")
        if hits:
            rename[hits[0]] = std
        else:
            missing.append(std)
    if missing and strict:
        raise MissingColumns(f"missing columns {missing}; found {list(df.columns)}")
    out = df.rename(columns=rename)
    return out if keep_others else out[list(rename.values())]


# ==================== NORMALISATION (standard values) ====================

_TRUE = {"y", "yes", "true", "t", "1", "on", "x", "✓"}
_FALSE = {"n", "no", "false", "f", "0", "off"}


def normalise_bool(series):
    """'Y'/'Yes'/'TRUE'/'1'/'x' -> True, 'N'/'No'/'0' -> False, anything else -> <NA>."""
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return pd.NA
        if isinstance(v, (bool, np.bool_)):
            return bool(v)
        t = str(v).strip().lower()
        if t.endswith(".0"):
            t = t[:-2]
        return True if t in _TRUE else False if t in _FALSE else pd.NA
    return pd.Series([one(v) for v in series], index=series.index, dtype="boolean")


CURRENCY_ALIASES = {
    "US$": "USD", "US DOLLAR": "USD", "DOLLAR": "USD", "$": "USD", "S$": "SGD", "SING DOLLAR": "SGD",
    "SINGAPORE DOLLAR": "SGD", "€": "EUR", "EURO": "EUR", "EUROS": "EUR", "£": "GBP", "STERLING": "GBP",
    "POUND": "GBP", "GBX": "GBP", "¥": "JPY", "YEN": "JPY", "HK$": "HKD", "RMB": "CNY", "YUAN": "CNY",
    "A$": "AUD", "AUS DOLLAR": "AUD", "SWISS FRANC": "CHF", "SFR": "CHF", "₹": "INR", "RUPEE": "INR",
    "RINGGIT": "MYR", "RM": "MYR", "BAHT": "THB", "WON": "KRW", "NT$": "TWD", "C$": "CAD", "NZ$": "NZD",
}


def normalise_currency(series, extra=None):
    """'US$' / 'us dollar' / 'S$' / '€' / 'rmb' -> ISO code; 3-letter codes upper-cased; unknown kept (upper)."""
    m = {**CURRENCY_ALIASES, **{k.upper(): v for k, v in (extra or {}).items()}}
    s = series.astype("string").str.strip().str.upper().str.replace(r"\s+", " ", regex=True)
    return s.map(lambda v: m.get(v, v) if v is not pd.NA else pd.NA).astype("string")


_SIDES = {"B": "BUY", "BUY": "BUY", "BOT": "BUY", "BOUGHT": "BUY", "PURCHASE": "BUY", "LONG": "BUY", "1": "BUY",
          "S": "SELL", "SELL": "SELL", "SLD": "SELL", "SOLD": "SELL", "SALE": "SELL", "SHORT": "SELL", "2": "SELL",
          "SS": "SELL_SHORT", "SELL SHORT": "SELL_SHORT", "SHORT SELL": "SELL_SHORT", "5": "SELL_SHORT"}


def normalise_side(series):
    """'B'/'Bot'/'Purchase'/'1' -> BUY, 'S'/'Sold'/'2' -> SELL, 'SS'/'5' -> SELL_SHORT; unknown -> <NA>."""
    s = series.astype("string").str.strip().str.upper()
    return s.map(lambda v: _SIDES.get(v, pd.NA) if v is not pd.NA else pd.NA).astype("string")


COUNTRY_ALIASES = {
    "SINGAPORE": "SG", "SGP": "SG", "UNITED STATES": "US", "USA": "US", "U.S.A.": "US", "U.S.": "US",
    "AMERICA": "US", "UNITED KINGDOM": "GB", "UK": "GB", "U.K.": "GB", "GREAT BRITAIN": "GB", "ENGLAND": "GB",
    "HONG KONG": "HK", "HKG": "HK", "CHINA": "CN", "PRC": "CN", "JAPAN": "JP", "INDIA": "IN", "GERMANY": "DE",
    "FRANCE": "FR", "SWITZERLAND": "CH", "AUSTRALIA": "AU", "MALAYSIA": "MY", "INDONESIA": "ID", "THAILAND": "TH",
    "PHILIPPINES": "PH", "VIETNAM": "VN", "VIET NAM": "VN", "SOUTH KOREA": "KR", "KOREA": "KR", "TAIWAN": "TW",
    "NETHERLANDS": "NL", "HOLLAND": "NL", "LUXEMBOURG": "LU", "IRELAND": "IE", "ITALY": "IT", "SPAIN": "ES",
    "CANADA": "CA", "UAE": "AE", "UNITED ARAB EMIRATES": "AE", "SAUDI ARABIA": "SA", "NEW ZEALAND": "NZ",
}


def normalise_country(series, extra=None):
    """Country names / 3-letter codes -> ISO alpha-2 ('Singapore' -> 'SG', 'U.K.' -> 'GB'); unknown kept (upper)."""
    m = {**COUNTRY_ALIASES, **{k.upper(): v for k, v in (extra or {}).items()}}
    s = series.astype("string").str.strip().str.upper().str.replace(r"\s+", " ", regex=True)
    return s.map(lambda v: m.get(v, v) if v is not pd.NA else pd.NA).astype("string")


def strip_accents(series):
    """'Café Société' -> 'Cafe Societe' (for matching names across systems)."""
    import unicodedata
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            return v
        return "".join(c for c in unicodedata.normalize("NFKD", str(v)) if not unicodedata.combining(c))
    return series.map(one)


def standardise_dates(series, fmt="%Y-%m-%d", dayfirst=True):
    """Mixed date text -> one text format (ISO by default); unparsable -> <NA>."""
    return to_date(series, dayfirst=dayfirst).dt.strftime(fmt).astype("string")


def minmax_scale(series):
    """Scale numbers to 0..1 (constant column -> 0)."""
    lo, hi = series.min(), series.max()
    return (series - lo) / (hi - lo) if hi != lo else series * 0.0


def zscore(series):
    """(x - mean) / std — how unusual each value is (|z| > 3 is a common outlier flag)."""
    sd = series.std()
    return (series - series.mean()) / sd if sd else series * 0.0


def outliers_iqr(series, k=1.5):
    """True where a value is outside Q1 - k*IQR .. Q3 + k*IQR (robust outlier flag)."""
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    return (series < q1 - k * iqr) | (series > q3 + k * iqr)
