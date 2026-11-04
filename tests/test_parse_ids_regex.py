import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from toolbank import *

MT940 = """{1:F01BANKSGSGAXXX0000000000}{2:I940BANKSGSGXXXXN}{4:
:20:STMT0306
:25:SG1234567890
:28C:00065/001
:60F:C260305SGD10000,00
:61:2603060306C1500,00NTRFINV123//BK001
Payment from client
:86:INVOICE 123 ACME PTE LTD
:61:2603060306D250,50NCHGNONREF//BK002
:86:BANK CHARGES
:62F:C260306SGD11249,50
:86:STATEMENT END NOTE
-}"""

CAMT = """<?xml version="1.0"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt><Id>S1</Id>
<Acct><Id><IBAN>GB82WEST12345698765432</IBAN></Id></Acct>
<Ntry><NtryRef>E1</NtryRef><Amt Ccy="EUR">100.50</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>
<BookgDt><Dt>2026-03-06</Dt></BookgDt><ValDt><DtTm>2026-03-06T10:00:00</DtTm></ValDt><AcctSvcrRef>ASR1</AcctSvcrRef>
<BkTxCd><Domn><Cd>PMNT</Cd><Fmly><Cd>RCDT</Cd><SubFmlyCd>ESCT</SubFmlyCd></Fmly></Domn></BkTxCd>
<NtryDtls><TxDtls><Refs><EndToEndId>E2E-1</EndToEndId></Refs><RltdPties><Dbtr><Nm>ACME</Nm></Dbtr></RltdPties>
<RmtInf><Ustrd>INV 1</Ustrd><Ustrd>INV 2</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
<Ntry><Amt Ccy="EUR">20</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts>BOOK</Sts><BookgDt><Dt>2026-03-07</Dt></BookgDt>
<NtryDtls><TxDtls><RltdPties><Cdtr><Pty><Nm>VENDOR</Nm></Pty></Cdtr></RltdPties></TxDtls></NtryDtls></Ntry>
</Stmt></BkToCstmrStmt></Document>"""


def _fix(fields):
    body = "\x01".join(f"{k}={v}" for k, v in fields) + "\x01"
    return body + f"10={sum(body.encode('latin-1')) % 256:03d}\x01"


def test_json_helpers(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"data": {"rows": [{"id": 1, "x": {"y": 2}}, {"id": 2, "x": {"y": 3}}]}, "run": "r1"}')
    assert read_json(p)["run"] == "r1" and read_json('{"a": 1}') == {"a": 1} and read_json(b"[1]") == [1]
    assert read_json({"a": 1}) == {"a": 1}
    (tmp_path / "b.jsonl").write_text('{"a": 1}\n\n{"a": 2}\n')
    assert read_jsonl(tmp_path / "b.jsonl") == [{"a": 1}, {"a": 2}]
    assert flatten_json({"a": {"b": 1}, "items": [{"id": 7}]}) == {"a.b": 1, "items.0.id": 7}
    df = json_to_df(p, record_path=["data", "rows"])
    assert df.columns.tolist() == ["id", "x.y"] and df["x.y"].tolist() == [2, 3]
    src = pd.DataFrame({"k": [1, 2], "payload": ['{"a": {"b": 5}}', None]})
    out = json_column_to_columns(src, "payload")
    assert out.loc[0, "payload.a.b"] == 5 and pd.isna(out.loc[1, "payload.a.b"])
    recs = df_to_json_records(pd.DataFrame({"d": pd.to_datetime(["2026-03-06", None]), "v": [1.5, np.nan]}),
                              path=tmp_path / "o.json")
    assert recs == [{"d": "2026-03-06", "v": 1.5}, {"d": None, "v": None}]
    assert json.loads((tmp_path / "o.json").read_text()) == recs


def test_xml_helpers():
    xml = ('<Doc xmlns="urn:x"><Trade id="T1"><Ccy>USD</Ccy><Amt cur="USD">10</Amt><Leg>A</Leg><Leg>B</Leg>'
           '</Trade><Trade id="T2"><Ccy>SGD</Ccy><Amt>20</Amt></Trade></Doc>')
    df = xml_to_df(xml, "Trade")
    assert df.loc[0, "@id"] == "T1" and df.loc[0, "Amt@cur"] == "USD" and df.loc[0, "Leg[2]"] == "B"
    assert df["Amt"].tolist() == ["10", "20"] and pd.isna(df.loc[1, "Leg[1]"])
    out = df_to_xml(pd.DataFrame({"id": ["T1"], "amt": [1.5]}), "trades", "trade")
    assert "<trade><id>T1</id><amt>1.5</amt></trade>" in out


