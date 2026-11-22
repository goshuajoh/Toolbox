"""Builds probe/semantics_probe.yxmd and probe/semantics_probe_expected.txt.

The probe is a small workflow that exercises every Alteryx behaviour the converter had to assume.
Run it in Designer and compare each Browse with the expected file, which holds what ayx_runtime
produces. A difference names the switch in ayx_runtime.SEM (or the parser option) to change.

    python probe/make_probe.py            (re-creates both files; they are also checked by the tests)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "tests"), os.path.join(HERE, "..")]

from helpers import convert, rt, table      # noqa: E402
from yx import WB                           # noqa: E402

S, D, I, B = "V_WString", "Double", "Int64", "Bool"
FORMULAS = [   # field, expression, type, what a different answer means
    ("P01", '"P" = "p"', B, "documented: string = ignores case (STRING_EQ_IGNORES_CASE)"),
    ("P02", '"abc" < "ABD"', B, "documented: < > ignore case (STRING_ORDER_IGNORES_CASE)"),
    ("P03", '"a" IN ("A","b")', B, "IN_IGNORES_CASE"),
    ("P04", 'IsNull([NS])', B, "is an empty Text Input cell null? (if False, P05-P06 test an empty string instead)"),
    ("P05", '[NS] = ""', B, "NULL_EQUALS_EMPTY_STRING"),
    ("P06", '[NS] + "abc"', S, "documented: null + string = the string (NULL_CONCAT_AS_EMPTY)"),
    ("P07", 'ToString(Null() + 1)', S, "documented: null + number = null"),
    ("P08", '1 = 1 OR 1 = 2 AND 1 = 2', B, "documented: AND/OR left to right. True here means AND binds tighter: use --and-before-or"),
    ("P09", 'ToString(1/3)', S, "NUMBER_TO_STRING_DIGITS"),
    ("P10", 'ToString(1234567.891)', S, "NUMBER_TO_STRING_DIGITS"),
    ("P11", 'ToString(100.0)', S, "number to text drops a trailing .0"),
    ("P12", 'ToString(0.1+0.2)', S, "NUMBER_TO_STRING_DIGITS"),
    ("P13", 'ToNumber("123abc")', D, "documented by example: leading number is used"),
    ("P14", 'ToNumber("abc")', D, "documented: non-numeric text gives 0"),
    ("P15", 'ToString(7 / 0)', S, "DIV_BY_ZERO_IS_NULL"),
    ("P16", 'DateTimeDiff("2026-01-02 00:00:00","2026-01-01 00:00:01","days")', D, "DATETIMEDIFF_DAYS_IGNORE_TIME (ours: fraction dropped)"),
    ("P17", 'DateTimeDiff("2026-02-15 00:00:00","2026-01-15 00:00:01","months")', D, "documented: months ignore the time of day"),
    ("P18", 'ToString(ToDate(46343))', S, "ToDate(number) is an Excel serial day"),
    ("P19", 'REGEX_Replace("abc","B","x")', S, "REGEX_ICASE_DEFAULT"),
    ("P20", 'REGEX_Replace(";;a;b","\\G;","")', S, "\\G is emulated: only matches at the start / after the previous match"),
    ("P21", 'Round(2.5, 1)', D, "Round: halves away from zero"),
    ("P22", 'Round(-2.5, 1)', D, "Round: halves away from zero"),
    ("P23", 'Length("\\n")', D, "string literals have no escape sequences (2 = backslash + n)"),
    ("P24", 'Contains("ABC","b")', B, "documented: Contains ignores case by default"),
    ("P25", 'FindString("ABC","b")', D, "FindString is case-sensitive (-1 = not found)"),
    ("P26", 'Replace("aXa","x","")', S, "Replace is case-sensitive"),
    ("P27", 'DateTimeAdd("2026-01-31", 1, "months")', S, "month end is clipped; a date stays a date"),
    ("P28", 'DateTimeParse("17/11/2026","%d/%m/%Y")', S, "DateTimeParse returns a date-time"),
    ("P29", 'ToString(1250000.5, 2, 1)', S, "ToString with decimals and thousands separator"),
    ("P30", 'Uppercase([NS])', S, "string functions on null give null"),
]


def build():
    wb = WB()
    wb.text_input(1, ["ID", "NS"], [["1", None]], {"ID": I, "NS": S})
    wb.formula(2, [(f, e, t) for f, e, t, _ in FORMULAS])
    wb.conn(1, 2)
    flows = {"2": "Formula: one field per assumption (see the list below)"}

    def flow(tid, title):
        wb.browse(tid + 900)
        wb.conn(tid, tid + 900) if not isinstance(title, tuple) else [wb.conn(tid, tid + 900 + k, sa=a) for k, a in enumerate(title[1])]
        flows[str(tid)] = title

    wb.text_input(10, ["K", "V"], [["a", "1.5"]], {"K": S, "V": D})
    wb.text_input(11, ["V", "K"], [["x", "b"]], {"K": S, "V": S})
    wb.text_input(12, ["V"], [["100"]], {"V": I})
    wb.union(13)
    for n, t in enumerate((10, 11, 12), 1):
        wb.conn(t, 13, name="#%d" % n)
    flow(13, "Union by name of Double + String + Int64 in [V]: resulting type and text (Union type rule), order of the inputs")
    wb.text_input(20, ["K", "L"], [["b", "1"], ["a", "2"], [None, "3"], ["A", "4"]], {"K": S, "L": I})
    wb.text_input(21, ["K", "R"], [["a", "10"], [None, "20"], ["c", "30"]], {"K": S, "R": I})
    wb.join(22, ["K"], ["K"])
    wb.conn(20, 22, da="Left"); wb.conn(21, 22, da="Right")
    for k, a in enumerate(("Left", "Join", "Right")):
        wb.browse(922 + k * 100)
        wb.conn(22, 922 + k * 100, sa=a)
    flows["22"] = "Join on [K]: does null match null (JOIN_NULLS_MATCH), is 'A' = 'a' (no), row order of L / J / R (JOIN_OUTPUT_SORTED)"
    wb.text_input(30, ["G", "V", "T"], [["b", "2", "x"], ["a", None, None], ["b", "4", "z"], ["B", None, "q"]], {"G": S, "V": D, "T": S})
    wb.summarize(31, [("G", "GroupBy", "G"), ("G", "Count", "Count"), ("V", "Sum", "Sum_V"), ("V", "CountNonNull", "NonNull"),
                      ("T", "Concat", "Concat_T", ";"), ("T", "Last", "Last_T")])
    wb.conn(30, 31)
    flow(31, "Summarize: order of the groups (SUMMARIZE_OUTPUT_SORTED), Sum of only nulls (SUM_OF_NO_VALUES), Concat with a null (CONCAT_KEEPS_EMPTY)")
    wb.text_input(40, ["DEAL"], [["1234"], ["99"], ["x123"]], {"DEAL": S})
    wb.text_input(41, ["DEAL", "N"], [["123", "1"], ["1234", "2"], ["99", "3"]], {"DEAL": S, "N": I})
    wb.find_replace(42, "DEAL", "DEAL", "FindAny", ["N"])
    wb.conn(40, 42, da="Targets"); wb.conn(41, 42, da="Source")
    flow(42, "Find Replace, any part of field, append [N]: which Source row wins for '1234' (ours: the first in Source order, N=1)")
    wb.text_input(50, ["K"], [["b"], [None], ["B"], ["a"]], {"K": S})
    wb.sort(51, [("K", "Ascending")])
    wb.conn(50, 51)
    flow(51, "Sort ascending: place of null (NULL_SORTS_FIRST) and of upper/lower case (ours: code-point order, B before a)")
    wb.text_input(60, ["K"], [["b"], ["a"], ["b"], ["A"]], {"K": S})
    wb.unique(61, ["K"])
    wb.conn(60, 61)
    wb.browse(961); wb.conn(61, 961, sa="Unique")
    flows["61"] = "Unique on [K]: order of the output (UNIQUE_OUTPUT_SORTED) and case ('A' is not 'a')"
    wb.record_id(70, "RecordID", 1, "String", 6)
    wb.conn(60, 70)
    flow(70, "Record ID as String(6): leading zeros (documented)")
    wb.text_input(80, ["E"], [["17/11/2026"]], {"E": S})
    wb.datetime(81, "E", "dd/MM/yyyy", "OUT")
    wb.conn(80, 81)
    flow(81, "DateTime tool, format dd/MM/yyyy: value and TYPE of [OUT] (DATETIME_TOOL_DATE_ONLY_GIVES_DATE; ours: Date)")
    return wb, flows


def show(v):
    return "[Null]" if v is None else repr(v) if isinstance(v, str) else str(v)


def expected(wb, flows):
    wf, mod, _, _ = convert(wb)
    s = mod.run(rt.Context())
    out = ["SEMANTICS PROBE: what the converter's runtime produces. Compare with the Browse tools in Designer.",
           "Not yet opened in Designer: if the .yxmd does not load, paste the expressions below into a Formula tool by hand.",
           "", "Formula tool #2 (field, expression, our result, what a different answer means):"]
    row = rt.rows_of(s["2"])[0]
    for f, e, t, why in FORMULAS:
        out.append("  %s  %-72s -> %-26s %s" % (f, e, show(row[f]), why))
    for tid, title in flows.items():
        if tid == "2":
            continue
        out += ["", "Tool #%s: %s" % (tid, title)]
        res = s[tid]
        for anchor, df in (res.items() if isinstance(res, dict) else [("Output", res)]):
            out.append("  %s  fields: %s" % (anchor, ", ".join("%s [%s]" % kv for kv in rt.types_of(df).items())))
            out += ["    " + " | ".join(show(v) for v in r) for r in table(df)] or ["    (no rows)"]
    return out


def main(write=True):
    wb, flows = build()
    files = {"semantics_probe.yxmd": wb.xml(), "semantics_probe_expected.txt": "\n".join(expected(wb, flows)) + "\n"}
    if write:
        for name, text in files.items():
            with open(os.path.join(HERE, name), "w", encoding="utf-8") as fh:
                fh.write(text)
    return files


if __name__ == "__main__":
    for name, text in main().items():
        print("wrote probe/%s (%d characters)" % (name, len(text)))
