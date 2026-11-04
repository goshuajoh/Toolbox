"""Parse HTML without a browser (standard library; bs4 only for CSS selectors): tables with colspan/rowspan,
visible text, links, forms (incl. hidden CSRF fields to replay a post with requests), JSON embedded in scripts."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import pandas as pd

_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tfoot",
          "caption", "section", "article", "header", "footer", "nav", "main", "aside", "address", "details", "summary",
          "fieldset", "legend", "figcaption", "ul", "ol", "dl", "dt", "dd", "pre", "blockquote", "hr", "form", "select",
          "option", "textarea"}
_HIDDEN = {"script", "style", "noscript", "template", "title"}


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._stack = [], []

    def _close_cell(self, t):
        if t["cell"] is not None and t["row"] is not None:
            t["row"].append(t["cell"])
        t["cell"] = None

    def _close_row(self, t):
        self._close_cell(t)
        if t["row"] is not None:
            t["rows"].append(t["row"])
        t["row"] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None, "head_rows": 0, "in_head": False})
            return
        if not self._stack:
            return
        t = self._stack[-1]
        if tag == "thead":
            t["in_head"] = True
        elif tag == "tr":
            self._close_row(t)
            t["row"] = []
            if t["in_head"]:
                t["head_rows"] += 1
        elif tag in ("td", "th"):
            if t["row"] is None:
                t["row"] = []
            self._close_cell(t)
            def span(k):
                try:
                    return max(1, int(re.sub(r"\D", "", a.get(k) or "1") or 1))
                except ValueError:
                    return 1
            t["cell"] = {"text": [], "colspan": span("colspan"), "rowspan": span("rowspan"), "th": tag == "th"}
        elif tag == "br" and t["cell"] is not None:
            t["cell"]["text"].append("\n")

    def handle_endtag(self, tag):
        if not self._stack:
            return
        t = self._stack[-1]
        if tag in ("td", "th"):
            self._close_cell(t)
        elif tag == "tr":
            self._close_row(t)
        elif tag == "thead":
            self._close_row(t)
            t["in_head"] = False
        elif tag == "table":
            self._close_row(t)
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"]["text"].append(data)


def _cell_text(cell):
    return re.sub(r"[ \t\r\f\v\u00a0]+", " ", "".join(cell["text"])).strip()


def _grid(rows):
    grid, spans = [], {}          # spans: column -> [rows_left, text]
    for row in rows:
        out, col, queue = [], 0, list(row)
        while queue or any(c >= col for c in spans):
            if col in spans:
                left, txt = spans[col]
                out.append(txt)
                spans[col][0] -= 1
                if spans[col][0] == 0:
                    del spans[col]
                col += 1
                continue
            if not queue:
                out.append(None)
                col += 1
                continue
            cell = queue.pop(0)
            txt = _cell_text(cell)
            for _ in range(cell["colspan"]):
                out.append(txt)
                if cell["rowspan"] > 1:
                    spans[col] = [cell["rowspan"] - 1, txt]
                col += 1
        grid.append(out)
    width = max((len(r) for r in grid), default=0)
    return [r + [None] * (width - len(r)) for r in grid]


def _unique(names):
    seen, out = {}, []
    for n in names:
        n = n if n not in (None, "") else "col"
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


def html_tables(html, header="auto", match=None):
    """Every <table> -> DataFrame (colspan/rowspan expanded, <br> -> newline). header: 'auto' (thead or all-<th>
    first row), True (first row), False (numbered columns). match: regex a table's text must contain."""
    p = _TableParser()
    p.feed(str(html))
    p.close()
    out = []
    for t in p.tables:
        if not t["rows"]:
            continue
        grid = _grid(t["rows"])
        if match and not re.search(match, " ".join(str(c) for r in grid for c in r if c), re.I):
            continue
        n_head = t["head_rows"] or (1 if all(c["th"] for c in t["rows"][0]) else 0) if header == "auto" \
            else (1 if header else 0)
        if n_head:
            cols = [" | ".join(dict.fromkeys(str(grid[h][i]) for h in range(n_head) if grid[h][i])) or None
                    for i in range(len(grid[0]))]
            out.append(pd.DataFrame(grid[n_head:], columns=_unique(cols)))
        else:
            out.append(pd.DataFrame(grid))
    return out


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN:
            self.skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _HIDDEN:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(html):
    """Visible text of a page (scripts/styles/title removed, one line per block or table row, spaces tidied)."""
    p = _TextParser()
    p.feed(str(html))
    p.close()
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in "".join(p.parts).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def strip_tags(series):
    """Remove HTML tags/entities from a column of HTML snippets."""
    return series.map(lambda v: html_text(v) if isinstance(v, str) else v)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._cur = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._cur = {"href": dict(attrs).get("href"), "text": []}

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self.links.append(self._cur)
            self._cur = None


