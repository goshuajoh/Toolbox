"""Parse semi-structured data: JSON, XML, key-value text, SWIFT MT (MT940/950), ISO 20022 camt.053, FIX,
amounts with currency, 1.5m/250k shorthand, percentages/basis points, tenors."""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .identifiers import CURRENCIES

try:   # defusedxml protects against malicious XML (entity bombs); falls back to the standard library
    import defusedxml.ElementTree as ET
except ImportError:   # pragma: no cover
    import xml.etree.ElementTree as ET


# ==================== JSON ====================

def read_json(source):
    """Path, JSON text, bytes or an already-parsed object -> Python object."""
    if isinstance(source, (dict, list)):
        return source
    if isinstance(source, bytes):
        source = source.decode("utf-8-sig")
    s = str(source)
    if s.lstrip()[:1] in ("{", "["):
        return json.loads(s)
    return json.loads(Path(s).read_text(encoding="utf-8-sig"))


def read_jsonl(path):
    """JSON Lines file (one JSON object per line) -> list of objects."""
    with open(path, encoding="utf-8-sig") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def flatten_json(obj, sep=".", prefix=""):
    """Nested dict/list -> flat dict {'a.b': 1, 'items.0.id': 7}."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten_json(v, sep, f"{prefix}{k}{sep}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten_json(v, sep, f"{prefix}{i}{sep}"))
    else:
        out[prefix[: -len(sep)] if prefix else ""] = obj
    return out


def json_to_df(source, record_path=None, meta=None, sep="."):
    """JSON (path/text/object) -> DataFrame. record_path='items' or ['data', 'rows'] picks the list of records;
    meta=['id', ['account', 'name']] copies parent fields onto every record. Nested dicts become 'a.b' columns."""
    data = read_json(source)
    if record_path is None and isinstance(data, dict):
        data = [data]
    return pd.json_normalize(data, record_path=record_path, meta=meta, sep=sep, errors="ignore")


def json_column_to_columns(df, col, prefix=None, sep="."):
    """A column holding JSON text or dicts -> flattened columns (prefix defaults to 'col.')."""
    def _load(v):
        if isinstance(v, (dict, list)):
            return v
        if isinstance(v, str) and v.strip():
            return json.loads(v)
        return {}
    flat = pd.DataFrame([flatten_json(_load(v), sep) for v in df[col]], index=df.index)
    flat = flat.add_prefix(f"{col}{sep}" if prefix is None else prefix)
    return pd.concat([df.drop(columns=col), flat], axis=1)


def df_to_json_records(df, path=None, date_format="%Y-%m-%d", indent=None):
    """DataFrame -> list of dicts (dates as text, NaN as null), optionally saved to a JSON file for an API upload."""
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime(date_format)
    records = json.loads(out.to_json(orient="records", date_format="iso"))
    if path:
        Path(path).write_text(json.dumps(records, indent=indent, ensure_ascii=False), encoding="utf-8")
    return records


# ==================== XML ====================

def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _xml_root(source):
    if hasattr(source, "tag") and hasattr(source, "attrib"):
        return source
    if isinstance(source, bytes):
        return ET.fromstring(source)
    s = str(source)
    if s.lstrip().startswith("<"):
        return ET.fromstring(s)
    return ET.parse(s).getroot()


def _flatten_el(el, sep="/", path=""):
    out = {}
    for k, v in el.attrib.items():
        out[f"{path}@{_local(k)}"] = v
    text = (el.text or "").strip()
    if text:
        out[path or "#text"] = text
    kids = list(el)
    counts = {}
    for child in kids:
        counts[_local(child.tag)] = counts.get(_local(child.tag), 0) + 1
    seen = {}
    for child in kids:
        name = _local(child.tag)
        seen[name] = seen.get(name, 0) + 1
        key = name if counts[name] == 1 else f"{name}[{seen[name]}]"
        out.update(_flatten_el(child, sep, f"{path}{sep}{key}" if path else key))
    return out


def xml_to_df(source, row_tag, sep="/"):
    """Every element named row_tag (namespaces ignored) -> one row. Descendants become columns:
    'Amt', 'Amt@Ccy', 'Dbtr/Nm'; repeated children get [1], [2]."""
    root = _xml_root(source)
    return pd.DataFrame([_flatten_el(el, sep) for el in root.iter() if _local(el.tag) == row_tag])


def xml_find(el, path):
    """First element at a slash path of local names ('Acct/Id/IBAN'), ignoring namespaces; None if absent."""
    nodes = [el]
    for part in path.split("/"):
        nodes = [c for n in nodes for c in list(n) if _local(c.tag) == part]
        if not nodes:
            return None
    return nodes[0]


def xml_text(el, path, default=None):
    """Text of the first element at path (see xml_find), stripped."""
    node = xml_find(el, path)
    return node.text.strip() if node is not None and node.text else default


def df_to_xml(df, root_tag="rows", row_tag="row", path=None):
    """DataFrame -> simple XML (one element per row, one child per column); returns the XML string."""
    import xml.etree.ElementTree as StdET
    root = StdET.Element(root_tag)
    for rec in df.to_dict("records"):
        row = StdET.SubElement(root, row_tag)
        for k, v in rec.items():
            child = StdET.SubElement(row, re.sub(r"[^\w.-]", "_", str(k)))
            child.text = "" if pd.isna(v) else str(v)
    xml = StdET.tostring(root, encoding="unicode")
    if path:
        Path(path).write_text(xml, encoding="utf-8")
    return xml


# ==================== KEY: VALUE TEXT ====================

def parse_key_values(text, keys=None, next_line=True):
    """'Trade Date: 06/03/2026' / 'Amount = 1,000' lines -> dict. With keys (case-insensitive), only those labels
    are kept and, if next_line, a label alone on its line takes the next non-empty line as its value (PDF layouts)."""
    want = {re.sub(r"\s+", " ", k).strip().lower(): k for k in keys} if keys else None
    out = {}
    lines = [ln.rstrip() for ln in str(text).splitlines()]
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(?P<k>[^:=]{1,60}?)\s*[:=]\s*(?P<v>.*?)\s*$", line)
        if m:
            k = re.sub(r"\s+", " ", m.group("k")).strip()
            if want is None:
                out[k] = m.group("v")
            elif k.lower() in want:
                out[want[k.lower()]] = m.group("v")
            continue
        if want and next_line:
            k = re.sub(r"\s+", " ", line).strip().lower()
            if k in want:
                nxt = next((ln.strip() for ln in lines[i + 1:] if ln.strip()), None)
                out[want[k]] = nxt
    return out


def key_values_to_df(texts, keys=None, source_names=None):
    """Many documents' text -> one row each (columns = keys)."""
    rows = [parse_key_values(t, keys) for t in texts]
    df = pd.DataFrame(rows, columns=list(keys) if keys else None)
    if source_names is not None:
        df.insert(0, "source", list(source_names))
    return df


