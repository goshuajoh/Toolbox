"""A small document model written out as Markdown (readable as plain text) and as one self-contained
HTML page (no scripts, no network), plus a layered flow diagram drawn as SVG. Standard library only.

Blocks:
    ("h", level, text, anchor)          heading
    ("p", text)                         paragraph
    ("steps", items, ordered)           list; item = {"text", "pre": [lines], "children": [items]}
    ("table", header, rows[, anchors])  table; cells are text; anchors: one id (or None) per row, HTML only
    ("pre", lines)                      preformatted lines
    ("diagram", nodes, edges)           drawn in HTML only; see flow_svg()
    ("details", level, summary, anchor, blocks, open)
                                        Markdown: a heading and its blocks; HTML: a collapsible section
    ("card", anchor, title, fields)     a step; fields = [(label, text, [preformatted lines])]
    ("folds", items)                    small closed sections; item = {"anchor", "summary", "lines"}
                                        Markdown: a list item and its lines as code; HTML: one <details> each
    ("nav", items)                      contents list (items as for "steps")
    ("box", anchor, title, badge, paragraphs, meta)
                                        a summary card; meta = [(label, text or [lines])]
    ("acts", anchor, kind, title, badge, intro, rows, housekeeping, extra anchors)
                                        a table of actions; kind: output / table; intro: lines
    ("hk", housekeeping)                a folded list: {"anchor", "title", "items", "anchors", "folds"}
A row is {"num", "anchor", "anchors", "text", "lines", "table": {"head", "rows"}, "cols", "small", "note", "hand",
"split", "folds", "sub"}; folds as in "folds".
Inline text may use **bold**, `code`, [[anchor|label]] links, and chips: {{f|file}}, {{o|saved file}}, {{s|small}},
{{b|badge}}. In HTML a [Column] outside code is drawn as a column chip.
"""
import html
import re

_LINK = re.compile(r"\[\[([\w-]+)\|(.+?)\]\](?!\])")
_CHIP = re.compile(r"\{\{([fosb])\|(.+?)\}\}")


# ----------------------------------------------------------------------------- Markdown
def _md_inline(text):
    text = _CHIP.sub(lambda m: {"f": "`%s`", "o": "`%s`", "s": "(%s)", "b": "_(%s)_"}[m.group(1)] % m.group(2), text)
    text = _LINK.sub(lambda m: m.group(2), text)
    parts = re.split(r"(`[^`]*`)", text)                  # leave `code` alone
    return "".join(x if x.startswith("`") else re.sub(r"(?<![\w\\*])\*(?![*\w])", r"\\*", x) for x in parts)


def _md_steps(items, ordered, indent=""):
    out = []
    for n, it in enumerate(items, 1):
        marker = "%d. " % n if ordered else "- "
        out.append(indent + marker + _md_inline(it["text"]))
        inner = indent + " " * len(marker)
        if it.get("pre"):
            out.append("")
            out += [inner + "    " + line for line in it["pre"]]
            out.append("")
        if it.get("children"):
            out += _md_steps(it["children"], it.get("ordered", False), inner)
    return out