def html_links(html, base_url=None, pattern=None):
    """All <a href> -> DataFrame text, url (absolute if base_url); pattern = regex the url must match."""
    p = _LinkParser()
    p.feed(str(html))
    p.close()
    rows = []
    for link in p.links:
        href = link["href"]
        if not href or href.startswith(("javascript:", "#")):
            continue
        url = urljoin(base_url, href) if base_url else href
        if pattern and not re.search(pattern, url):
            continue
        rows.append({"text": re.sub(r"\s+", " ", "".join(link["text"])).strip(), "url": url})
    return pd.DataFrame(rows, columns=["text", "url"])


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self._form, self._select, self._textarea = [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = {"action": a.get("action"), "method": (a.get("method") or "get").upper(),
                          "id": a.get("id"), "fields": {}}
        elif self._form is None:
            return
        elif tag == "input" and a.get("name"):
            kind = (a.get("type") or "text").lower()
            if kind in ("checkbox", "radio") and "checked" not in a:
                self._form["fields"].setdefault(a["name"], None)
                return
            if kind in ("checkbox", "radio"):
                self._form["fields"][a["name"]] = a.get("value") or "on"     # what a browser would send
            elif kind not in ("submit", "button", "image", "reset", "file"):
                self._form["fields"][a["name"]] = a.get("value", "")
        elif tag == "select" and a.get("name"):
            self._select = a["name"]
            self._form["fields"].setdefault(self._select, None)
        elif tag == "option" and self._select:
            if "selected" in a or self._form["fields"].get(self._select) is None:
                self._form["fields"][self._select] = a.get("value", "")
        elif tag == "textarea" and a.get("name"):
            self._textarea = a["name"]
            self._form["fields"][self._textarea] = ""

    def handle_data(self, data):
        if self._form is not None and self._textarea:
            self._form["fields"][self._textarea] += data

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None


def html_forms(html, base_url=None):
    """Every <form> -> dict(action, method, id, fields) with current/hidden values (e.g. CSRF tokens), so you
    can replay the post: session.post(form['action'], data={**form['fields'], 'user': 'x'})."""
    p = _FormParser()
    p.feed(str(html))
    p.close()
    for f in p.forms:
        if base_url:
            f["action"] = urljoin(base_url, f["action"] or "")
    return p.forms


def html_select(html, css, attr=None):
    """CSS selector query -> list of texts (or attribute values). Needs beautifulsoup4."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(str(html), "html.parser")
    return [el.get(attr) if attr else el.get_text(" ", strip=True) for el in soup.select(css)]


def _balanced(text, i):
    """Text of the JSON object/array starting at text[i] ('{' or '['), or None."""
    close, stack, quote, esc = {"{": "}", "[": "]"}, [], None, False
    for j in range(i, len(text)):
        ch = text[j]
        if quote:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in close:
            stack.append(close[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return text[i:j + 1]
    return None


def html_json(html):
    """JSON shipped inside a page, read without a browser: <script type="application/json"> / ld+json blocks
    (e.g. `__NEXT_DATA__`) and `window.__STATE__ = {...}` / `var rows = [...]` assignments -> [{'source', 'data'}].
    Only valid JSON is returned (JavaScript-only syntax is skipped)."""
    out = []
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", str(html), re.S | re.I):
        attrs, body = m.group(1), m.group(2).strip()
        typ = re.search(r"""type\s*=\s*["']?([^"'\s>]+)""", attrs, re.I)
        sid = re.search(r"""id\s*=\s*["']?([^"'\s>]+)""", attrs, re.I)
        if not body:
            continue
        if typ and "json" in typ.group(1).lower():
            try:
                out.append({"source": sid.group(1) if sid else typ.group(1), "data": json.loads(body)})
            except ValueError:
                pass
            continue
        for a in re.finditer(r"(?:\b(?:var|let|const)\s+|\bwindow\.|\bself\.)([\w$.]+)\s*=\s*(?=[\[{])", body):
            chunk = _balanced(body, a.end())
            if chunk:
                try:
                    out.append({"source": a.group(1), "data": json.loads(chunk)})
                except ValueError:
                    pass
    return out
