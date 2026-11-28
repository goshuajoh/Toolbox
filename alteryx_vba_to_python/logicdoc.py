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
    ("story", anchor, kind, title, badge, intro, items)
                                        numbered steps; kind: output / table; item = a step or a side
    ("side", side)                      folded numbered steps: {"anchor", "title", "steps"}
A step is {"anchor", "tool", "note", "small", "text", "meta": [(label, text)], "split", "hand", "fold"}, fold as in
"folds"; a side is {"side": True, "anchor", "title", "steps"}.
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
        elif k == "story":
            out += ["", "#### " + _md_inline(b[3]) + (" _(%s)_" % b[4] if b[4] else ""), "", _md_inline(b[5]), ""]
            n = 0
            for it in b[6]:
                if it.get("side"):
                    out += _md_side(it)
                else:
                    n += 1
                    out += _md_step(it, n)
        elif k == "side":
            out += _md_side(b[1])
        elif k == "card":
            out += ["", "#" * 5 + " " + _md_inline(b[2]), ""]
            for label, text, pre in b[3]:
                out.append("- **%s:** %s" % (label, _md_inline(text)) if label else "- " + _md_inline(text))
                if pre:
                    out += [""] + ["      " + line for line in pre] + [""]
            out.append("")
    return out


def _md_step(it, n, lead=""):
    head = "%s**%s. %s**%s · %s%s" % (lead, n, it["tool"], " “%s”" % it["note"] if it.get("note") else "", it["small"],
                                     " · ⚠ " + it["hand"] if it.get("hand") else "")
    out = [head + "  ", lead + _md_inline(it["text"]) + "  "]
    out += [lead + ("%s: " % label if label else "") + _md_inline(text) + "  " for label, text in it.get("meta", [])]
    if it.get("split"):
        out.append(lead + "↳ " + _md_inline(it["split"]) + "  ")
    out.append("")
    f = it.get("fold")
    if f:
        out.append("- " + _md_inline(f["summary"]))
        if f.get("lines"):
            out += [""] + ["      " + line for line in f["lines"]]
        out.append("")
    return out


def _md_side(side):
    out = ["", "> **%s**" % _md_inline(side["title"]), ""]
    for n, it in enumerate(side["steps"], 1):
        out += _md_step(it, n, "> ")
    return out


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
@media (max-width:600px){.meta{grid-template-columns:1fr}ol.steps>li.side{padding-left:0}}
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
        elif k == "story":
            chip = '<span class="chip %s">%s</span>' % ("o" if b[2] == "output" else "w", _h_inline(b[3]))
            out.append('<div class="box story"%s><h3>%s%s%s</h3><p class="sub">%s</p><ol class="steps">' % (
                ' id="%s"' % b[1] if b[1] else "", '<span class="kind">working table</span> ' if b[2] == "table" else "",
                chip, ' <span class="badge">%s</span>' % html.escape(b[4]) if b[4] else "", _h_inline(b[5])))
            for it in b[6]:
                out += _h_side(it, True) if it.get("side") else _h_step(it)
            out.append("</ol></div>")
        elif k == "side":
            out += _h_side(b[1], False)
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


def _h_step(it):
    note = ' <i>“%s”</i>' % html.escape(it["note"]) if it.get("note") else ""
    out = ['<li%s><div><div class="tool">%s%s <small>· %s</small>%s</div>' % (
        ' id="%s"' % it["anchor"] if it.get("anchor") else "", html.escape(it["tool"]), note, html.escape(it["small"]),
        ' <span class="hand">%s</span>' % html.escape(it["hand"]) if it.get("hand") else ""),
        '<div class="does">%s</div>' % _h_inline(it["text"])]
    if it.get("meta"):
        out.append('<div class="meta">%s</div>' % "".join(
            "<b>%s</b><span>%s</span>" % (html.escape(label), _h_inline(text)) for label, text in it["meta"]))
    if it.get("split"):
        out.append('<div class="split">↳ %s</div>' % _h_inline(it["split"]))
    f = it.get("fold")
    if f:
        out.append('<details class="rule"><summary>Rule and pandas</summary><div class="fs">%s</div>%s</details>' % (
            _h_inline(f["summary"]), '<pre%s>%s</pre>' % (' id="%s"' % f["anchor"] if f.get("anchor") else "",
                                                          html.escape("\n".join(f["lines"]), quote=False))
            if f.get("lines") else '<span%s></span>' % (' id="%s"' % f["anchor"] if f.get("anchor") else "")))
    return out + ["</div></li>"]


def _h_side(side, in_list):
    out = ['<li class="side">'] if in_list else []
    out.append('<details class="side"%s><summary>%s</summary><ol class="steps">' % (
        ' id="%s"' % side["anchor"] if side.get("anchor") else "", _h_inline(side["title"])))
    for it in side["steps"]:
        out += _h_step(it)
    out.append("</ol></details>")
    return out + (["</li>"] if in_list else [])


def to_html(blocks, title):
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>%s</title><style>%s</style></head><body><main>" % (html.escape(title), CSS)]
    return "\n".join(out + _h_blocks(blocks) + ["</main></body></html>"]) + "\n"