# ==================== SWIFT MT ====================

def parse_swift_fields(text):
    """SWIFT MT message (or just block 4) -> list of (tag, value) in order; multi-line values joined with '\\n'."""
    text = str(text).replace("\r\n", "\n")
    m = re.search(r"\{4:(.*?)-\}", text, re.S)
    body = m.group(1) if m else text
    fields, tag, buf = [], None, []
    for line in body.split("\n"):
        mm = re.match(r"^:(\d{2}[A-Z]?):(.*)$", line)
        if mm:
            if tag is not None:
                fields.append((tag, "\n".join(buf).rstrip()))
            tag, buf = mm.group(1), [mm.group(2)]
        elif tag is not None and line.strip() not in ("", "-"):
            buf.append(line)
    if tag is not None:
        fields.append((tag, "\n".join(buf).rstrip()))
    return fields


def _swift_amount(s):
    return round(float(s.replace(",", ".")), 2) if s else np.nan


def _yymmdd(s):
    return date(2000 + int(s[:2]), int(s[2:4]), int(s[4:6]))


def parse_swift_balance(value):
    """'C260306SGD1234,56' (:60F:/:62F:) -> dict(mark, date, ccy, amount, signed)."""
    m = re.match(r"^(?P<mark>[CD])(?P<date>\d{6})(?P<ccy>[A-Z]{3})(?P<amount>[\d,]+)$", value.strip())
    if not m:
        return {"mark": None, "date": None, "ccy": None, "amount": np.nan, "signed": np.nan}
    amt = _swift_amount(m.group("amount"))
    return {"mark": m.group("mark"), "date": _yymmdd(m.group("date")), "ccy": m.group("ccy"), "amount": amt,
            "signed": amt if m.group("mark") == "C" else -amt}


_MT_61 = re.compile(r"^(?P<value_date>\d{6})(?P<entry_date>\d{4})?(?P<mark>RC|RD|C|D)(?P<funds_code>[A-Z])?"
                    r"(?P<amount>\d[\d,]*)(?P<type>[A-Z][A-Z0-9]{3})(?P<rest>.*)$")


