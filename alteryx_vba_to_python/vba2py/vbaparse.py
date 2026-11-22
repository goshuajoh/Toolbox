"""VBA source text -> modules, procedures and a statement tree. Standard library only.

A real tokenizer and expression parser. Statements the parser does not know are kept as
{"k": "raw"} nodes with their source text, so later stages can show them and refuse to convert them.
"""
import re

KEYWORDS = {"if", "then", "else", "elseif", "end", "for", "each", "in", "to", "step", "next", "do", "loop", "while",
            "wend", "until", "with", "select", "case", "sub", "function", "exit", "on", "error", "goto", "resume",
            "dim", "set", "let", "call", "const", "and", "or", "not", "xor", "mod", "is", "like", "new", "nothing",
            "true", "false", "empty", "null", "byval", "byref", "optional", "as", "public", "private", "static"}


class VBAError(Exception):
    pass


# ----------------------------------------------------------------------------- lines
def logical_lines(text):
    """-> [(line_no, code, comment)] with continuations joined and comments split off."""
    out, buf, start = [], "", 0
    for n, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        if not buf:
            start = n
        code, comment = _split_comment(line)
        if code.rstrip().endswith(" _") or code.strip() == "_":
            buf += code.rstrip()[:-1] + " "
            continue
        out.append((start, (buf + code).strip(), comment))
        buf = ""
    if buf:
        out.append((start, buf.strip(), ""))
    return out


def _split_comment(line):
    in_str = False
    for i, c in enumerate(line):
        if c == '"':
            in_str = not in_str
        elif c == "'" and not in_str:
            return line[:i], line[i + 1:].strip()
    m = re.match(r"\s*Rem\b(.*)", line, re.I)
    return ("", m.group(1).strip()) if m else (line, "")


def split_statements(code):
    """Split 'a: b' into statements; ':=' and colons inside strings or brackets stay."""
    parts, depth, in_str, start, i = [], 0, False, 0, 0
    while i < len(code):
        c = code[i]
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif c == ":" and depth == 0 and code[i + 1:i + 2] != "=":
                parts.append(code[start:i].strip())
                start = i + 1
        i += 1
    parts.append(code[start:].strip())
    return [p for p in parts if p]