def test_key_values():
    text = "Trade Date: 06/03/2026\nAmount = 1,000.00\nCounterparty\n\n  ACME BANK  \nNotes: none"
    assert parse_key_values(text)["Trade Date"] == "06/03/2026"
    kv = parse_key_values(text, keys=["trade date", "Counterparty", "amount"])
    assert kv == {"trade date": "06/03/2026", "amount": "1,000.00", "Counterparty": "ACME BANK"}
    df = key_values_to_df([text, "Amount: 5"], keys=["Amount"], source_names=["a.pdf", "b.pdf"])
    assert df["Amount"].tolist() == ["1,000.00", "5"]


def test_mt940():
    stmts, tx = parse_mt940(MT940)
    s = stmts.iloc[0]
    assert (s["statement_ref"], s["account"], s["statement_no"]) == ("STMT0306", "SG1234567890", "00065/001")
    assert s["opening_balance"] == 10000.0 and s["opening_date"] == date(2026, 3, 5) and s["closing_balance"] == 11249.5
    assert s["info_86"] == "STATEMENT END NOTE"
    assert tx["signed_amount"].tolist() == [1500.0, -250.5] and tx["type"].tolist() == ["NTRF", "NCHG"]
    assert tx["customer_ref"].tolist() == ["INV123", "NONREF"] and tx["bank_ref"].tolist() == ["BK001", "BK002"]
    assert tx.loc[0, "details"] == "Payment from client" and tx["info_86"].tolist() == ["INVOICE 123 ACME PTE LTD",
                                                                                         "BANK CHARGES"]
    assert s["opening_balance"] + tx["signed_amount"].sum() == pytest.approx(s["closing_balance"])
    _, tx2 = parse_mt940(":20:X\n:61:2601021231D5,00NTRFREF\n:61:2512310102RD7,5NTRFREF2")
    assert tx2.loc[0, "entry_date"] == date(2025, 12, 31) and tx2.loc[1, "entry_date"] == date(2026, 1, 2)
    assert tx2["signed_amount"].tolist() == [-5.0, 7.5]
    assert parse_swift_balance("D260306USD1,5")["signed"] == -1.5


def test_camt053():
    df = parse_camt053(CAMT)
    assert df["signed_amount"].tolist() == [100.5, -20.0] and df["ccy"].tolist() == ["EUR", "EUR"]
    r = df.iloc[0]
    assert r["account"] == "GB82WEST12345698765432" and r["status"] == "BOOK" and r["end_to_end_id"] == "E2E-1"
    assert r["bank_tx_code"] == "PMNT-RCDT-ESCT" and r["counterparty"] == "ACME" and r["remittance"] == "INV 1 | INV 2"
    assert r["value_date"] == pd.Timestamp("2026-03-06") and pd.isna(df.loc[1, "value_date"])
    assert df.loc[1, "status"] == "BOOK" and df.loc[1, "counterparty"] == "VENDOR"


def test_fix():
    m = _fix([("8", "FIX.4.4"), ("35", "8"), ("55", "VOD.L"), ("54", "1"), ("38", "1000"), ("31", "2.15"),
              ("448", "A"), ("448", "B")])
    d = parse_fix(m)
    assert d["MsgType"] == "8" and d["Symbol"] == "VOD.L" and d["448"] == ["A", "B"] and FIX_SIDE[d["Side"]] == "BUY"
    assert fix_checksum_ok(m) and not fix_checksum_ok(m.replace("VOD.L", "VOD.X"))
    piped = m.replace("\x01", "|")
    assert fix_checksum_ok(piped) and parse_fix(piped, names=False)["55"] == "VOD.L"
    assert fix_to_df([m, "", m]).shape[0] == 2


def test_amounts_shorthand_percent():
    cases = {"USD 1,200.50": (1200.5, "USD"), "1.200,50 EUR": (1200.5, "EUR"), "(1,000.00)": (-1000.0, None),
             "1,000.00 DR": (-1000.0, None), "S$ 12": (12.0, "SGD"), "1,234": (1234.0, None), "1,5": (1.5, None),
             "CHF 1'234.50": (1234.5, "CHF"), "-12.5": (-12.5, None), "Net 100": (100.0, None),
             "1 200,50 eur": (1200.5, "EUR"), "£5": (5.0, "GBP")}
    for text, want in cases.items():
        assert parse_amount(text) == want, text
    assert parse_amount("1.234", decimal=",") == (1234.0, None)
    assert np.isnan(parse_amount(None)[0]) and np.isnan(parse_amount("n/a")[0])
    df = parse_amounts(pd.Series(["USD 1", None, "2,50 EUR"], index=[5, 6, 7]))
    assert df.index.tolist() == [5, 6, 7] and df.loc[7, "amount"] == 2.5 and df.loc[7, "ccy"] == "EUR"
    s = parse_shorthand(pd.Series(["1.5m", "250k", "2bn", "3mm", "5 million", "1,200", "x", None]))
    assert s.tolist()[:6] == [1.5e6, 250e3, 2e9, 3e6, 5e6, 1200.0] and s.isna().tolist()[6:] == [True, True]
    p = parse_percent(pd.Series(["5%", "250bp", "250 bps", "0.05", "abc"]))
    assert p.tolist()[:4] == [0.05, 0.025, 0.025, 0.05] and np.isnan(p.iloc[4])
    assert parse_percent(pd.Series(["5"]), plain_is_percent=True).iloc[0] == 0.05