def parse_mt940(text):
    """MT940/MT950 statement(s) -> (statements_df, transactions_df).
    Transactions: value/entry date, mark (C/D/RC/RD), signed amount (credit +), type, references, :86: info."""
    statements, txns, cur, prev = [], [], None, None
    for tag, value in parse_swift_fields(text):
        prev_tag, prev = prev, tag
        if tag == "20":
            cur = {"statement_ref": value.strip(), "account": None, "statement_no": None}
            statements.append(cur)
            continue
        if cur is None:
            cur = {"statement_ref": None, "account": None, "statement_no": None}
            statements.append(cur)
        if tag == "25":
            cur["account"] = value.strip()
        elif tag == "28C":
            cur["statement_no"] = value.strip()
        elif tag in ("60F", "60M", "62F", "62M", "64", "65"):
            b = parse_swift_balance(value)
            name = {"60": "opening", "62": "closing", "64": "available", "65": "forward_available"}[tag[:2]]
            for k in ("date", "ccy", "signed"):
                cur[f"{name}_{'balance' if k == 'signed' else k}"] = b[k]
        elif tag == "61":
            first, _, extra = value.partition("\n")
            m = _MT_61.match(first.strip())
            if not m:
                txns.append({"statement_ref": cur["statement_ref"], "account": cur["account"], "raw_61": value,
                             "parse_error": True})
                continue
            vd = _yymmdd(m.group("value_date"))
            ed = None
            if m.group("entry_date"):
                ed = date(vd.year, int(m.group("entry_date")[:2]), int(m.group("entry_date")[2:]))
                if (ed - vd).days < -180:
                    ed = ed.replace(year=ed.year + 1)
                elif (ed - vd).days > 180:
                    ed = ed.replace(year=ed.year - 1)
            cust, _, bank = m.group("rest").partition("//")
            amt = _swift_amount(m.group("amount"))
            mark = m.group("mark")
            txns.append({"statement_ref": cur["statement_ref"], "account": cur["account"],
                         "value_date": vd, "entry_date": ed, "mark": mark, "funds_code": m.group("funds_code"),
                         "amount": amt, "signed_amount": amt if mark in ("C", "RD") else -amt,
                         "type": m.group("type"), "customer_ref": cust.strip() or None,
                         "bank_ref": bank.strip() or None, "details": extra.strip() or None, "info_86": None,
                         "parse_error": False})
        elif tag == "86":
            info = value.replace("\n", " ").strip()
            if prev_tag == "61" and txns:
                txns[-1]["info_86"] = info          # transaction information
            else:
                cur["info_86"] = info               # statement-level information
    return pd.DataFrame(statements), pd.DataFrame(txns)


# ==================== ISO 20022 camt.053 ====================

def parse_camt053(source):
    """ISO 20022 camt.053 bank statement XML -> DataFrame of entries (Ntry): account, signed amount, ccy,
    booking/value date, references, status, counterparty, remittance text. Works across camt.053 versions."""
    root = _xml_root(source)
    rows = []
    for stmt in (el for el in root.iter() if _local(el.tag) == "Stmt"):
        acct = xml_text(stmt, "Acct/Id/IBAN") or xml_text(stmt, "Acct/Id/Othr/Id")
        for ntry in (c for c in list(stmt) if _local(c.tag) == "Ntry"):
            amt_el = xml_find(ntry, "Amt")
            amt = float(amt_el.text) if amt_el is not None and amt_el.text else np.nan
            credit = xml_text(ntry, "CdtDbtInd") == "CRDT"
            tx = xml_find(ntry, "NtryDtls/TxDtls")
            party_path = "RltdPties/Dbtr" if credit else "RltdPties/Cdtr"
            cpty = None
            if tx is not None:
                cpty = xml_text(tx, f"{party_path}/Nm") or xml_text(tx, f"{party_path}/Pty/Nm")
            ustrd = [(e.text or "").strip() for e in ntry.iter() if _local(e.tag) == "Ustrd"]
            dom = [xml_text(ntry, f"BkTxCd/Domn/{p}") for p in ("Cd", "Fmly/Cd", "Fmly/SubFmlyCd")]
            rows.append({
                "statement_id": xml_text(stmt, "Id"), "account": acct,
                "amount": amt, "ccy": amt_el.get("Ccy") if amt_el is not None else None,
                "signed_amount": amt if credit else -amt, "cdt_dbt": xml_text(ntry, "CdtDbtInd"),
                "booking_date": xml_text(ntry, "BookgDt/Dt") or xml_text(ntry, "BookgDt/DtTm"),
                "value_date": xml_text(ntry, "ValDt/Dt") or xml_text(ntry, "ValDt/DtTm"),
                "status": xml_text(ntry, "Sts/Cd") or xml_text(ntry, "Sts"),
                "entry_ref": xml_text(ntry, "NtryRef"), "acct_svcr_ref": xml_text(ntry, "AcctSvcrRef"),
                "end_to_end_id": xml_text(tx, "Refs/EndToEndId") if tx is not None else None,
                "bank_tx_code": "-".join(d for d in dom if d) or xml_text(ntry, "BkTxCd/Prtry/Cd"),
                "counterparty": cpty, "remittance": " | ".join(u for u in ustrd if u) or None,
            })
    df = pd.DataFrame(rows)
    for c in ("booking_date", "value_date"):
        if c in df:
            df[c] = pd.to_datetime(df[c].astype("string").str[:10], format="%Y-%m-%d", errors="coerce")
    return df