# ----------------------------------------------------------------------------- tokens
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<date>\#[^#\n]+\#)
  | (?P<br>\[[^\]\n]*\])
  | (?P<num>&H[0-9A-Fa-f]+&?|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?[%&!\#@]?)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*[$%&!\#]?)
  | (?P<op>:=|<>|<=|>=|=<|=>|[=<>&+\-*/\\^(),.!;])
""", re.X)


def tokenize(code):
    """-> [(kind, text, space_before)]"""
    out, i, space = [], 0, True
    while i < len(code):
        m = _TOKEN.match(code, i)
        if not m:
            raise VBAError("cannot read %r" % code[i:i + 20])
        i = m.end()
        kind = m.lastgroup
        if kind == "ws":
            space = True
            continue
        text = m.group(0)
        if kind == "str":
            text = text[1:-1].replace('""', '"')
        elif kind == "br":
            text = text[1:-1].strip()
        elif kind == "id":
            text = text.rstrip("$%&!#")
        out.append((kind, text, space))
        space = False
    out.append(("end", "", True))
    return out


# ----------------------------------------------------------------------------- expressions
BINARY = [("imp",), ("eqv",), ("xor",), ("or",), ("and",), None,                     # None: Not sits here
          ("=", "<>", "<", ">", "<=", ">=", "=<", "=>", "like", "is"), ("&",), ("+", "-"), ("mod",), ("\\",), ("*", "/")]


class Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def at(self, *words):
        kind, text, _ = self.peek()
        return kind in ("id", "op") and text.lower() in words

    def take(self):
        self.i += 1
        return self.t[self.i - 1]

    def need(self, word):
        if not self.at(word):
            raise VBAError("expected %s, found %r" % (word, self.peek()[1]))
        self.i += 1

    def done(self):
        return self.peek()[0] == "end"

    def expr(self, level=0):
        if level == len(BINARY):
            return self.unary()
        if BINARY[level] is None:
            if self.at("not"):
                self.take()
                return ("un", "not", self.expr(level))
            return self.expr(level + 1)
        node = self.expr(level + 1)
        while self.at(*BINARY[level]):
            op = self.take()[1].lower()
            node = ("bin", {"=<": "<=", "=>": ">="}.get(op, op), node, self.expr(level + 1))
        return node

    def unary(self):
        if self.at("-"):
            self.take()
            return ("un", "-", self.unary())
        if self.at("+"):
            self.take()
            return self.unary()
        node = self.postfix()
        if self.at("^"):
            self.take()
            node = ("bin", "^", node, self.unary())
        return node

    def args(self, closing=True):
        """Argument list up to ')' (or the end of the statement when closing=False)."""
        out = []
        while True:
            if (closing and self.at(")")) or self.done():
                break
            if self.at(","):
                self.take()
                out.append(None)                         # omitted argument
                continue
            if self.peek()[0] == "id" and self.peek(1)[1] == ":=":
                name = self.take()[1]
                self.take()
                out.append((name, self.expr()))
            else:
                if self.at("byval"):
                    self.take()
                out.append((None, self.expr()))
            if self.at(","):
                self.take()
                if (closing and self.at(")")) or self.done():
                    out.append(None)
            else:
                break
        if closing:
            self.need(")")
        return out

    def postfix(self):
        kind, text, space = self.peek()
        low = text.lower()
        if kind == "num":
            self.take()
            return ("num", text)
        if kind == "str":
            self.take()
            return ("str", text)
        if kind == "date":
            self.take()
            return ("date", text)
        if kind == "br":
            self.take()
            node = ("br", text)
        elif kind == "op" and text == "(":
            self.take()
            node = ("paren", self.expr())
            self.need(")")
        elif kind == "op" and text == ".":
            self.take()
            node = ("mem", ("with",), self.take()[1])
        elif kind == "id":
            self.take()
            if low in ("true", "false"):
                return ("bool", low == "true")
            if low in ("nothing", "empty", "null"):
                return (low,)
            if low == "new":
                return ("new", self.take()[1])
            if low in KEYWORDS:
                raise VBAError("unexpected keyword %s" % text)
            node = ("name", text)
        else:
            raise VBAError("unexpected %r" % text)
        while True:
            kind, text, space = self.peek()
            if kind == "op" and text == "." and not space:
                self.take()
                node = ("mem", node, self.take()[1])
            elif kind == "op" and text == "(" and not space:
                self.take()
                node = ("call", node, self.args())
            elif kind == "op" and text == "!" and not space:
                self.take()
                node = ("call", node, [(None, ("str", self.take()[1]))])
            else:
                return node


def parse_expr(code):
    p = Parser(tokenize(code))
    node = p.expr()
    if not p.done():
        raise VBAError("unexpected %r" % p.peek()[1])
    return node


# ----------------------------------------------------------------------------- statements
def _stmt(code, line):
    """One simple statement -> node dict. Block keywords are handled by the caller."""
    node = {"line": line, "src": code}
    p = Parser(tokenize(code))
    kind, text, _ = p.peek()
    low = text.lower() if kind == "id" else ""
    try:
        if low in ("dim", "static") or (low in ("public", "private") and not p.peek(1)[1].lower() in ("sub", "function", "const")):
            p.take()
            names = []
            while not p.done():
                name = p.take()[1]
                if p.at("("):
                    p.take()
                    p.args()
                    name += "()"
                typ = ""
                if p.at("as"):
                    p.take()
                    if p.at("new"):
                        p.take()
                    typ = p.take()[1]
                    while p.at("."):
                        p.take()
                        typ += "." + p.take()[1]
                names.append((name, typ))
                if p.at(","):
                    p.take()
            return dict(node, k="dim", names=names)
        if low == "const" or (low in ("public", "private") and p.peek(1)[1].lower() == "const"):
            while not p.at("const"):
                p.take()
            p.take()
            name = p.take()[1]
            if p.at("as"):
                p.take()
                p.take()
            p.need("=")
            return dict(node, k="const", name=name, value=p.expr())
        if low == "on":
            m = re.match(r"on\s+error\s+(goto\s+(\w+)|resume\s+next)\s*$", code, re.I)
            if not m:
                raise VBAError("unknown On statement")
            label = m.group(2)
            return dict(node, k="onerror", mode="resume" if label is None else "off" if label == "0" else "goto", label=label)
        if low == "exit":
            return dict(node, k="exit", what=p.peek(1)[1].lower())
        if low == "goto":
            return dict(node, k="goto", label=p.peek(1)[1])
        if low in ("resume", "end", "stop", "redim", "erase", "open", "close", "print", "line", "input", "declare",
                   "option", "attribute", "type", "enum", "implements", "event", "raiseevent", "gosub", "return", "load", "unload"):
            return dict(node, k="raw")
        is_set = low == "set"
        if low in ("set", "let"):
            p.take()
        if low == "call":
            p.take()
            target = p.postfix()
            if not p.done():
                raise VBAError("unexpected text after Call")
            return dict(node, k="call", target=target, args=None)
        target = p.postfix()
        if p.at("="):
            p.take()
            value = p.expr()
            if not p.done():
                raise VBAError("unexpected %r" % p.peek()[1])
            return dict(node, k="assign", target=target, value=value, set=is_set)
        if p.done():
            return dict(node, k="call", target=target, args=None)
        return dict(node, k="call", target=target, args=p.args(closing=False))
    except (VBAError, IndexError) as e:
        return dict(node, k="raw", why=str(e))


_PROC = re.compile(r"^(?:(public|private|friend)\s+)?(?:static\s+)?(sub|function)\s+(\w+)\s*(?:\((.*)\))?\s*(?:as\s+[\w.]+)?\s*$", re.I)


def _params(text):
    out = []
    for part in split_top(text or "", ","):
        m = re.match(r"\s*(optional\s+)?(byval\s+|byref\s+)?(paramarray\s+)?(\w+)(\(\))?(?:\s+as\s+([\w.]+))?(?:\s*=\s*(.+))?\s*$", part, re.I)
        if m:
            out.append({"name": m.group(4), "type": m.group(6) or "", "default": m.group(7),
                        "byval": (m.group(2) or "").strip().lower() == "byval"})
    return out


def split_top(text, sep):
    parts, depth, in_str, start = [], 0, False, 0
    for i, c in enumerate(text):
        if c == '"':
            in_str = not in_str
        elif not in_str:
            depth += c == "("
            depth -= c == ")"
            if c == sep and depth == 0:
                parts.append(text[start:i])
                start = i + 1
    parts.append(text[start:])
    return [p for p in parts if p.strip()]


def _cond(code, node):
    try:
        return parse_expr(code)
    except (VBAError, IndexError) as e:
        node["bad"] = "%s (%s)" % (code, e)
        return ("str", code)


def _block(items, pos, stops):
    """Parse statements until one of the stop words starts a line. -> (body, pos, stop line)."""
    body = []
    while pos < len(items):
        line, code, comment = items[pos]
        low = re.sub(r"\s+", " ", code.lower())
        first = low.split(" ")[0].rstrip(":") if low else ""
        two = " ".join(low.split(" ")[:2])
        if any(low == s or low.startswith(s + " ") or first == s or two == s for s in stops):
            return body, pos, items[pos]
        pos += 1
        if comment and not code:
            body.append({"k": "comment", "line": line, "src": "", "text": comment})
            continue
        if not code:
            continue
        node = {"line": line, "src": code, "comment": comment}
        m = re.match(r"if\s+(.*?)\s+then\s*(.*)$", code, re.I)
        if m and m.group(2):                                   # single-line If
            then_part, _, else_part = _split_else(m.group(2))
            node.update(k="if", branches=[(_cond(m.group(1), node), _simple(then_part, line))],
                        orelse=_simple(else_part, line) if else_part else [])
        elif m:
            branches, cond = [], _cond(m.group(1), node)
            while True:
                inner, pos, stop = _block(items, pos, ("elseif", "else", "end if"))
                branches.append((cond, inner))
                if stop is None:
                    break
                pos += 1
                s = stop[1]
                if re.match(r"elseif\b", s, re.I):
                    mm = re.match(r"elseif\s+(.*?)\s+then\s*(.*)$", s, re.I)
                    cond = _cond(mm.group(1) if mm else s, node)
                    continue
                if re.match(r"else\b", s, re.I):
                    rest = s[4:].lstrip(": ")
                    orelse, pos, stop = _block(items, pos, ("end if",))
                    pos += stop is not None
                    node["orelse"] = _simple(rest, stop[0] if stop else line) + orelse
                break
            node.update(k="if", branches=branches)
            node.setdefault("orelse", [])
        elif first == "with":
            inner, pos, stop = _block(items, pos, ("end with",))
            pos += stop is not None
            node.update(k="with", target=_cond(code[4:].strip(), node), body=inner)
        elif two == "for each":
            mm = re.match(r"for\s+each\s+(\w+)\s+in\s+(.*)$", code, re.I)
            inner, pos, stop = _block(items, pos, ("next",))
            pos += stop is not None
            node.update(k="foreach", var=mm.group(1) if mm else "?", coll=_cond(mm.group(2) if mm else code, node), body=inner)
        elif first == "for":
            mm = re.match(r"for\s+(\w+)\s*=\s*(.*?)\s+to\s+(.*?)(?:\s+step\s+(.*))?$", code, re.I)
            inner, pos, stop = _block(items, pos, ("next",))
            pos += stop is not None
            if mm:
                node.update(k="for", var=mm.group(1), start=_cond(mm.group(2), node), stop=_cond(mm.group(3), node),
                            step=_cond(mm.group(4), node) if mm.group(4) else None, body=inner)
            else:
                node.update(k="raw", why="For statement not understood")
        elif first in ("do", "while"):
            mm = re.match(r"(?:do\s+)?(while|until)\s+(.*)$", code, re.I)
            inner, pos, stop = _block(items, pos, ("loop", "wend"))
            pos += stop is not None
            post = re.match(r"loop\s+(while|until)\s+(.*)$", stop[1], re.I) if stop else None
            node.update(k="do", pre=(mm.group(1).lower(), _cond(mm.group(2), node)) if mm else None,
                        post=(post.group(1).lower(), _cond(post.group(2), node)) if post else None, body=inner)
        elif two == "select case":
            cases, subject = [], _cond(code[11:].strip(), node)
            _, pos, stop = _block(items, pos, ("case", "end select"))
            while stop is not None and re.match(r"case\b", stop[1], re.I):
                parts = split_statements(stop[1][4:].strip())
                head, first = (parts[0] if parts else ""), _simple(": ".join(parts[1:]), stop[0])
                pos += 1
                inner, pos, stop = _block(items, pos, ("case", "end select"))
                if head.lower().startswith("else"):
                    cases.append((None, _simple(head[4:], line) + first + inner))
                else:
                    tests = []
                    for part in split_top(head, ","):
                        part = part.strip()
                        mi = re.match(r"is\s*(<>|<=|>=|=|<|>)\s*(.*)$", part, re.I)
                        mt = re.match(r"(.*?)\s+to\s+(.*)$", part, re.I)
                        tests.append(("is", mi.group(1), _cond(mi.group(2), node)) if mi else
                                     ("to", _cond(mt.group(1), node), _cond(mt.group(2), node)) if mt else
                                     ("eq", _cond(part, node)))
                    cases.append((tests, first + inner))
            pos += stop is not None
            node.update(k="select", subject=subject, cases=cases)
        else:
            mm = re.match(r"([A-Za-z_]\w*):\s*(.*)$", code)
            if mm and mm.group(1).lower() not in KEYWORDS and not mm.group(2).startswith("="):
                body.append(dict(node, k="label", name=mm.group(1)))
                body += _simple(mm.group(2), line)
                continue
            body += _simple(code, line, comment)
            continue
        body.append(node)
    return body, pos, None


def _split_else(text):
    depth, in_str = 0, False
    for m in re.finditer(r'"|\(|\)|\belse\b', text, re.I):
        tok = m.group(0)
        if tok == '"':
            in_str = not in_str
        elif not in_str and tok == "(":
            depth += 1
        elif not in_str and tok == ")":
            depth -= 1
        elif not in_str and depth == 0 and tok.lower() == "else":
            return text[:m.start()].strip(), "else", text[m.end():].strip()
    return text.strip(), "", ""


def _simple(code, line, comment=""):
    out = []
    for part in split_statements(code or ""):
        node = _stmt(part, line)
        node["comment"] = comment
        out.append(node)
    return out


# ----------------------------------------------------------------------------- modules
def parse_module(text, name="Module"):
    """-> {"name", "procs": [...], "decls": [...], "compare_text": bool}"""
    m = re.search(r'^\s*Attribute\s+VB_Name\s*=\s*"([^"]+)"', text, re.M | re.I)
    module = {"name": m.group(1) if m else name, "procs": [], "decls": [],
              "compare_text": bool(re.search(r"^\s*Option\s+Compare\s+Text", text, re.M | re.I))}
    items = logical_lines(text)
    pos = 0
    while pos < len(items):
        line, code, comment = items[pos]
        pm = _PROC.match(code)
        if pm:
            body, pos2, stop = _block(items, pos + 1, ("end sub", "end function"))
            module["procs"].append({"name": pm.group(3), "kind": pm.group(2).lower(), "params": _params(pm.group(4)),
                                    "private": (pm.group(1) or "").lower() == "private", "body": body, "line": line,
                                    "end": stop[0] if stop else items[-1][0], "module": module["name"]})
            pos = pos2 + 1
            continue
        pos += 1
        if not code or re.match(r"(attribute|option|version|begin|end|multiuse)\b", code, re.I):
            continue
        if re.match(r"(public\s+|private\s+)?(property|type|enum|declare)\b", code, re.I):
            module["decls"].append({"k": "raw", "line": line, "src": code, "why": "declaration kind not supported"})
            continue
        module["decls"] += _simple(code, line, comment)
    return module


def split_modules(text, default="Module1"):
    """A text file holding several exported modules (each starting with Attribute VB_Name) -> [(name, text)]."""
    marks = [m.start() for m in re.finditer(r"^\s*Attribute\s+VB_Name\s*=", text, re.M | re.I)]
    if len(marks) < 2:
        return [(default, text)]
    cuts = [0] + marks[1:] + [len(text)]
    return [(default, text[a:b]) for a, b in zip(cuts, cuts[1:])]


def to_text(node):
    """Expression tree back to compact VBA text (for the specification)."""
    k = node[0]
    if k in ("num", "date"):
        return node[1]
    if k == "str":
        return '"%s"' % node[1].replace('"', '""')
    if k == "br":
        return "[%s]" % node[1]
    if k == "name":
        return node[1]
    if k == "bool":
        return "True" if node[1] else "False"
    if k in ("nothing", "empty", "null"):
        return k.capitalize()
    if k == "new":
        return "New " + node[1]
    if k == "with":
        return ""
    if k == "mem":
        return "%s.%s" % (to_text(node[1]), node[2])
    if k == "paren":
        return "(%s)" % to_text(node[1])
    if k == "call":
        return "%s(%s)" % (to_text(node[1]), args_text(node[2]))
    if k == "un":
        return ("Not " if node[1] == "not" else "-") + to_text(node[2])
    if k == "bin":
        op = node[1]
        return "%s %s %s" % (to_text(node[2]), op.capitalize() if op.isalpha() else op, to_text(node[3]))
    return "?"


def args_text(args):
    return ", ".join("" if a is None else ("%s:=%s" % (a[0], to_text(a[1])) if a[0] else to_text(a[1])) for a in args or [])
