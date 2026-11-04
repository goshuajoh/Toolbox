"""Validate and convert securities / banking identifiers by their check digits:
ISIN, CUSIP, SEDOL, LEI, IBAN, BIC, card numbers (Luhn)."""
import re

import pandas as pd

COUNTRIES = set("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO
JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR
MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO
RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV
TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW
""".split())   # ISO 3166-1 alpha-2 (+ XK)

CURRENCIES = set("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD CAD CDF CHF
CLP CNH CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG
HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD
MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF
SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS
VES VND VUV WST XAF XAG XAU XCD XDR XOF XPF YER ZAR ZMW ZWG
""".split())


def currency_valid(code):
    """True for an ISO 4217 currency code (plus CNH offshore yuan and XAU/XAG metals)."""
    return isinstance(code, str) and code.strip().upper() in CURRENCIES


IBAN_LENGTHS = {"AE": 23, "AT": 20, "AU": None, "BE": 16, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18,
                "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HU": 28, "IE": 22, "IL": 23, "IT": 27,
                "LU": 20, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "QA": 29, "SA": 24, "SE": 24, "TR": 26}


def _clean(v):
    return re.sub(r"\s+", "", str(v)).upper() if v is not None else ""


def _val(ch):
    """0-9 -> 0-9, A-Z -> 10-35 (the usual securities letter mapping)."""
    return int(ch) if ch.isdigit() else ord(ch) - 55


def luhn_check_digit(payload):
    """Check digit that makes payload + digit pass Luhn."""
    total = 0
    for i, d in enumerate(reversed(str(payload))):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return str((10 - total % 10) % 10)


def luhn_valid(number):
    """Luhn mod-10 check (card numbers, ISIN digit string)."""
    digits = re.sub(r"\D", "", str(number))
    return len(digits) >= 2 and luhn_check_digit(digits[:-1]) == digits[-1]


def isin_check_digit(first11):
    """12th character of an ISIN from its first 11."""
    return luhn_check_digit("".join(str(_val(c)) for c in _clean(first11)))


def isin_valid(isin):
    """2-letter country + 9 alphanumerics + check digit, e.g. US0378331005."""
    s = _clean(isin)
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", s)) and isin_check_digit(s[:11]) == s[11]


def cusip_check_digit(first8):
    """9th character of a CUSIP from its first 8."""
    total = 0
    for i, ch in enumerate(_clean(first8), start=1):
        v = int(ch) if ch.isdigit() else (ord(ch) - 55 if ch.isalpha() else {"*": 36, "@": 37, "#": 38}[ch])
        if i % 2 == 0:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def cusip_valid(cusip):
    """9 characters, last is the check digit, e.g. 037833100."""
    s = _clean(cusip)
    return bool(re.fullmatch(r"[0-9A-Z*@#]{8}\d", s)) and cusip_check_digit(s[:8]) == s[8]


def cusip_to_isin(cusip, country="US"):
    """US/CA ISIN from a CUSIP: country + CUSIP + check digit."""
    base = country.upper() + _clean(cusip)
    return base + isin_check_digit(base)


_SEDOL_W = (1, 3, 1, 7, 3, 9)


def sedol_check_digit(first6):
    """7th character of a SEDOL from its first 6 (no vowels allowed)."""
    total = sum(_val(c) * w for c, w in zip(_clean(first6), _SEDOL_W))
    return str((10 - total % 10) % 10)


def sedol_valid(sedol):
    """7 characters (consonants/digits) with check digit, e.g. 0263494."""
    s = _clean(sedol)
    return bool(re.fullmatch(r"[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}\d", s)) and sedol_check_digit(s[:6]) == s[6]


def sedol_to_isin(sedol, country="GB"):
    """GB/IE ISIN from a SEDOL: country + '00' + SEDOL + check digit."""
    base = country.upper() + "00" + _clean(sedol)
    return base + isin_check_digit(base)


def _mod97(s):
    return int("".join(str(_val(c)) for c in s)) % 97


def lei_valid(lei):
    """ISO 17442 LEI: 18 alphanumerics + 2 check digits, mod 97 == 1."""
    s = _clean(lei)
    return bool(re.fullmatch(r"[A-Z0-9]{18}\d{2}", s)) and _mod97(s) == 1


def lei_check_digits(first18):
    """Two check digits that complete an LEI."""
    return f"{98 - _mod97(_clean(first18) + '00'):02d}"


def iban_valid(iban, check_length=True):
    """IBAN mod-97 check (spaces allowed), with country length check for common countries."""
    s = _clean(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s) or s[:2] not in COUNTRIES:
        return False
    expected = IBAN_LENGTHS.get(s[:2])
    if check_length and expected and len(s) != expected:
        return False
    return _mod97(s[4:] + s[:4]) == 1


def bic_valid(bic, check_country=True):
    """SWIFT BIC: 4 bank + 2 country + 2 location (+ 3 branch), e.g. DBSSSGSG / DBSSSGSGXXX; country must be ISO."""
    s = _clean(bic)
    ok = bool(re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?", s))
    return ok and (not check_country or s[4:6] in COUNTRIES)


_VALIDATORS = {"isin": isin_valid, "cusip": cusip_valid, "sedol": sedol_valid, "lei": lei_valid,
               "iban": iban_valid, "bic": bic_valid, "luhn": luhn_valid, "card": luhn_valid}


def validate_ids(series, kind):
    """Boolean Series: is each value a valid <kind> (isin/cusip/sedol/lei/iban/bic/card)? Blanks -> False."""
    f = _VALIDATORS[kind.lower()]
    return pd.Series([False if pd.isna(v) or not str(v).strip() else bool(f(v)) for v in series],
                     index=series.index, dtype=bool)


def identify_id(value):
    """Best guess of which identifier a value is ('ISIN', 'CUSIP', 'SEDOL', 'LEI', 'IBAN', 'BIC') or None."""
    for kind in ("isin", "lei", "iban", "cusip", "sedol", "bic"):
        if _VALIDATORS[kind](value):
            return kind.upper()
    return None