# ==================== FIX ====================

FIX_TAGS = {
    "1": "Account", "6": "AvgPx", "8": "BeginString", "9": "BodyLength", "10": "CheckSum", "11": "ClOrdID",
    "14": "CumQty", "15": "Currency", "17": "ExecID", "22": "SecurityIDSource", "31": "LastPx", "32": "LastQty",
    "34": "MsgSeqNum", "35": "MsgType", "37": "OrderID", "38": "OrderQty", "39": "OrdStatus", "40": "OrdType",
    "44": "Price", "48": "SecurityID", "49": "SenderCompID", "52": "SendingTime", "54": "Side", "55": "Symbol",
    "56": "TargetCompID", "58": "Text", "59": "TimeInForce", "60": "TransactTime", "64": "SettlDate",
    "75": "TradeDate", "150": "ExecType", "151": "LeavesQty",
}
FIX_SIDE = {"1": "BUY", "2": "SELL", "5": "SELL_SHORT", "6": "SELL_SHORT_EXEMPT"}


def parse_fix(msg, sep=None, names=True):
    """FIX message ('8=FIX.4.4|35=8|55=VOD.L|...', SOH or '|') -> dict; repeated tags become lists."""
    sep = sep or ("\x01" if "\x01" in msg else "|")
    out = {}
    for part in msg.strip().strip(sep).split(sep):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = FIX_TAGS.get(k, k) if names else k
        if key in out:
            out[key] = (out[key] if isinstance(out[key], list) else [out[key]]) + [v]
        else:
            out[key] = v
    return out


def fix_checksum_ok(msg, sep=None):
    """True if the FIX 10= checksum matches (sum of bytes before '10=' mod 256)."""
    sep = sep or ("\x01" if "\x01" in msg else "|")
    raw = msg.replace(sep, "\x01")
    i = raw.rfind("\x0110=")
    if i < 0:
        return False
    body = raw[: i + 1]
    expected = raw[i + 4:].split("\x01")[0]
    return f"{sum(body.encode('latin-1')) % 256:03d}" == expected


def fix_to_df(messages, sep=None):
    """Many FIX messages -> DataFrame (one row each, tag names as columns)."""
    return pd.DataFrame([parse_fix(m, sep) for m in messages if str(m).strip()])


# ==================== AMOUNTS / NUMBERS ====================

CCY_SYMBOLS = {"US$": "USD", "S$": "SGD", "HK$": "HKD", "A$": "AUD", "C$": "CAD", "NZ$": "NZD",
               "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₩": "KRW", "$": "USD"}