def _md_blocks(blocks):
    out = []
    for b in blocks:
        k = b[0]
        if k == "h":
            out += ["", "#" * b[1] + " " + _md_inline(b[2]), ""]
        elif k == "p":
            out += [_md_inline(b[1]), ""]
        elif k == "steps":
            out += _md_steps(b[1], b[2]) + [""]
        elif k == "table":
            cell = lambda s: _md_inline(str(s)).replace("|", "\\|").replace("\n", " ")
            out.append("| " + " | ".join(cell(h) for h in b[1]) + " |")
            out.append("|" + "|".join("---" for _ in b[1]) + "|")
            out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in b[2]]
            out.append("")
        elif k == "folds":
            for it in b[1]:
                out.append("- " + _md_inline(it["summary"]))
                if it.get("lines"):
                    out += [""] + ["      " + line for line in it["lines"]] + [""]
            out.append("")
        elif k == "pre":
            out += ["    " + line for line in b[1]] + [""]
        elif k == "details":
            out += ["", "#" * b[1] + " " + _md_inline(b[2]), ""] + _md_blocks(b[4])
        elif k == "nav":
            out += _md_steps(b[1], False) + [""]
        elif k == "box":
            out += ["", "### " + _md_inline(b[2]) + (" _(%s)_" % b[3] if b[3] else ""), ""]
            out += [_md_inline(x) + "\n" for x in b[4]]
            for label, text in b[5]:
                out.append("- **%s:** %s" % (label, _md_inline("; ".join(text) if isinstance(text, list) else text)))
            out.append("")
        elif k == "acts":
            out += _md_acts(b)
        elif k == "hk":
            out += _md_hk(b[1])
        elif k == "card":
            out += ["", "#" * 5 + " " + _md_inline(b[2]), ""]
            for label, text, pre in b[3]:
                out.append("- **%s:** %s" % (label, _md_inline(text)) if label else "- " + _md_inline(text))
                if pre:
                    out += [""] + ["      " + line for line in pre] + [""]
            out.append("")
    return out


def _md_cell(text):
    return _md_inline(text).replace("|", "\\|").replace("\n", " ")


def _md_acts(b):
    out = ["", "#### " + ("Working table: " if b[2] == "table" else "") + _md_inline(b[3]) +
           (" _(%s)_" % b[4] if b[4] else ""), ""]
    out += [_md_inline(x) + "\n" for x in b[5] if x]
    out += ["| # | What happens | Columns | Tool |", "|---|---|---|---|"]
    folds = []
    for r in b[6]:
        cell = [r["text"]] + list(r.get("lines") or [])
        if r.get("table"):
            cell += ["• %s — %s %s" % tuple(row) for row in r["table"]["rows"]]
        if r.get("split"):
            cell.append("↳ " + r["split"])
        out.append("| %s | %s | %s | %s |" % (r["num"], "<br>".join(_md_cell(x) for x in cell), _md_cell(r.get("cols") or ""),
                                              _md_cell(r["small"] + (" “%s”" % r["note"] if r.get("note") else "") +
                                                       (" ⚠ " + r["hand"] if r.get("hand") else ""))))
        folds += r.get("folds") or []
    out.append("")
    if b[7]:
        out += ["_%s:_ %s" % (b[7]["title"], "; ".join(_md_inline(x) for x in b[7]["items"])), ""]
        folds += b[7]["folds"]
    if folds:
        out += ["Rule and pandas:", ""] + _md_folds(folds)
    return out


def _md_hk(h):
    out = ["_%s:_" % h["title"], ""] + ["- " + _md_inline(x) for x in h["items"]] + [""]
    return out + ["Rule and pandas:", ""] + _md_folds(h["folds"])


def _md_folds(items):
    out = []
    for it in items:
        out.append("- " + _md_inline(it["summary"]))
        if it.get("lines"):
            out += [""] + ["      " + line for line in it["lines"]] + [""]
    return out + [""]


def to_markdown(blocks):
    text = "\n".join(_md_blocks(blocks)).strip("\n") + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


# ----------------------------------------------------------------------------- HTML
_CHIP_CLASS = {"f": '<span class="chip f">%s</span>', "o": '<span class="chip o">%s</span>',
               "s": '<small class="sid">%s</small>', "b": '<span class="badge">%s</span>'}


