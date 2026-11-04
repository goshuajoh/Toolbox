"""Type conversion, text cleansing, normalising keys/names, column selection."""
import re

import numpy as np
import pandas as pd


def to_number(series):
    """'1,234.50' / '(10.00)' / 'S$ 12' -> float; unparsable -> NaN."""
    s = series.astype(str).str.strip()
    s = s.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    s = s.str.replace(r"[,\s$€£¥]|S\$", "", regex=True)
    return pd.to_numeric(s, errors="coerce")


def to_date(series, dayfirst=True):
    """ISO strings parsed as ISO; everything else day-first, value by value. Unparsable -> NaT."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype("string").str.strip().fillna("")
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    iso = s.str.match(r"^\d{4}-\d{2}-\d{2}").astype(bool)
    rest = (~iso) & (s != "").astype(bool)
    if iso.any():
        out[iso] = pd.to_datetime(s[iso], errors="coerce", format="ISO8601")
    if rest.any():
        out[rest] = pd.to_datetime(s[rest], errors="coerce", dayfirst=dayfirst, format="mixed")
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


_INVISIBLE = "[\u00a0\u200b\u200c\u200d\ufeff\t\r\n]+"   # not raw: pyarrow (RE2) needs real characters


def cleanse(df, cols=None, case=None, remove_punct=False, blanks_to_nan=True):
    """Text columns: NBSP/zero-width/tabs -> space, collapse spaces, trim; optional case
    ('upper'/'lower'/'title') and punctuation removal; '' -> NaN."""
    out = df.copy()
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
