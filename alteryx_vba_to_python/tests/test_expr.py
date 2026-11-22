"""Expression translation against hand-computed results, including the semantic edge cases."""
import unittest

from helpers import evaluate as ev, rt
from ayx2py import expr


class Parser(unittest.TestCase):
    def test_tokens_and_comments(self):
        self.assertEqual(ev("1 + /* two */ 2 // three\n + 3"), 6)
        self.assertEqual(ev('"a" + \'b\''), "ab")
        self.assertEqual(ev(r"'\w' + '\n'"), "\\w\\n")                # no escape processing in literals

    def test_arithmetic_precedence(self):
        self.assertEqual(ev("2 + 3 * 4"), 14)
        self.assertEqual(ev("(2 + 3) * 4"), 20)
        self.assertEqual(ev("-2 * -3"), 6)
        self.assertEqual(ev("7 / 2"), 3.5)
        self.assertEqual(ev("7 % 4"), 3)
        self.assertEqual(ev("10 - 4 - 3"), 3)

    def test_and_or_left_to_right(self):
        # documented Alteryx rule: AND and OR have the same rank, grouped left to right
        self.assertEqual(ev("1=1 OR 1=2 AND 1=2"), False)             # (T or F) and F
        self.assertEqual(ev("1=1 OR (1=2 AND 1=2)"), True)
        self.assertEqual(ev("1=2 AND 1=2 OR 1=1"), True)              # (F and F) or T: same either way
        notes = expr.precedence_notes("[A]=1 OR [B]=2 AND [C]=3")
        self.assertTrue(notes and "left to right" in notes[0])
        self.assertEqual(expr.precedence_notes("[A]=1 OR ([B]=2 AND [C]=3)"), [])
        old = expr.AND_BINDS_TIGHTER
        try:
            expr.AND_BINDS_TIGHTER = True
            self.assertEqual(ev("1=1 OR 1=2 AND 1=2"), True)
        finally:
            expr.AND_BINDS_TIGHTER = old

    def test_not(self):
        self.assertEqual(ev("!IsEmpty([A]) AND !IsEmpty([B])", {"A": "x", "B": ""}), False)
        self.assertEqual(ev("NOT [A] > 3", {"A": 5}), False)
        self.assertEqual(ev("![A] = [B]", {"A": 0, "B": True}), True)   # (!a) = b
        self.assertTrue(expr.precedence_notes("![A] = [B]"))

    def test_if_elseif(self):
        e = "If [L]='Long' then 'BUY' elseif [L]='Short' then 'SELL' else '' endif"
        self.assertEqual([ev(e, {"L": v}) for v in ("Long", "SHORT", "x", None)], ["BUY", "SELL", "", ""])
        self.assertEqual(ev("IF 1=2 THEN 1/0 ELSE 5 ENDIF"), 5)
        self.assertEqual(ev("1 + IF [A] > 1 THEN 10 ELSE 20 ENDIF", {"A": 2}), 11)
        self.assertEqual(ev("IIF([A] > 1, 'big', 'small')", {"A": 1}), "small")
        self.assertEqual(ev("Switch([A], 'other', 'x', 'ex', 'y', 'why')", {"A": "Y"}), "why")

    def test_in(self):
        self.assertTrue(ev("[A] IN ('x','y')", {"A": "y"}))
        self.assertFalse(ev("[A] IN ('x','y')", {"A": "Y"}))          # IN is an exact match
        self.assertTrue(ev("[A] NOT IN (1, 2)", {"A": 3}))
        self.assertTrue(ev("[A] in (1, 2) and [B] = 'k'", {"A": 2.0, "B": "K"}))

    def test_errors(self):
        for bad in ("IF 1 THEN 2", "[A", "'abc", "Foo(1)", "1 +", "Left('a')", "[Row-1:A] + 1", "1 $ 2", ""):
            with self.assertRaises(expr.ExprError, msg=bad):
                expr.parse(bad)

    def test_canon_ignores_layout(self):
        a = expr.canon(expr.parse("IF [A]='x'  THEN 1\n ELSE 2 ENDIF"))
        b = expr.canon(expr.parse("if [A] = \"x\" then 1 else 2 endif"))
        self.assertEqual(a, b)
        self.assertEqual(expr.fields(expr.parse("[A]+[B]+[A]")), ["A", "B"])