def _h_inline(text):
    s = html.escape(text, quote=False)
    s = _LINK.sub(lambda m: '<a href="#%s">%s</a>' % (m.group(1), m.group(2)), s)
    s = _CHIP.sub(lambda m: _CHIP_CLASS[m.group(1)] % m.group(2), s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    parts = re.split(r"`([^`]+)`", s)
    return "".join("<code>%s</code>" % x if n % 2 else re.sub(r"\[([^\[\]<>]{1,80})\]", r'<span class="col">\1</span>', x)
                   for n, x in enumerate(parts))


def _h_steps(items, ordered):
    tag = "ol" if ordered else "ul"
    out = ["<%s>" % tag]
    for it in items:
        out.append(("<li id=\"%s\">" % it["anchor"] if it.get("anchor") else "<li>") + _h_inline(it["text"]))
        if it.get("pre"):
            out.append('<pre class="rule">%s</pre>' % html.escape("\n".join(it["pre"]), quote=False))
        if it.get("children"):
            out += _h_steps(it["children"], it.get("ordered", False))
        out.append("</li>")
    return out + ["</%s>" % tag]


CSS = """
:root{--bg:#fbfbfc;--ink:#1c1d21;--muted:#676973;--line:#dfe1e6;--card:#fff;--accent:#1f5fbf;--warn:#9a3412;--box:#f1f1f4;
--file:#e9f2ff;--filefg:#134a9c;--out:#e6f6ec;--outfg:#17643a;--tab:#f3ecfb;--tabfg:#5b2d91;--hand:#fff4dc;--handfg:#8a5a00;
--off:#f4e9ea;--offfg:#8c2f39;--soft:#fbfbfc}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#131417;--ink:#ecedf1;--muted:#9c9fab;--line:#2b2d34;
--card:#1b1c21;--accent:#7fb0ff;--warn:#f0883e;--box:#26272e;--file:#172a47;--filefg:#a9cbff;--out:#16301f;--outfg:#8fd9aa;
--tab:#2b2140;--tabfg:#cdb4f5;--hand:#3a2e12;--handfg:#f0c36a;--off:#3a1f23;--offfg:#f0a3ab;--soft:#131417}}
:root[data-theme="dark"]{--bg:#131417;--ink:#ecedf1;--muted:#9c9fab;--line:#2b2d34;--card:#1b1c21;--accent:#7fb0ff;
--warn:#f0883e;--box:#26272e;--file:#172a47;--filefg:#a9cbff;--out:#16301f;--outfg:#8fd9aa;--tab:#2b2140;--tabfg:#cdb4f5;
--hand:#3a2e12;--handfg:#f0c36a;--off:#3a1f23;--offfg:#f0a3ab;--soft:#131417}
*{box-sizing:border-box}
body{font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--bg);margin:0}
main{max-width:980px;margin:0 auto;padding:20px 16px 80px}
h1{font-size:1.5em;margin:.2em 0 .3em}h2{font-size:1.25em;margin:2em 0 .5em;padding-bottom:.25em;border-bottom:1px solid var(--line)}
h3{font-size:1.05em;margin:1.4em 0 .4em}h4{font-size:1em;margin:1.2em 0 .3em;color:var(--muted)}
p{margin:.4em 0 .8em}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
code{font:12.5px ui-monospace,Menlo,Consolas,monospace;background:var(--box);padding:1px 4px;border-radius:4px;
overflow-wrap:anywhere}
pre{font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace;background:var(--box);padding:8px 10px;border-radius:6px;
overflow-x:auto;margin:.4em 0 .7em}li{margin:.25em 0}li>pre.rule{margin-top:.3em}
table{border-collapse:collapse;margin:.5em 0 1.2em;font-size:13.5px;width:100%;display:block;overflow-x:auto}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12.5px}
.chip{display:inline-block;border-radius:6px;padding:0 7px;font-size:13px;overflow-wrap:anywhere}
.chip.f{background:var(--file);color:var(--filefg)}.chip.o{background:var(--out);color:var(--outfg)}
.chip.w{background:var(--tab);color:var(--tabfg)}
.col{background:var(--box);border-radius:4px;padding:0 5px;font-size:13px}
.badge{font-size:11.5px;font-weight:650;border-radius:999px;padding:1px 8px;background:var(--off);color:var(--offfg);
white-space:nowrap}
.hand{background:var(--hand);color:var(--handfg);border-radius:6px;padding:1px 7px;font-size:12px;font-weight:600}
small.sid,.sub{color:var(--muted)}small.sid{font-size:11.5px}
.kind{font-size:11.5px;font-weight:650;color:var(--tabfg);text-transform:uppercase;letter-spacing:.03em}
nav.toc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 14px;margin:12px 0}
nav.toc ul{margin:.2em 0;padding-left:1.3em}nav.toc li{margin:.1em 0}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0;
scroll-margin-top:12px}.box>h3{margin:0 0 6px}
.meta{font-size:13px;color:var(--muted);display:grid;grid-template-columns:90px 1fr;gap:2px 8px;margin:4px 0}
.meta b{font-weight:600}.meta span{color:var(--ink);min-width:0;overflow-wrap:anywhere}
ol.steps{list-style:none;padding:0;margin:8px 0 0;counter-reset:s}
ol.steps>li{counter-increment:s;display:grid;grid-template-columns:30px 1fr;gap:10px;padding:10px 0;
border-top:1px solid var(--line);margin:0;scroll-margin-top:12px}
ol.steps>li::before{content:counter(s);width:26px;height:26px;border-radius:50%;background:var(--box);display:grid;
place-items:center;font-weight:650;font-size:13px}
ol.steps>li.side{counter-increment:none;display:block;padding:6px 0 6px 40px}ol.steps>li.side::before{content:none}
.tool{font-size:12px;font-weight:650;color:var(--accent);text-transform:uppercase;letter-spacing:.03em}
.tool small,.tool i{color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0}
.does{margin:1px 0 4px;overflow-wrap:anywhere}.split{font-size:13px;margin-top:4px;overflow-wrap:anywhere}
details{margin:.5em 0}details>summary{cursor:pointer;color:var(--accent)}
details.rule{margin:6px 0 0;font-size:13px}details.rule .fs{color:var(--muted);font-size:12.5px;margin:4px 0}
details.side{background:var(--soft);border-left:3px solid var(--tabfg);border-radius:6px;padding:4px 10px;margin:4px 0}
details.side>summary{font-size:13.5px;font-weight:600}
details.d2>summary .h2{font-size:1.1em;font-weight:650;color:var(--ink)}details.d3>summary .h3{font-weight:600;color:var(--ink)}
details>.inner{padding:4px 0 4px 14px;border-left:2px solid var(--line);margin-left:6px}
section.stage{border-left:3px solid var(--line);padding:0 0 0 14px;margin:1em 0}
.diagram{overflow:auto;border:1px solid var(--line);border-radius:6px;margin:.6em 0 1.2em}.diagram svg{display:block}
.warn{color:var(--warn)}
.card{border:1px solid var(--line);border-radius:8px;margin:.7em 0;background:var(--card);scroll-margin-top:12px}
.card-h{font-weight:600;padding:6px 10px;border-bottom:1px solid var(--line);background:var(--box);border-radius:8px 8px 0 0}
.card dl{display:grid;grid-template-columns:7.5em 1fr;gap:3px 10px;margin:0;padding:8px 10px}
.card dt{color:var(--muted);font-size:13px;padding-top:2px}.card dd{margin:0;min-width:0}
.card pre.rule{margin:.2em 0}.card dd code{white-space:pre-wrap;word-break:break-word}
:target{outline:2px solid var(--accent);outline-offset:2px}tr:target{background:var(--box)}
details.fold{margin:.15em 0}details.fold>summary{padding:2px 6px;font-size:14px}
details.fold>pre{margin:.2em 0 .6em 1.2em}div.fold{padding:2px 6px 2px 22px;font-size:14px}
table.acts{display:table;width:100%;margin:8px 0}table.acts td{border-bottom:1px solid var(--line)}
table.acts td.n{font-weight:650;color:var(--muted);width:2.2em;white-space:nowrap}
table.acts td.cols{width:16%;font-size:12.5px}table.acts td.tl{width:15%;font-size:12px;color:var(--muted)}
table.acts tr.sub td{background:var(--soft);font-size:13.5px}table.acts tr.sub td.n{padding-left:16px}
table.acts .ln{margin:1px 0 1px 10px;overflow-wrap:anywhere}table.acts .does{overflow-wrap:anywhere}
table.mini{display:table;width:100%;font-size:12.5px;margin:4px 0}table.mini td:first-child{width:48%}table.mini td:last-child{width:5.5em;color:var(--muted)}table.mini td,table.mini th{padding:3px 6px}
details.hk{font-size:13px;margin:6px 0 0}details.hk ul{margin:4px 0;padding-left:1.2em}
@media (max-width:600px){.meta{grid-template-columns:1fr}table.acts,table.acts tbody,table.acts tr,table.acts td{display:block;
width:auto!important}table.acts thead{display:none}table.acts tr{border-top:1px solid var(--line);padding:6px 0}
table.acts td{border:0;padding:2px 4px}table.mini{display:block;overflow-x:auto}}
"""


def flow_svg(nodes, edges, box_w=230, box_h=42, gap_x=26, gap_y=46):
    """Top-to-bottom layered drawing. nodes: [{"id", "label", "sub", "kind", "href"}];
    kind: input / output / stage / repeat / stub / other. edges: [(from id, to id)]."""
    ids = [n["id"] for n in nodes]
    succ = {i: [] for i in ids}
    pred = {i: [] for i in ids}
    for a, b in edges:
        if a in succ and b in succ and b not in succ[a]:
            succ[a].append(b)
            pred[b].append(a)
    layer = {}
    for i in ids:                                    # nodes come in dependency order
        layer[i] = max([layer[p] + 1 for p in pred[i] if p in layer] or [0])
    rows = {}
    for i in ids:
        rows.setdefault(layer[i], []).append(i)
    pos = {}
    for _ in range(3):                               # order each row by the average place of its parents
        for L in sorted(rows):
            for k, i in enumerate(rows[L]):
                pos[i] = k
            if L:
                rows[L].sort(key=lambda i: (sum(pos.get(p, 0) for p in pred[i]) / len(pred[i]) if pred[i] else pos[i]))
        for k_row in rows.values():
            for k, i in enumerate(k_row):
                pos[i] = k
    widest = max(len(r) for r in rows.values())
    width = widest * (box_w + gap_x) + gap_x
    xy = {}
    for L, row in rows.items():
        offset = (width - len(row) * (box_w + gap_x) + gap_x) / 2
        for k, i in enumerate(row):
            xy[i] = (offset + k * (box_w + gap_x), 16 + L * (box_h + gap_y))
    height = 16 + (max(rows) + 1) * (box_h + gap_y)
    fill = {"input": "#dff3e4", "output": "#fde8d7", "repeat": "#ece6fb", "stub": "#fbdada", "stage": "#e3eefb"}
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="Segoe UI,Roboto,sans-serif">'
           % (width, height),
           '<defs><marker id="ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">'
           '<path d="M0,0 L8,4 L0,8 z" fill="#8c959f"/></marker></defs>']
    for a in ids:
        for b in succ[a]:
            x1, y1 = xy[a][0] + box_w / 2, xy[a][1] + box_h
            x2, y2 = xy[b][0] + box_w / 2, xy[b][1]
            mid = (y1 + y2) / 2
            out.append('<path d="M%.0f,%.0f C%.0f,%.0f %.0f,%.0f %.0f,%.0f" fill="none" stroke="#8c959f" '
                       'stroke-width="1.2" marker-end="url(#ah)"/>' % (x1, y1, x1, mid, x2, mid, x2, y2 - 2))
    for n in nodes:
        x, y = xy[n["id"]]
        label = n["label"] if len(n["label"]) <= 36 else n["label"][:35] + "…"
        sub = n.get("sub", "")
        sub = sub if len(sub) <= 40 else sub[:39] + "…"
        body = ('<rect x="%.0f" y="%.0f" width="%d" height="%d" rx="7" fill="%s" stroke="#8c959f"/>'
                '<text x="%.0f" y="%.0f" font-size="12.5" font-weight="600" fill="#1f2328">%s</text>'
                '<text x="%.0f" y="%.0f" font-size="11.5" fill="#424a53">%s</text><title>%s</title>') % (
            x, y, box_w, box_h, fill.get(n.get("kind"), "#eeeeee"), x + 8, y + 17, html.escape(label), x + 8, y + 33,
            html.escape(sub), html.escape(n["label"] + (" — " + n.get("sub", "") if n.get("sub") else "")))
        out.append('<a href="#%s">%s</a>' % (n["href"], body) if n.get("href") else body)
    return "\n".join(out + ["</svg>"])


