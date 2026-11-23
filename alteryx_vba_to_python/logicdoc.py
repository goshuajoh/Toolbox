"""A small document model written out as Markdown (readable as plain text) and as one self-contained
HTML page (no scripts, no network), plus a layered flow diagram drawn as SVG. Standard library only.

Blocks:
    ("h", level, text, anchor)          heading
    ("p", text)                         paragraph
    ("steps", items, ordered)           list; item = {"text", "pre": [lines], "children": [items]}
    ("table", header, rows)             table; cells are text
    ("pre", lines)                      preformatted lines
    ("diagram", nodes, edges)           drawn in HTML only; see flow_svg()
Inline text may use **bold**, `code` and [[anchor|label]] links.
"""
import html
import re

_LINK = re.compile(r"\[\[([\w-]+)\|([^\]]+)\]\]")


# ----------------------------------------------------------------------------- Markdown
def _md_inline(text):
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


def to_markdown(blocks):
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
        elif k == "pre":
            out += ["    " + line for line in b[1]] + [""]
    text = "\n".join(out).strip("\n") + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


# ----------------------------------------------------------------------------- HTML
def _h_inline(text):
    s = html.escape(text, quote=False)
    s = _LINK.sub(lambda m: '<a href="#%s">%s</a>' % (m.group(1), m.group(2)), s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def _h_steps(items, ordered):
    tag = "ol" if ordered else "ul"
    out = ["<%s>" % tag]
    for it in items:
        out.append("<li>" + _h_inline(it["text"]))
        if it.get("pre"):
            out.append('<pre class="rule">%s</pre>' % html.escape("\n".join(it["pre"]), quote=False))
        if it.get("children"):
            out += _h_steps(it["children"], it.get("ordered", False))
        out.append("</li>")
    return out + ["</%s>" % tag]


CSS = """
:root{--ink:#1f2328;--muted:#59636e;--line:#d1d9e0;--soft:#f6f8fa;--accent:#0b5cad;--warn:#9a3412;--box:#eef4fb}
@media (prefers-color-scheme:dark){:root{--ink:#e6edf3;--muted:#9da7b3;--line:#3d444d;--soft:#151b23;--accent:#58a6ff;
--warn:#f0883e;--box:#16273d}}
body{font:15px/1.55 -apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:var(--soft);margin:0}
main{max-width:1150px;margin:0 auto;padding:24px 16px 80px;background:transparent}
h1{font-size:1.6em;margin:.2em 0 .6em}h2{font-size:1.3em;margin:2em 0 .5em;padding-bottom:.25em;border-bottom:1px solid var(--line)}
h3{font-size:1.1em;margin:1.6em 0 .4em}h4{font-size:1em;margin:1.2em 0 .3em;color:var(--muted)}
p{margin:.4em 0 .8em}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
code{font:13px ui-monospace,Consolas,monospace;background:var(--box);padding:1px 4px;border-radius:4px}
pre{font:13px/1.45 ui-monospace,Consolas,monospace;background:var(--box);padding:8px 12px;border-radius:6px;overflow-x:auto;
margin:.4em 0 .7em}li{margin:.25em 0}li>pre.rule{margin-top:.3em}
table{border-collapse:collapse;margin:.5em 0 1.2em;font-size:14px;width:100%}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}th{background:var(--box)}
section.stage{background:var(--soft);border-left:3px solid var(--line);padding:0 0 0 14px;margin:1em 0}
.diagram{overflow:auto;border:1px solid var(--line);border-radius:6px;background:var(--soft);margin:.6em 0 1.2em}
.diagram svg{display:block}.warn{color:var(--warn)}
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


def to_html(blocks, title):
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>%s</title><style>%s</style></head><body><main>" % (html.escape(title), CSS)]
    open_section = False
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
            out += ["<tr>%s</tr>" % "".join("<td>%s</td>" % _h_inline(str(c)) for c in row) for row in b[2]]
            out.append("</tbody></table>")
        elif k == "pre":
            out.append("<pre>%s</pre>" % html.escape("\n".join(b[1]), quote=False))
        elif k == "diagram" and b[1]:
            out.append('<div class="diagram">%s</div>' % flow_svg(b[1], b[2]))
    if open_section:
        out.append("</section>")
    return "\n".join(out + ["</main></body></html>"]) + "\n"