class Semantics(unittest.TestCase):
    def test_string_equality_ignores_case(self):
        self.assertTrue(ev("[B]='P'", {"B": "p"}))                    # a code typed in the other case still matches
        self.assertFalse(ev("[B]!='P'", {"B": "p"}))
        self.assertTrue(ev("'abc' < 'ABD'"))
        self.assertTrue(ev("[A]=[B]", {"A": "USD Put / CHF Call", "B": "USD PUT / CHF CALL"}))

    def test_nulls(self):
        self.assertEqual(ev("[A] + 'abc'", {"A": None}), "abc")       # string + null = the string
        self.assertIsNone(ev("[A] + 1", {"A": None}))                 # number + null = null
        self.assertIsNone(ev("Null() * 2"))
        self.assertFalse(ev("[A] < 5", {"A": None}))
        self.assertFalse(ev("[A] > 5", {"A": None}))
        self.assertTrue(ev("[A] = [B]", {"A": None, "B": None}))
        self.assertTrue(ev("[A] != 5", {"A": None}))
        self.assertTrue(ev("[A] <= [B]", {"A": None, "B": None}))
        self.assertFalse(ev("[A] <= 5", {"A": None}))
        self.assertFalse(ev("[A] = ''", {"A": None}))                 # switch NULL_EQUALS_EMPTY_STRING
        self.assertTrue(ev("IsEmpty([A]) AND IsNull([A]) AND IsEmpty('') AND !IsNull('')", {"A": None}))
        self.assertEqual(ev("IF [A] THEN 1 ELSE 2 ENDIF", {"A": None}), 2)
        self.assertEqual(ev("IF [A]='x' THEN 1 ELSE 2 ENDIF", {"A": None}), 2)

    def test_type_mismatch_is_an_error_not_a_guess(self):
        with self.assertRaises(rt.AlteryxError):
            ev("[A] = 101", {"A": "101"})
        with self.assertRaises(rt.AlteryxError):
            ev("[A] + 1", {"A": "5"})
        with self.assertRaises(rt.AlteryxError):
            ev("[Missing] + 1", {"A": 1})
        self.assertEqual(ev("[deal id]", {"DEAL ID": "x"}), "x")      # field names: case-insensitive fallback

    def test_conversion(self):
        self.assertEqual(ev("ToNumber('12.5')"), 12.5)
        self.assertEqual(ev("ToNumber('123abc')"), 123)
        self.assertEqual(ev("ToNumber('abc')"), 0)
        self.assertEqual(ev("ToNumber([A])", {"A": None}), 0)
        self.assertIsNone(ev("ToNumber([A], 0, 1)", {"A": None}))
        self.assertEqual(ev("ToString(1250000.5)"), "1250000.5")
        self.assertEqual(ev("ToString(100.0)"), "100")
        self.assertEqual(ev("ToString(1234.5678, 2)"), "1234.57")
        self.assertEqual(ev("ToString(1234.5, 1, 1)"), "1,234.5")
        self.assertEqual(ev("Round(2.5, 1)"), 3)
        self.assertEqual(ev("Round(-2.5, 1)"), -3)
        self.assertEqual(ev("Round(1234.567, 0.01)"), 1234.57)
        self.assertEqual(ev("ABS(-3) + CEIL(1.2) + FLOOR(1.8)"), 6)
        self.assertIsNone(ev("1 / 0"))

    def test_strings(self):
        self.assertTrue(ev("Contains([F],'consolidated b') and !Contains([F],'.bak')", {"F": "Consolidated B 1.xlsx"}))
        self.assertFalse(ev("Contains('ABC','b', 0)"))
        self.assertEqual(ev("left([S],3)+' CALL / '+right([S],3)+' PUT'", {"S": "USDCHF"}), "USD CALL / CHF PUT")
        self.assertEqual(ev("[B]+'-'+[N]", {"B": "101", "N": "123456"}), "101-123456")
        self.assertEqual(ev("PadLeft('7', 3, '0') + Uppercase('a') + LowerCase('B') + TitleCase('ab cd')"), "007AbAb Cd")
        self.assertEqual(ev("Trim('  a b  ') + '|' + TrimLeft('xxa','x') + '|' + Substring('abcdef', 2, 3)"), "a b|a|cde")
        self.assertEqual(ev("Replace('a-b-c','-','')"), "abc")
        self.assertEqual(ev("Replace('aXa','x','')"), "aXa")           # Replace is case-sensitive
        self.assertEqual(ev("Length('abc') + FindString('abc','c')"), 5)
        self.assertEqual(ev("StartsWith('Hello','he') AND EndsWith('Hello','LO')"), True)
        self.assertIsNone(ev("Uppercase([A])", {"A": None}))

    def test_regex(self):
        self.assertEqual(ev(r"REGEX_Replace('1,250,000.00 USD',' [A-Z]{3}','')"), "1,250,000.00")
        self.assertEqual(ev(r"REGEX_Replace('abc','B','x')"), "axc")              # icase defaults to on
        self.assertEqual(ev(r"REGEX_Replace('abc','B','x', 0)"), "abc")
        self.assertEqual(ev(r"REGEX_Replace('USD PUT / CHF CALL','(\w{3}) (\w+) / (\w{3}) (\w+)','$3 $4 / $1 $2')"),
                         "CHF CALL / USD PUT")
        self.assertTrue(ev(r"REGEX_Match('AB12','[a-z]+\d+')"))
        self.assertFalse(ev(r"REGEX_Match('AB12x','[a-z]+\d+')"))                  # whole string must match
        self.assertEqual(ev(r"REGEX_CountMatches('a;b;c',';')"), 2)
        self.assertEqual(ev(r'REGEX_Replace(";;a;b","\G;","")'), "a;b")           # \G emulated at the start
        with self.assertRaises(rt.NotConverted):
            ev(r"REGEX_Replace('a','[[:alpha:]]','')")

    def test_position_swap_as_in_workflow_tool_1464(self):
        e = r"""If Contains([P],'/') then TRIM(REGEX_Replace([P],'(\w{3}\s\w*\s)/','')+' / '
                 +REGEX_Replace([P],'/(\s\w{3}\s\w*)','')) ELSE [P] ENDIF"""
        self.assertEqual(ev(e, {"P": "USD CALL / CHF PUT"}), "CHF PUT / USD CALL")
        self.assertEqual(ev(e, {"P": "855000"}), "855000")

    def test_discrepancy_cleanup_as_in_workflow_tool_2660(self):
        e = r'''REGEX_Replace(REGEX_Replace(REPLACE(REPLACE(TRIM(REGEX_Replace(REPLACE(REPLACE(
            [_CurrentField_],";;;;",";"),";;",";"),"(\bDiscrepancy in .+);(?=.*\b\1;?)", "")) ,"; ",";")," ;",";"),"\G;",""),";$","")'''
        cur = ";Discrepancy in Side;;Discrepancy in Currency;Discrepancy in Side;"
        self.assertEqual(ev(e, {}, cur=cur), "Discrepancy in Currency;Discrepancy in Side")

    def test_dates(self):
        self.assertEqual(ev("ToDate(7000)"), "1919-03-01")                         # the documented example
        self.assertEqual(ev("todate((Round(tonumber([E])/86400000,1)+25569))", {"E": "1794873600000"}), "2026-11-17")
        self.assertEqual(ev("ToDate('2020-10-31 12:00:00')"), "2020-10-31")
        self.assertEqual(ev("DateTimeDiff('2026-11-18','2026-11-17','days')"), 1)
        self.assertEqual(ev("DateTimeDiff('2016-01-01 23:59:59','2016-01-01 00:00:00','days')"), 0)
        self.assertEqual(ev("DateTimeDiff('2016-01-02 00:00:00','2016-01-01 00:00:01','days')"), 0)   # fraction dropped
        self.assertEqual(ev("DateTimeDiff('2016-02-15 00:00:00','2016-01-15 00:00:01','months')"), 1)  # time ignored
        self.assertEqual(ev("DateTimeDiff('2016-02-14','2016-01-15','months')"), 0)
        self.assertEqual(ev("DateTimeDiff('2012-02-29','2011-03-31','years')"), 0)
        self.assertEqual(ev("DateTimeAdd('2026-01-31', 1, 'months')"), "2026-02-28")
        self.assertEqual(ev("DateTimeAdd('2026-01-31 10:00:00', -2, 'hours')"), "2026-01-31 08:00:00")
        self.assertEqual(ev("DateTimeFormat('2026-11-17','%d-%b-%y')"), "17-Nov-26")
        self.assertEqual(ev("DateTimeParse('17/11/2026','%d/%m/%Y')"), "2026-11-17 00:00:00")
        self.assertIsNone(ev("DateTimeParse('31/02/2026','%d/%m/%Y')"))
        self.assertEqual(ev("DateTimeYear('2026-11-17') + DateTimeMonth('2026-11-17')"), 2037)
        e = "If TODATE([A])=TODATE([B]) then '' ELSE 'Discrepancy in Trade Date' ENDIF"
        self.assertEqual(ev(e, {"A": "2026-11-17 09:30:00", "B": "2026-11-17"}), "")
        self.assertEqual(ev(e, {"A": "2026-11-17", "B": "2026-11-18"}), "Discrepancy in Trade Date")

    def test_amount_band(self):
        e = """IF [PREMIUM CCY]=[SYSB_PREMIUM CCY] AND ABS([SYSB_PREMIUM AMOUNT])>=(ABS([PREMIUM AMOUNT])*0.9)
               AND ABS([SYSB_PREMIUM AMOUNT])<= (ABS([PREMIUM AMOUNT])*1.1) THEN '' ELSE 'Discrepancy in Premium Amount' ENDIF"""
        row = {"PREMIUM CCY": "chf", "SYSB_PREMIUM CCY": "CHF", "PREMIUM AMOUNT": 34868, "SYSB_PREMIUM AMOUNT": -36000.0}
        self.assertEqual(ev(e, row), "")
        self.assertEqual(ev(e, dict(row, **{"SYSB_PREMIUM AMOUNT": 40000})), "Discrepancy in Premium Amount")
        self.assertEqual(ev(e, dict(row, **{"SYSB_PREMIUM AMOUNT": None})), "Discrepancy in Premium Amount")

    def test_current_field_and_constants(self):
        self.assertEqual(ev("Uppercase([_CurrentField_]) + [_CurrentFieldName_]", {}, cur="ab", cur_name="X"), "ABX")
        rt.use(rt.Context(workflow_dir="/tmp/wf"))
        self.assertTrue(ev("[Engine.WorkflowDirectory] + 'a.csv'").endswith("a.csv"))


if __name__ == "__main__":
    unittest.main()