def _h_blocks(blocks):
    out, open_section = [], False
    for b in blocks:
        k = b[0]
        if k == "h":
            if open_section and b[1] <= 3:
                out.append("</section>")
                open_section = False
            anchor = ' id="%s"' % b[3] if len(b) > 3 and b[3] else ""
            if b[1] == 3:
                out.append('<section class="stage"%s>' % anchor)
                anchor = ""
                open_section = True
            out.append("<h%d%s>%s</h%d>" % (b[1], anchor, _h_inline(b[2]), b[1]))
        elif k == "p":
            out.append("<p>%s</p>" % _h_inline(b[1]))
        elif k == "steps":
            out += _h_steps(b[1], b[2])
        elif k == "table":
            out.append("<table><thead><tr>%s</tr></thead><tbody>" % "".join("<th>%s</th>" % _h_inline(str(h)) for h in b[1]))
            anchors = b[3] if len(b) > 3 else [None] * len(b[2])
            out += ["<tr%s>%s</tr>" % (' id="%s"' % a if a else "", "".join("<td>%s</td>" % _h_inline(str(c)) for c in row))
                    for row, a in zip(b[2], anchors)]
            out.append("</tbody></table>")
        elif k == "folds":
            for it in b[1]:
                anchor = ' id="%s"' % it["anchor"] if it.get("anchor") else ""
                if not it.get("lines"):
                    out.append('<div class="fold"%s>%s</div>' % (anchor, _h_inline(it["summary"])))
                    continue
                out.append('<details class="fold"><summary>%s</summary><pre class="rule"%s>%s</pre></details>' % (
                    _h_inline(it["summary"]), anchor, html.escape("\n".join(it["lines"]), quote=False)))  # a link opens it
        elif k == "pre":
            out.append("<pre>%s</pre>" % html.escape("\n".join(b[1]), quote=False))
        elif k == "diagram" and b[1]:
            out.append('<div class="diagram">%s</div>' % flow_svg(b[1], b[2]))
        elif k == "details":
            level, summary, anchor, inner, is_open = b[1], b[2], b[3], b[4], b[5]
            if open_section:
                out.append("</section>")
                open_section = False
            out.append('<details class="d%d"%s%s><summary><span class="h%d">%s</span></summary><div class="inner">' % (
                level, ' id="%s"' % anchor if anchor else "", " open" if is_open else "", level, _h_inline(summary)))
            out += _h_blocks(inner)
            out.append("</div></details>")
        elif k == "nav":
            out.append('<nav class="toc">')
            out += _h_steps(b[1], False)
            out.append("</nav>")
        elif k == "box":
            out.append('<div class="box"%s><h3>%s%s</h3>' % (' id="%s"' % b[1] if b[1] else "", _h_inline(b[2]),
                                                             ' <span class="badge">%s</span>' % html.escape(b[3]) if b[3] else ""))
            out += ["<p>%s</p>" % _h_inline(x) for x in b[4]]
            if b[5]:
                out.append('<div class="meta">%s</div>' % "".join(
                    "<b>%s</b><span>%s</span>" % (html.escape(label), "<br>".join(_h_inline(x) for x in text)
                                                  if isinstance(text, list) else _h_inline(text)) for label, text in b[5]))
            out.append("</div>")
        elif k == "acts":
            out += _h_acts(b)
        elif k == "hk":
            out += _h_hk(b[1])
        elif k == "card":
            out.append('<div class="card"%s><div class="card-h">%s</div><dl>' % (
                ' id="%s"' % b[1] if b[1] else "", _h_inline(b[2])))
            for label, text, pre in b[3]:
                body = _h_inline(text) + ('<pre class="rule">%s</pre>' % html.escape("\n".join(pre), quote=False) if pre else "")
                out.append("<dt>%s</dt><dd>%s</dd>" % (html.escape(label), body))
            out.append("</dl></div>")
    if open_section:
        out.append("</section>")
    return out