def parse_amount(text, decimal=None):
    """'USD 1,200.50' / '1.200,50 EUR' / '(1,000.00)' / '1,000.00 DR' / 'S$ 12' -> (amount, ccy).
    decimal='.' or ',' forces the decimal mark; otherwise the last of '.'/',' wins and '1,234' is thousands."""
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return np.nan, None
    if isinstance(text, (int, float, np.integer, np.floating)):
        return float(text), None
    s = str(text).strip().replace("\u00a0", " ")
    ccy = None
    for sym in sorted(CCY_SYMBOLS, key=len, reverse=True):
        if sym in s:
            ccy = CCY_SYMBOLS[sym]
            s = s.replace(sym, " ")
            break
    for code in re.findall(r"\b([A-Za-z]{3})\b", s):
        if code.upper() in CURRENCIES:                  # 'Net 100' is not a currency
            ccy = ccy or code.upper()
            break
    neg = bool(re.search(r"\(.*\d.*\)", s)) or bool(re.search(r"(^|\s)-\s*\d|\d\s*-$", s)) \
        or bool(re.search(r"\b(DR|D|DEBIT)\b\.?$", s.upper().strip()))
    num = re.search(r"\d[\d.,' ]*", s)
    if not num:
        return np.nan, ccy
    n = num.group(0).strip().replace("'", "").replace(" ", "")
    dec = decimal
    if dec is None:
        if "." in n and "," in n:
            dec = "." if n.rfind(".") > n.rfind(",") else ","
        elif n.count(",") == 1 and len(n.split(",")[1]) != 3:
            dec = ","
        elif n.count(".") > 1:
            dec = ","
        else:
            dec = "."
    thousands = "," if dec == "." else "."
    n = n.replace(thousands, "").replace(dec, ".")
    try:
        val = float(n)
    except ValueError:
        return np.nan, ccy
    return (-val if neg else val), ccy


def parse_amounts(series, decimal=None):
    """Vectorised parse_amount -> DataFrame(amount, ccy) aligned to the input index."""
    pairs = [parse_amount(v, decimal) for v in series]
    return pd.DataFrame(pairs, columns=["amount", "ccy"], index=series.index)


_UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "mio": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "tn": 1e12, "trillion": 1e12}


def parse_shorthand(series):
    """'1.5m' / '250k' / '2bn' / '3mm' / '5 million' / '1,200' -> float (finance size shorthand)."""
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return np.nan
        if isinstance(v, (int, float, np.integer, np.floating)):
            return float(v)
        m = re.fullmatch(r"\s*(-?[\d,]*\.?\d+)\s*([a-zA-Z]*)\s*", str(v))
        if not m:
            return np.nan
        unit = m.group(2).lower()
        if unit and unit not in _UNITS:
            return np.nan
        return float(m.group(1).replace(",", "")) * _UNITS.get(unit, 1.0)
    return pd.Series([one(v) for v in series], index=series.index, dtype="float64")


def parse_percent(series, plain_is_percent=False):
    """'5%' -> 0.05, '250bp' / '250 bps' -> 0.025, '0.05' -> 0.05 (or 0.0005 if plain_is_percent)."""
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return np.nan
        s = str(v).strip().lower().replace(",", "")
        m = re.fullmatch(r"(-?\d*\.?\d+)\s*(%|bps?|bp)?", s)
        if not m:
            return np.nan
        x = float(m.group(1))
        unit = m.group(2)
        if unit == "%":
            return x / 100
        if unit in ("bp", "bps"):
            return x / 10_000
        return x / 100 if plain_is_percent else x
    return pd.Series([one(v) for v in series], index=series.index, dtype="float64")


def parse_tenor(tenor):
    """'3M' -> (3, 'M'); 'ON'/'TN'/'SN'/'SW' returned as (0, code). Units: D, W, M, Y."""
    t = str(tenor).strip().upper()
    if t in ("ON", "TN", "SN", "SW"):
        return 0, t
    m = re.fullmatch(r"(\d+)\s*([DWMY])", t)
    if not m:
        raise ValueError(f"unrecognised tenor {tenor!r}")
    return int(m.group(1)), m.group(2)


def tenor_to_date(start, tenor, holidays=(), roll="modified_following"):
    """End date of a tenor from start. ON=+1bd, TN=+2bd, SN=+3bd, SW=spot(+2bd)+1W; D/W/M/Y are calendar
    periods then rolled to a business day (default modified following)."""
    from .dates import add_business_days, adjust_business_day
    start = pd.Timestamp(start).date()
    n, unit = parse_tenor(tenor)
    if unit == "ON":
        return add_business_days(start, 1, holidays)
    if unit == "TN":
        return add_business_days(start, 2, holidays)
    if unit == "SN":
        return add_business_days(start, 3, holidays)
    if unit == "SW":
        return adjust_business_day(add_business_days(start, 2, holidays) + timedelta(days=7), holidays, roll)
    if unit == "D":
        end = start + timedelta(days=n)
    elif unit == "W":
        end = start + timedelta(weeks=n)
    elif unit == "M":
        end = (pd.Timestamp(start) + pd.DateOffset(months=n)).date()
    else:
        end = (pd.Timestamp(start) + pd.DateOffset(years=n)).date()
    return adjust_business_day(end, holidays, roll)
