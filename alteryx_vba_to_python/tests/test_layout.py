"""The guide's layout at the size of a real workflow (synth_layout.stress: one container of 300+ tools and 150+
branches): one story per output, shared work told once as a named working table, lookups described in the step that
uses them, no branch or tool-type headings, and no bare step numbers in the main text."""
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


def main_view(h):
    """The page's text above the Detail section, without small print (step numbers, tool IDs) and fold-outs."""
    body = h[h.index('<h2 id="does">'):h.index('<h2 id="detail">')]
    body = re.sub(r'<details class="rule">.*?</details>', "", body, flags=re.S)
    body = re.sub(r"<small[^>]*>.*?</small>", "", body, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r" ([.,)])", r"\1", re.sub(r"\s+", " ", text))


class Stress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g, cls.md, cls.html = build()
        cls.lay = layout.Layout(cls.g)

    def test_the_fixture_is_the_size_of_a_real_one(self):
        g = self.g
        big = max(g.tops, key=lambda k: sum(1 for i in g.order if g.top_of[i] == k))
        self.assertGreaterEqual(sum(1 for i in g.order if g.top_of[i] == big), 300)
        self.assertGreaterEqual(len({b for s, b in g.bid.items() if g.top_of[g.L.stages[s][0]] == big}), 150)
        self.assertEqual(len(g.off), 1)

    def test_no_branch_or_tool_type_headings(self):
        heads = re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", self.html, re.S)
        heads += re.findall(r"<summary>(.*?)</summary>", self.html, re.S)
        tools = {v.lower() for v in DESIGNER.values()}
        for head in heads:
            text = html.unescape(re.sub(r"<[^>]+>", "", head)).strip()
            self.assertFalse(re.match(r"Branch(es)? \d", text), text)
            self.assertFalse(re.search(r"— (%s)\b" % "|".join(map(re.escape, DESIGNER.values())), text), text)
            self.assertFalse(text.lower() in tools, text)
        self.assertFalse(re.search(r"^#+ Branch", self.md, re.M))

    def test_no_bare_step_numbers_in_the_main_view(self):
        text = main_view(self.html)
        self.assertEqual(re.findall(r"\b\d+\.\d+[a-z]?\b", text), [])
        self.assertFalse(re.search(r"\b(Next|Out|In):", text))
        self.assertFalse(re.search(r"\b[TFJLR] →", text))                     # Designer's anchor letters
        self.assertFalse("converted, assuming" in text or " · t" in text)       # status text stays in the fold-outs

    def test_each_output_has_exactly_one_story(self):
        g = self.g
        outputs = [i for i in g.order if g.t[i].kind == "Output"]
        stories = re.findall(r'<div class="box story" id="(o\d+)">', self.html)
        self.assertEqual(sorted(stories), sorted("o" + i for i in outputs))
        for i in outputs:
            self.assertEqual(sum(1 for grp in self.lay.groups if grp.kind == "story" and i in grp.ends), 1, i)

    def test_every_step_told_once(self):
        g = self.g
        shown = re.findall(r'<li id="s([\d-]+[a-z]*)">', self.html)
        want = [g.sid[i].replace(".", "-") for i in g.order if i not in g.fold_of]
        self.assertEqual(sorted(shown), sorted(want))
        self.assertEqual(sorted(re.findall(r"^- \*\*(\d+\.\d+)\*\* ", self.md, re.M)), sorted(g.sid[i] for i in g.order
                                                                                            if i not in g.fold_of))

    def test_shared_work_is_a_named_working_table(self):
        h = self.html
        tables = {grp.title: grp.anchor for grp in self.lay.groups if grp.kind == "table"}
        self.assertEqual(sorted(tables), ["Clean trades", "Clean trades, with Age calculated"])
        self.assertTrue('<span class="kind">working table</span> <span class="chip w">Clean trades</span>' in h)
        start = '<b>Starts from</b><span><b><a href="#%s">Clean trades</a></b></span>' % tables["Clean trades"]
        self.assertGreaterEqual(h.count(start), 50)                       # each check of the first output starts there
        self.assertTrue('<b>Starts from</b><span><b><a href="#%s">Clean trades, with Age calculated</a></b></span>'
                        % tables["Clean trades, with Age calculated"] in h)

    def test_a_lookup_and_its_preparation(self):
        h = self.html
        self.assertTrue(re.search(r'<li class="side">\s*<details class="side" id="p\d+"><summary>How Usable rates is '
                                  r'prepared \(4 steps\)</summary>', h))
        self.assertTrue(re.search(r'Matches its rows with <b><a href="#p\d+">Usable rates</a></b> on <span class="col">Ccy'
                                  r'</span>\.', h))
        self.assertTrue('Matches its rows with <span class="chip f">Config.xlsx › Clients</span> on <span class="col">'
                        'Client Code</span>.' in h)                       # a file read inside the step that uses it
        self.assertTrue("<b>matched rows</b> → " in h and "<b>rows only in the first input</b> → " in h and
                        "<b>rows only in the second input</b> → dropped" in h)

    def test_rules_in_words_where_safe(self):
        text = main_view(self.html)
        self.assertTrue('Ref = Ref with ".xlsx" removed' in text)
        self.assertTrue("Keeps only the rows where the last 2 characters of Trade ID equal Book." in text)
        self.assertTrue("Keeps only the rows where a test on Amount, Ccy, Status, Book holds (rule in the details)."
                        in text)
        self.assertEqual(layout.words(guide.expr.parse('Right([A], 2) = [B]')), "the last 2 characters of [A] equal [B]")
        self.assertEqual(layout.words(guide.expr.parse('Replace([A], ".xlsx", "")')), '[A] with ".xlsx" removed')
        self.assertIsNone(layout.words(guide.expr.parse('REGEX_Replace([A], "x", "y")')))

    def test_run_command_dead_ends_and_the_disabled_container(self):
        h = self.html
        story = h[h.index('id="o%s"' % self.out("Exceptions")):]
        story = story[:story.index('<div class="box')] if '<div class="box' in story else story
        self.assertTrue("Runs <code>cscript.exe FormatAll.vbs Exceptions.xlsx</code> outside Alteryx" in story)
        self.assertTrue('<span class="hand">outside Alteryx: write by hand</span>' in story)
        self.assertTrue("<b>Not used by any output:</b> 2 steps (Data Sampling 1, Formula 1)" in h or
                        "<b>Not used by any output:</b> 2 steps (Sample 1, Formula 1)" in h)
        self.assertTrue('Old recon.xlsx › Recon</span> <span class="badge">container 2 · disabled in the saved file'
                        '</span>' in h)
        self.assertTrue('2. Old recon</a> <span class="badge">disabled</span>' in h)

    def test_links_resolve_and_page_is_self_contained(self):
        h = self.html
        self.assertFalse(re.search(r"<script|<link|src=|href=\"http", h))
        ids = set(re.findall(r'id="([^"]+)"', h))
        for target in set(re.findall(r'href="#([^"]+)"', h)):
            self.assertTrue(target in ids, target)
        self.assertEqual(self.md, build()[1])                             # deterministic

    def out(self, name):
        return next(i for i in self.g.order if self.g.t[i].kind == "Output" and name in self.g.t[i].ir["file"])


if __name__ == "__main__":
    unittest.main()