def _h_folds(folds):
    out = []
    for f in folds:
        out.append('<div class="fs">%s</div>' % _h_inline(f["summary"]))
        anchor = ' id="%s"' % f["anchor"] if f.get("anchor") else ""
        out.append('<pre%s>%s</pre>' % (anchor, html.escape("\n".join(f["lines"]), quote=False)) if f.get("lines") else
                   "<span%s></span>" % anchor)
    return "".join(out)


def _h_acts(b):
    chip = '<span class="chip %s">%s</span>' % ("o" if b[2] == "output" else "w", _h_inline(b[3]))
    out = ['<div class="box story"%s>%s<h3>%s%s%s</h3>' % (
        ' id="%s"' % b[1] if b[1] else "", "".join('<span id="%s"></span>' % a for a in b[8] or []),
        '<span class="kind">working table</span> ' if b[2] == "table" else "", chip,
        ' <span class="badge">%s</span>' % html.escape(b[4]) if b[4] else "")]
    out += ['<p class="sub">%s</p>' % _h_inline(x) for x in b[5] if x]
    out.append('<table class="acts"><thead><tr><th>#</th><th>What happens</th><th>Columns</th><th>Tool</th></tr></thead>'
               '<tbody>')
    for r in b[6]:
        cell = ["".join('<span id="%s"></span>' % a for a in r.get("anchors") or []),
                '<div class="does">%s</div>' % _h_inline(r["text"])]
        cell += ['<div class="ln">%s</div>' % _h_inline(x) for x in r.get("lines") or []]
        if r.get("table"):
            t = r["table"]
            cell.append('<table class="mini"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (
                "".join("<th>%s</th>" % html.escape(h) for h in t["head"]),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _h_inline(c) for c in row) for row in t["rows"])))
        if r.get("split"):
            cell.append('<div class="split">↳ %s</div>' % _h_inline(r["split"]))
        if r.get("folds"):
            cell.append('<details class="rule"><summary>Rule and pandas</summary>%s</details>' % _h_folds(r["folds"]))
        tool = '<small>%s</small>%s%s' % (html.escape(r["small"]), '<br><i>“%s”</i>' % html.escape(r["note"])
                                           if r.get("note") else "",
                                           '<br><span class="hand">%s</span>' % html.escape(r["hand"]) if r.get("hand") else "")
        out.append('<tr%s%s><td class="n">%s</td><td>%s</td><td class="cols">%s</td><td class="tl">%s</td></tr>' % (
            ' id="%s"' % r["anchor"] if r.get("anchor") else "", ' class="sub"' if r.get("sub") else "",
            html.escape(r["num"]), "".join(cell), _h_inline(r.get("cols") or ""), tool))
    out.append("</tbody></table>")
    if b[7]:
        out += _h_hk(b[7])
    return out + ["</div>"]


def _h_hk(h):
    items = "".join('<li%s>%s</li>' % (' id="%s"' % a if a else "", _h_inline(x))
                    for x, a in zip(h["items"], (h.get("anchors") or []) + [None] * len(h["items"])))
    return ['<details class="hk"%s><summary>%s</summary><ul>%s</ul>%s</details>' % (
        ' id="%s"' % h["anchor"] if h.get("anchor") else "", html.escape(h["title"]), items,
        '<details class="rule"><summary>Their rule and pandas</summary>%s</details>' % _h_folds(h["folds"])
        if h.get("folds") else "")]


def to_html(blocks, title):
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>%s</title><style>%s</style></head><body><main>" % (html.escape(title), CSS)]
    return "\n".join(out + _h_blocks(blocks) + ["</main></body></html>"]) + "\n"
