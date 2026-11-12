"""
title: Business rules kept in Excel by operations, no code change needed
order: 100
when: >-
  Checks that operations change more often than the robot is released (limits, currency lists, new counterparty
  checks): they keep them in a rules workbook the owner signs off, and the robot flags and routes every item by it.
tags: [rules, controls]
needs: []
blocks: [RobotError, apply_rules, condition, file_sha256, first_match, flag_summary, rules_from_excel, safe_write_excel, validate_rules]
decisions:
  - "One sheet, Rules: code | column | op | value | severity | active | description. Operators are typed as words (eq, ne, gt, ge, lt, le, in, not in, is blank, between): in Excel a cell starting with '=' becomes a formula. Lists ('in', 'between') take ', ' or ';' between values: ';' when a number has thousands commas ('1,000; 2,000'). Known gap in toolbank: rules_from_excel checks 'between' before it knows the column's type, so it compares the bounds as text and refuses 'between 500; 1000' on a number column; until that is fixed, write a range as two rows with one code, ge 500 and le 1000. Rows with the same code must all hold (AND). active = N switches a rule off without deleting it and a blank counts as on; a value that is not clearly yes or no ('Active') stops the run rather than guess."
  - "The whole sheet is checked against the day's data before any item is touched. toolbank (rules_from_excel, then validate_rules one row at a time against the data's column types) finds an unknown operator, a half-filled row, an unclear active value, a column not in the data, a blank value where the operator needs one, 'between' without two values or with low above high, and a value a number or date column cannot read ('thirty', '2026-13-45', an ambiguous '1,5': the rule would match nothing and say nothing). The block adds what toolbank does not check: severity other than HIGH or MEDIUM, gt / ge / lt / le / between on a text column (as text '999' > '1000000'), a comma between digits in a comma-separated list on a number or date column ('in 1,5, 2,5' reads as 4 values), any other error the rule raises on today's data (a bad regex), no active rule. Any problem raises RobotError BAD_RULES with the problems (exit 5). Not a BusinessError per row: one typo would make every item an exception. Not SystemError_: a re-run fails the same way."
  - "Routing is code, not sheet: first_match over ROUTES. Any HIGH flag -> ESCALATE, any flag -> REVIEW, a blank in a column a rule reads -> REVIEW, else AUTO. Operations can add and tighten checks; changing who sees an item (ROUTES, SEVERITIES) is a robot release."
  - "A blank never matches a rule: 'ccy not in USD, EUR' is false for a blank ccy, so that row would pass every check unseen and go AUTO. missing_input sends it to REVIEW. An amount that did not parse is NaN and counts as blank. A column blank by design would flood REVIEW: fill its blanks with a value before the rules and write the rule on that value."
  - "The workbook is copied into the run folder first, and the hash, the read and the evidence all use the copy: someone saving the shared file mid-run cannot change what ran. The copy's name, and the evidence file's, carry the first 12 characters of its SHA-256, so a re-run with another version keeps both. A rules file that is missing or locked fails the copy with an OSError: SystemError_ (exit 3), run again once it is back."
  - "Only the newest row of the sign-off register counts; its sha256 is compared ignoring case and spaces (PowerShell Get-FileHash prints capitals). An edited file, or an older signed-off one put back, raises RobotError RULES_NOT_SIGNED_OFF (exit 5). To roll back, the owner signs off the old file again as a new row."
  - "A rule that matched no row stays in Flag summary with 0. A text value that never occurs (a currency typed 'USDD') raises nothing, so the 0 is the sign to check it."
governance:
  - "Evidence per run: rules file, SHA-256, active rule rows, codes and the sign-off row (version, approved_by, approved_on) in 'Rules version'; the active rules as applied in 'Rules'; rows per flag; rows per route; id, flags and route per item, no amounts. The copy of the workbook stays in the run folder."
  - "The rules owner signs off every change: they review the edited workbook (a run that stops at RULES_NOT_SIGNED_OFF has passed every rule check) and add its SHA-256, version, their id and the date to the register. Keep the register in a folder only the owner can write to, so an editor cannot approve their own change. Saving the workbook in Excel changes its hash even when no rule changed: sign off the file as saved and make the shared copy read-only."
  - "BAD_RULES and RULES_NOT_SIGNED_OFF carry the file name, its SHA-256 and the problems: rule codes, columns, operators and rule values (configuration), never a data value. The log line carries the rules version, the copy's name (with the hash) and counts per route."
"""
import re
import shutil
from pathlib import Path

import pandas as pd

from toolbank import (RobotError, apply_rules, condition, file_sha256, first_match, flag_summary, rules_from_excel,
                      safe_write_excel, validate_rules)

SEVERITIES = ("HIGH", "MEDIUM")
ORDER_OPS = {">", ">=", "<", "<=", "gt", "ge", "gte", "lt", "le", "lte", "greater than", "less than", "above",
             "below", "at least", "at most", "between"}  # compare by size: only on number and date columns
