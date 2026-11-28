"""The guide's layout at the size of a real workflow (synth_layout.stress: one container of 300+ tools and 150+
branches). Each output is one compact table of the steps that change its rows or values, with their rules; the rest
is presumed and listed, folded, at the end of the table. Shared work is told once as Table A, B...; lookups, settings
values, repeated saves and parallel paths are told the way a person would."""
import html
import os
import re
import tempfile
import unittest

from helpers import ROOT  # noqa: F401  (puts the package on the path)
import logicdoc
import synth_layout
from ayx2py import guide, layout, workflow
from ayx2py.guide import DESIGNER


def build():
    path = os.path.join(tempfile.mkdtemp(prefix="layout_"), "daily_checks.yxmd")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(synth_layout.stress().xml())
    g = guide.Guide(workflow.Workflow(path))
    doc = g.build()
    return g, logicdoc.to_markdown(doc), logicdoc.to_html(doc, "Daily checks")


def text_of(fragment):
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\( ", "(", re.sub(r" ([.,);:])", r"\1", re.sub(r"\s+", " ", text)))


def main_view(h):
    """The page's text above the Detail section, without small print (step numbers, tool IDs) and fold-outs."""
    body = h[h.index('<h2 id="does">'):h.index('<h2 id="detail">')]
    body = re.sub(r'<details class="rule">.*?</details>', "", body, flags=re.S)
    body = re.sub(r'<details class="hk".*?</details>', "", body, flags=re.S)
    body = re.sub(r"<small[^>]*>.*?</small>", "", body, flags=re.S)
    return text_of(body)


def block(h, anchor):
    """One output's or table's block of the page."""
    start = h.index('<div class="box story" id="%s">' % anchor)
    ends = [h.find(x, start + 40) for x in ('<div class="box story"', "<h3", "<p><b>", '<h2 id="order">')]
    return h[start:min(x for x in ends if x > 0)]


def rows(fragment):
    """The action rows of one block (not the rows of a small table inside a row)."""
    body = fragment[fragment.index('<table class="acts">'):]
    body = re.sub(r'<table class="mini">.*?</table>', "", body, flags=re.S)
    return re.findall(r'<tr id="s[^"]+"[^>]*><td class="n">([^<]+)</td>(.*?)</tr>', body, re.S)


class Stress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g, cls.md, cls.html = build()
        cls.lay = layout.Layout(cls.g)
        cls.main = main_view(cls.html)

    def out(self, name):
        return next(i for i in self.g.order if self.g.t[i].kind == "Output" and name in self.g.t[i].ir["file"])

    def anchor(self, title):
        return next(grp.anchor for grp in self.lay.groups if grp.title == title)

    def test_the_fixture_is_the_size_of_a_real_one(self):
        g = self.g
        big = max(g.tops, key=lambda k: sum(1 for i in g.order if g.top_of[i] == k))
        self.assertGreaterEqual(sum(1 for i in g.order if g.top_of[i] == big), 300)
        self.assertGreaterEqual(len({b for s, b in g.bid.items() if g.top_of[g.L.stages[s][0]] == big}), 150)
        self.assertEqual(len(g.off), 1)

    def test_headings_are_names_a_person_would_use(self):
        heads = re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", self.html, re.S)
        heads += re.findall(r"<summary>(.*?)</summary>", self.html, re.S)
        heads += re.findall(r'<a href="#[^"]+">([^<]*)</a>', self.html[:self.html.index('<h2 id="does">')])
        tools = {v.lower() for v in DESIGNER.values()}
        for head in heads:
            text = text_of(head).strip()
            self.assertFalse(re.search(r"Branch(es)? \d", text), text)
            self.assertFalse(re.search(r"\(\d+\)|\+\d+ more|, prepared", text), text)
            self.assertFalse(text.lower() in tools, text)
            self.assertFalse(re.search(r"— (%s)\b" % "|".join(map(re.escape, DESIGNER.values())), text), text)
        self.assertFalse(re.search(r"^#+ Branch", self.md, re.M))

    def test_no_bare_step_numbers_and_no_jargon_in_the_main_view(self):
        text = self.main
        self.assertEqual(re.findall(r"\b\d+\.\d+[a-z]?\b", text), [])
        self.assertFalse(re.search(r"\b(Next|Out|In):", text))
        self.assertFalse(re.search(r"\b[TFJLR] →", text))                     # Designer's anchor letters
        self.assertFalse("converted, assuming" in text or "Looks at" in text)
        self.assertFalse("worked out from" in text or "rule in the details" in text)
        self.assertFalse(any("feed no output" in x for x in re.findall(r'<p class="sub">(.*?)</p>', self.html)))

    def test_each_output_is_told_in_exactly_one_block(self):
        g = self.g
        for i in (i for i in g.order if g.t[i].kind == "Output"):
            self.assertEqual(self.html.count('id="o%s"' % i), 1, i)
            self.assertEqual(sum(1 for grp in self.lay.groups if grp.kind == "story" and i in grp.ends), 1, i)
        self.assertEqual(self.html.count('<span class="chip o">Dummy.xlsx › Order</span>'), 1)

    def test_every_step_is_a_row_or_housekeeping_of_its_own_block(self):
        g, h = self.g, self.html
        shown = re.findall(r'id="s([\d-]+[a-z]*)"', h)
        self.assertEqual(sorted(shown), sorted(g.sid[i].replace(".", "-") for i in g.order if i not in g.fold_of))
        for grp in self.lay.groups:
            if grp.kind not in ("story", "table") or grp.title == "Dummy.xlsx › Order":
                continue
            here = set(re.findall(r'id="s([\d-]+[a-z]*)"', block(h, grp.anchor)))
            self.assertEqual(here, {g.sid[i].replace(".", "-") for i in grp.tools}, grp.title)
        self.assertEqual(sorted(re.findall(r"^- \*\*(\d+\.\d+)\*\* ", self.md, re.M)),
                         sorted(g.sid[i] for i in g.order if i not in g.fold_of))

    def test_no_row_for_opening_a_file_browsing_or_waiting(self):
        for grp in self.lay.groups:
            if grp.kind in ("story", "table"):
                for r in grp.rows:
                    for i in (r["tools"][:1] if r.get("stack") else r["tools"]):
                        self.assertFalse(self.g.t[i].kind in ("Input", "TextInput", "Browse", "BlockUntilDone",
                                                              "ParallelBlockUntilDone"), (grp.title, i))
        for anchor in re.findall(r'<div class="box story" id="([^"]+)">', self.html):
            for _, cells in rows(block(self.html, anchor)):
                self.assertFalse(re.search(r'<td class="tl"><small>(Input Data|Text Input|Block Until Done|Browse)',
                                           cells), cells[:200])

    def test_fifty_parallel_paths_are_one_row(self):
        part = block(self.html, "o" + self.out("Exceptions"))
        self.assertLess(len(rows(part)), 15)
        text = text_of(part)
        self.assertTrue("Stacks 50 sets of rows taken from Table A, each chosen and changed as below; each set then "
                        "keeps only the columns Key, Desk, Issue:" in text, text[:600])
        self.assertTrue('Where Amount > 100 sets Issue = "Check 0"' in text)
        part = block(self.html, "o" + self.out("Breaks.xlsx|||By desk"))
        self.assertLess(len(rows(part)), 6)
        self.assertTrue('pass: sets Result = "Break 0"; fail: sets Result = "Clean 0"' in text_of(part))
        part = block(self.html, "o" + self.out("Desk one"))
        self.assertLess(len(rows(part)), 6)
        self.assertFalse(re.search(r"Stacks the (rows|tables) of step", self.main))

    def test_shared_work_is_table_a_b(self):
        tables = [grp for grp in self.lay.groups if grp.kind == "table"]
        self.assertEqual([grp.title for grp in tables], ["Table A", "Table B"])
        self.assertTrue('<span class="kind">working table</span> <span class="chip w">Table A</span>' in self.html)
        self.assertTrue("Clean trades" in text_of(block(self.html, tables[0].anchor)))           # a person's annotation
        b = text_of(block(self.html, tables[1].anchor))
        self.assertTrue('From Table A; last, sets 1 column: Age = DateTimeDiff(DateTimeToday(), Trade Date, "days")'
                        in b, b)
        self.assertTrue('<p class="sub"><b>Starts from:</b> <b><a href="#%s">Table A</a></b>.</p>' % tables[0].anchor
                        in self.html)

    def test_a_lookup_and_its_preparation(self):
        text = text_of(block(self.html, "o" + self.out("In USD")))
        self.assertTrue("To prepare the lookup from Rates.xlsx › Rates: Sets 1 column: Ccy = Currency in capitals" in text,
                        text)
        self.assertTrue("Matches the rows with Rates.xlsx › Rates, prepared in rows 1a–1b (only rows where Rate is not "
                        "empty) on Ccy." in text, text)
        self.assertTrue("matched rows → row 2 · rows only in the first input → row 3 · rows only in the second input → "
                        "dropped" in text)
        a = text_of(block(self.html, self.anchor("Table A")))
        self.assertTrue("Keeps the rows that have a match in Config.xlsx › Clients on Client Code, and adds its columns."
                        in a, a)                                           # a file read inside the step that uses it

    def test_settings_formulas_in_a_row_and_rules_in_words(self):
        part = block(self.html, "o" + self.out("Settings check"))
        text = text_of(part)
        self.assertTrue("Keeps only the rows where the last 2 characters of Book equal the setting T-1 Sheet "
                        "(Settings.xlsx › Settings)." in text, text)
        self.assertTrue('Sets 3 columns: Day = "T-1" Label = Desk + " " + Day Size = "Large" when Amount > 1000, '
                        'otherwise "Small"' in text, text)
        self.assertTrue("Append Fields #" in part)                         # in the housekeeping list
        self.assertTrue('Ref = Ref with ".xlsx" removed' in self.main)
        self.assertTrue('IF [Amount] > 106 AND [Ccy] = "USD" OR [Status] = "Hold" THEN True ELSE [Book] = "B" ENDIF'
                        in self.main)                                      # not simple: the rule as written
        self.assertEqual(layout.words(guide.expr.parse('Right([A], 2) = [B]')), "the last 2 characters of [A] equal [B]")
        self.assertEqual(layout.words(guide.expr.parse('Replace([A], ".xlsx", "")')), '[A] with ".xlsx" removed')
        self.assertIsNone(layout.words(guide.expr.parse('REGEX_Replace([A], "x", "y")')))

    def test_ten_saves_to_one_sheet_are_one_block(self):
        part = block(self.html, "o" + self.out("Dummy"))
        self.assertTrue("Saved 10 times in this container. Each save replaces the last, so all but the last are most "
                        "likely there only to control the run order." in text_of(part))
        self.assertEqual(len(rows(part)), 10)

    def test_a_run_command_that_runs_the_text_it_was_given(self):
        text = text_of(block(self.html, self.anchor("Runs run_import.bat")))
        self.assertTrue("Writes the text in Script to run_import.bat and runs it outside Alteryx." in text, text)
        self.assertTrue('Makes one row: Script = all Script joined with "\\n"' in text, text)
        self.assertFalse("Runs `` " in self.md or "Runs outside" in self.main)
        story = text_of(block(self.html, "o" + self.out("Exceptions")))
        self.assertTrue("Runs cscript.exe FormatAll.vbs Exceptions.xlsx outside Alteryx." in story, story[-800:])

    def test_a_folder_of_files_is_one_source(self):
        text = text_of(block(self.html, "o" + self.out("Daily")))
        self.assertTrue("Starts from: every file in Input\\Daily named *.csv, each read like template.csv." in text, text)

    def test_dead_ends_and_the_disabled_container(self):
        h = self.html
        self.assertTrue("<b>Not used by any output:</b> 2 steps (Sample 1, Formula 1)" in h)
        self.assertTrue('Old recon.xlsx › Recon</span> <span class="badge">container 2 · disabled in the saved file'
                        '</span>' in h)
        self.assertTrue('2. Old recon</a> <span class="badge">disabled</span>' in h)

    def test_links_resolve_and_page_is_self_contained(self):
        h = self.html
        self.assertFalse(re.search(r"<script|<link|src=|href=\"http", h))
        ids = re.findall(r'id="([^"]+)"', h)
        self.assertEqual(len(ids), len(set(ids)))
        for target in set(re.findall(r'href="#([^"]+)"', h)):
            self.assertTrue(target in ids, target)
        self.assertEqual(self.md, build()[1])                             # deterministic


if __name__ == "__main__":
    unittest.main()