def test_tenors():
    assert parse_tenor("3M") == (3, "M") and parse_tenor("on") == (0, "ON") and parse_tenor("10Y") == (10, "Y")
    fri = date(2026, 3, 6)
    assert tenor_to_date(fri, "ON") == date(2026, 3, 9)
    assert tenor_to_date(fri, "TN") == date(2026, 3, 10) and tenor_to_date(fri, "SN") == date(2026, 3, 11)
    assert tenor_to_date(fri, "SW") == date(2026, 3, 17)
    assert tenor_to_date(date(2026, 1, 30), "1M") == date(2026, 2, 27)          # modified following
    assert tenor_to_date(date(2026, 1, 30), "1M", roll="following") == date(2026, 3, 2)
    assert tenor_to_date(fri, "1W", holidays=["2026-03-13"]) == date(2026, 3, 16)
    with pytest.raises(ValueError):
        parse_tenor("3Q")


def test_identifiers():
    assert isin_valid("US0378331005") and not isin_valid("US0378331006") and isin_check_digit("US037833100") == "5"
    assert cusip_valid("037833100") and cusip_to_isin("037833100") == "US0378331005"
    assert sedol_valid("0263494") and sedol_valid("B0YBKJ7") and sedol_to_isin("B03MLX2") == "GB00B03MLX29"
    assert not sedol_valid("A263494")                                                # vowels not allowed
    assert lei_valid("HWUPKR0MPOU8FGXBT394") and lei_check_digits("HWUPKR0MPOU8FGXBT3") == "94"
    assert iban_valid("GB82WEST12345698765432") and iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("GB82WEST12345698765431") and not iban_valid("GB82WEST1234569876543")
    assert bic_valid("DBSSSGSG") and bic_valid("DBSSSGSGXXX") and not bic_valid("PAYMENTS")
    assert luhn_valid("4111111111111111") and luhn_check_digit("411111111111111") == "1"
    assert currency_valid("sgd") and currency_valid("CNH") and not currency_valid("NET")
    s = pd.Series(["US0378331005", "", None, "XX", pd.NA], dtype=object)
    assert validate_ids(s, "isin").tolist() == [True, False, False, False, False]
    assert validate_ids(pd.Series(["US0378331005", None], dtype="string"), "ISIN").tolist() == [True, False]
    assert [identify_id(v) for v in ["US0378331005", "HWUPKR0MPOU8FGXBT394", "GB82WEST12345698765432",
                                     "037833100", "B0YBKJ7", "DBSSSGSG", "hello"]] == \
        ["ISIN", "LEI", "IBAN", "CUSIP", "SEDOL", "BIC", None]


def test_regexkit():
    t = regex_test(r"(?P<ccy>[A-Z]{3}) (?P<amt>[\d.]+)", ["USD 10.5 and EUR 3", "none"])
    assert t["matched"].tolist() == [True, False] and t.loc[0, "groups"] == {"ccy": "USD", "amt": "10.5"}
    assert t.loc[0, "all"] == ["USD 10.5", "EUR 3"]
    s = pd.Series(["a-1", "b-22", None])
    assert regex_replace(s, r"-(\d+)", r"#\1").tolist()[:2] == ["a#1", "b#22"]
    assert regex_extract_all(pd.Series(["1 and 22"]), r"\d+").iloc[0] == ["1", "22"]
    assert regex_split(pd.Series(["a, b;c"]), r"[,;]\s*").iloc[0] == ["a", "b", "c"]
    text = ("Pay ISIN US0378331005 (bad US0378331006) to GB82WEST12345698765432 via DBSSSGSG, mail ops@bank.test "
            "on 2026-03-06 at 09:30 for USD 1,200.50 or 5% ; NRIC S1234567D")
    found = find_patterns(text, ["isin", "iban", "bic", "email", "date_iso", "time", "ccy_amount", "percent"])
    by = found.groupby("kind")["value"].apply(list).to_dict()
    assert by["isin"] == ["US0378331005"] and by["iban"] == ["GB82WEST12345698765432"] and "DBSSSGSG" in by["bic"]
    assert by["email"] == ["ops@bank.test"] and by["ccy_amount"] == ["USD 1,200.50"] and by["percent"] == ["5%"]
    assert found["start"].is_monotonic_increasing
    masked = mask_patterns(text)
    assert "ops@bank.test" not in masked and "<email:***>" in masked and "<sg_nric:***>" in masked
    assert set(PATTERNS) >= {"isin", "lei", "iban", "email", "sg_nric"}