LIST_OPS = {"in", "not in", "one of", "not one of", "between"}   # split at ';' if there is one, else at commas
ROUTES = [("ESCALATE", [("high_flags", "ge", 1)]),        # the first line that holds wins; a change is a release
          ("REVIEW", [("flags_count", "ge", 1)]), ("REVIEW", [("missing_input", "is true", None)])]  # blank: never AUTO


def check_rules(rules, df):
    """Every problem of the active rules on this data ([] = OK): validate_rules, plus what it does not check."""
    if rules.empty:                                       # every rule switched off: nothing would ever be flagged
        return ["no active rule: every item would go to AUTO"]
    probs = []
    for i, r in rules.iterrows():
        if str(r.get("severity")).strip().upper() not in SEVERITIES:
            probs.append(f"{r['code']}: severity must be HIGH or MEDIUM")
        if bad := validate_rules(rules.loc[[i]], df):     # column, blank or unreadable value, 'between': each once
            probs += bad
            continue
        op, s, typed = str(r["op"]).strip().lower(), df[r["column"]], str(r["value"])
        sized = pd.api.types.is_datetime64_any_dtype(s) or \
            (pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s))
        if op in ORDER_OPS and not sized:
            probs.append(f"{r['code']}: column {r['column']!r} is text; convert it (to_number, to_date) before "
                         f"{r['op']!r}")
        elif sized and op in LIST_OPS and ";" not in typed and re.search(r"\d,\d", typed):   # '1,5, 2,5': 4 values
            probs.append(f"{r['code']}: in {r['value']!r} a comma sits between digits: type '1.5, 2.5', or "
                         "'1,000; 2,000' for thousands")
        else:
            try:
                condition(df, r["column"], r["op"], r["value"])   # the rule on today's data, before any item
            except Exception as e:                        # a bad regex, a value the column cannot compare...
                probs.append(f"{r['code']}: {e}")
    return probs


def load_rules(path, df, register, run_folder):
    """The signed-off rules, checked against this data -> (rules, version); RobotError stops the run."""
    path, staging = Path(path), Path(run_folder) / f".{Path(path).name}"
    sha = file_sha256(shutil.copy2(path, staging))        # hash, read and evidence all use these bytes
    copy = staging.replace(staging.with_name(f"{path.stem}_{sha[:12]}{path.suffix}"))
    stop = lambda why, code, **ids: RobotError(why, code=code, file=path.name, sha256=sha, **ids)
    try:
        rules = rules_from_excel(copy, sheet="Rules")
    except ValueError as e:                               # unknown operator, half-filled row, missing columns
        raise stop("the rules sheet cannot be used", "BAD_RULES", problems=[str(e)]) from e
    if probs := check_rules(rules, df):
        raise stop("the rules do not fit the data", "BAD_RULES", problems=probs)
    signed = (pd.read_csv(register, dtype=str).to_dict("records") or [{}])[-1] if Path(register).exists() else {}
    if str(signed.get("sha256", "")).strip().lower() != sha:                      # the newest sign-off only
        raise stop("this rules file is not the one the owner signed off last", "RULES_NOT_SIGNED_OFF",
                   signed_version=signed.get("version"))
    return rules, {"rules_file": copy.name, "rules_sha256": sha, "rule_rows": len(rules),
                   "codes": rules["code"].nunique(), **{k: v for k, v in signed.items() if k != "sha256"}}


def flag_and_route(df, rules):
    """apply_rules flags, missing_input and the route (ESCALATE / REVIEW / AUTO) of every row."""
    out = apply_rules(df, rules)
    high = rules.loc[rules["severity"].str.strip().str.upper() == "HIGH", "code"].unique()
    out["high_flags"] = out[[f"flag_{c}" for c in high]].sum(axis=1)
    out["missing_input"] = pd.concat([condition(df, c, "is blank") for c in set(rules["column"])], axis=1).any(axis=1)
    out["route"] = first_match(out, ROUTES, default="AUTO")
    return out


def apply_signed_rules(df, rules_path, register, run_folder, key="id", log=print):
    """Load, check, flag, route and keep the evidence; the robot then works AUTO and hands over the rest."""
    rules, version = load_rules(rules_path, df, register, run_folder)
    out = flag_and_route(df, rules)
    routes = out["route"].value_counts().reindex(["ESCALATE", "REVIEW", "AUTO"], fill_value=0)
    safe_write_excel(Path(run_folder) / f"evidence_{version['rules_file']}", {
        "Rules version": pd.DataFrame([version]), "Rules": rules, "Flag summary": flag_summary(out),
        "Routes": routes.rename_axis("route").reset_index(name="rows"),
        "Items": out[[key, "flags", "missing_input", "route"]]}, overwrite=True)
    log(f"rules {version.get('version')} {version['rules_file']}: " + ", ".join(f"{r} {n}" for r, n in routes.items()))
    return out


# --- demo ---


def demo(tmp):
    """Six payments flagged and routed by a signed-off rules workbook, with the evidence an audit needs. Refused
    before any item: an unknown operator, 'thirty' days, a list '1,5, 2,5', an unclear active value, 'gt' on
    amounts read as text, an edit nobody signed off."""
    rules = pd.DataFrame([["LARGE", "amount", "gt", "1,000,000", "HIGH", "Y"],
                          ["EXOTIC_CCY", "ccy", "not in", "USD, EUR, GBP, SGD", "MEDIUM", "Y"],
                          ["NEW_CPTY_LARGE", "amount", "ge", "100000", "MEDIUM", "Y"],    # same code: both hold
                          ["NEW_CPTY_LARGE", "cpty_days", "lt", "30", "MEDIUM", "Y"],
                          ["NO_PURPOSE", "purpose", "is blank", None, "MEDIUM", "Y"],
                          ["OLD_LIMIT", "amount", "gt", "10000", "HIGH", "N"]],             # switched off
                         columns=["code", "column", "op", "value", "severity", "active"])
    book = lambda frame, name: frame.to_excel(tmp / name, sheet_name="Rules", index=False) or tmp / name
    sha, register, log = file_sha256(book(rules, "rules.xlsx")), tmp / "signoff.csv", []   # the owner's register
    register.write_text(f"sha256,version,approved_by,approved_on\n{sha.upper()} ,v7,ops.owner,2026-11-10\n")
    df = pd.DataFrame({"id": [f"P{i}" for i in range(1, 7)], "amount": [50e3, 2.5e6, 20e3, 150e3, 30e3, 80e3],
                       "ccy": ["USD", "TRY", "TRY", "USD", None, "USD"], "cpty_days": [400, 400, 400, 10, 400, 5],
                       "purpose": ["INV1", "INV2", "INV3", "INV4", "INV5", " "]})
    def run(path, data, folder):
        (tmp / folder).mkdir()
        try:
            return apply_signed_rules(data, path, register, tmp / folder, log=log.append)
        except RobotError as e:
            assert not list((tmp / folder).glob("evidence_*"))     # stopped before any item
            return e

    out, tag = run(tmp / "rules.xlsx", df, "run1").set_index("id"), sha[:12]
    assert out["route"].tolist() == ["AUTO", "ESCALATE"] + ["REVIEW"] * 4 and out["flags"]["P2"] == "LARGE;EXOTIC_CCY"
    assert out.loc["P5", ["flags", "missing_input"]].tolist() == ["", True]      # a blank ccy matched no rule
    assert "flag_OLD_LIMIT" not in out and log == [f"rules v7 rules_{tag}.xlsx: ESCALATE 1, REVIEW 4, AUTO 1"]
    ev = pd.read_excel(tmp / f"run1/evidence_rules_{tag}.xlsx", sheet_name=None)
    assert ev["Rules version"].iloc[0, :5].tolist() == [f"rules_{tag}.xlsx", sha, 5, 4, "v7"]
    assert dict(ev["Flag summary"].values) == {"EXOTIC_CCY": 2, "LARGE": 1, "NEW_CPTY_LARGE": 1, "NO_PURPOSE": 1}
    assert list(ev["Items"]) == ["id", "flags", "missing_input", "route"]               # no amounts
    e = run(book(rules.assign(op=rules["op"].replace("gt", "bigger than")), "typo.xlsx"), df, "run2")
    assert e.code == "BAD_RULES" and "LARGE: unknown operator 'bigger than'" in e.context["problems"][0]
    typos = pd.concat([rules.assign(value=rules["value"].replace("30", "thirty")),
                       pd.DataFrame([["ODD", "amount", "in", "1,5, 2,5", "MEDIUM", "Y"]], columns=rules.columns)])
    e = run(book(typos, "words.xlsx"), df, "run3")       # toolbank finds the first, the block the second
    assert e.context["problems"] == [
        "NEW_CPTY_LARGE: rule value 'thirty' is not a number, but the column holds numbers",
        "ODD: in '1,5, 2,5' a comma sits between digits: type '1.5, 2.5', or '1,000; 2,000' for thousands"]
    e = run(book(rules.assign(active=rules["active"].replace("N", "Paused")), "paused.xlsx"), df, "run6")
    assert e.code == "BAD_RULES" and "have active ['Paused']" in e.context["problems"][0]   # on or off? never guess
    e = run(tmp / "rules.xlsx", df.assign(amount=df["amount"].astype(str)), "run5")       # amounts read as text
    assert e.code == "BAD_RULES" and [p.split(":")[0] for p in e.context["problems"]] == ["LARGE", "NEW_CPTY_LARGE"]
    e = run(book(rules.assign(description="limit raised"), "rules.xlsx"), df, "run4")   # nobody signed this off
    assert e.code == "RULES_NOT_SIGNED_OFF" and e.context["signed_version"] == "v7"
