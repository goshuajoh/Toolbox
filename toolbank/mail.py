"""Email files and SMTP: read .eml / .msg (subject, sender, body, attachments), build messages, save an Outlook
draft (.eml that opens ready to review and Send), send through an SMTP relay.
Live Outlook: outlook.py. Microsoft Graph: api.graph_send_mail."""
import mimetypes
import os
import re
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from pathlib import Path

import pandas as pd

from .files import unique_path
from .robust import BusinessError, RobotError, SystemError_


def _parse_eml(path):
    with open(path, "rb") as fh:
        return BytesParser(policy=policy.default).parse(fh)


def _content(part):
    """Text of a MIME part; unknown / wrong charsets decode as UTF-8 with replacement instead of failing."""
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def read_eml(path):
    """.eml -> dict(subject, from, to, cc, date, text, html, attachments, message_id). text falls back to the
    HTML body's visible text."""
    from .htmlparse import html_text
    msg = _parse_eml(path)
    text, html = msg.get_body(preferencelist=("plain",)), msg.get_body(preferencelist=("html",))
    html = _content(html)
    try:
        date = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
    except (TypeError, ValueError):
        date = None
    return {"subject": str(msg["Subject"] or ""), "from": str(msg["From"] or ""), "to": str(msg["To"] or ""),
            "cc": str(msg["Cc"] or ""), "date": date,
            "text": _content(text) if text is not None else (html_text(html) if html else ""),
            "html": html, "attachments": [a.get_filename() or "attachment" for a in msg.iter_attachments()],
            "message_id": str(msg["Message-ID"] or "")}


def read_eml_folder(folder, pattern="*.eml"):
    """Every .eml in a folder -> DataFrame (one row per email, plus file name)."""
    rows = [{"file": p.name, **read_eml(p)} for p in sorted(Path(folder).glob(pattern))]
    return pd.DataFrame(rows)


def _save_bytes(out_dir, name, data):
    p = unique_path(out_dir, Path(str(name or "attachment")).name)   # safe name, extension kept, never overwrite
    p.write_bytes(data)
    return p


def eml_attachments(path, out_dir, pattern=r".*"):
    """Save attachments whose file name matches the regex; returns paths (names made safe, never overwritten)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for part in _parse_eml(path).iter_attachments():
        name = part.get_filename() or "attachment"
        if not re.search(pattern, name, re.I):
            continue
        data = part.get_payload(decode=True)
        if data is None and part.get_content_type() == "message/rfc822":
            data, name = part.get_payload(0).as_bytes(), (name if name.lower().endswith(".eml") else f"{name}.eml")
        if data is not None:
            saved.append(_save_bytes(out_dir, name, data))
    return saved


def read_msg(path):
    """Outlook .msg -> same dict as read_eml (pip install extract-msg)."""
    import extract_msg
    m = extract_msg.openMsg(str(path))
    try:
        html = getattr(m, "htmlBody", None) or b""
        return {"subject": m.subject or "", "from": m.sender or "", "to": m.to or "", "cc": m.cc or "",
                "date": m.date, "text": m.body or "",
                "html": html.decode("utf-8", "replace") if isinstance(html, bytes) else html,
                "attachments": [a.longFilename or a.shortFilename or "attachment" for a in m.attachments],
                "message_id": getattr(m, "messageId", "") or ""}
    finally:
        m.close()


def msg_attachments(path, out_dir, pattern=r".*"):
    """Save .msg attachments whose name matches the regex; returns paths."""
    import extract_msg
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    m, saved = extract_msg.openMsg(str(path)), []
    try:
        for a in m.attachments:
            name = a.longFilename or a.shortFilename or "attachment"
            if re.search(pattern, name, re.I) and isinstance(a.data, bytes):
                saved.append(_save_bytes(out_dir, name, a.data))
    finally:
        m.close()
    return saved


def _addr(v):
    return v if isinstance(v, str) else ", ".join(v)


def build_email(sender, to, subject, html=None, text=None, cc=(), bcc=(), attachments=(), reply_to=None):
    """EmailMessage with a plain-text body and optional HTML alternative + file attachments."""
    from .htmlparse import html_text
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, _addr(to), subject
    if cc:
        msg["Cc"] = _addr(cc)
    if bcc:
        msg["Bcc"] = _addr(bcc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
    msg.set_content(text if text is not None else (html_text(html) if html else ""))
    if html:
        msg.add_alternative(html, subtype="html")
    for p in attachments:
        p = Path(p)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
    return msg


def save_eml(msg, path, draft=True):
    """Write the message to .eml. draft=True adds X-Unsent so Outlook opens it as an unsent draft with a Send
    button: the robot prepares, a human reviews and sends."""
    if draft and msg["X-Unsent"] is None:
        msg["X-Unsent"] = "1"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")                      # temp file + rename: never a half-written draft
    tmp.write_bytes(msg.as_bytes(policy=msg.policy.clone(linesep="\r\n")))
    os.replace(tmp, p)
    return p


def smtp_send(msg, host, port=25, starttls=False, user=None, password=None, timeout=30, dry_run=False,
              smtp_cls=smtplib.SMTP):
    """Send through an SMTP relay. Refused recipients/sender or a permanent 5xx reply (e.g. a DLP block) ->
    BusinessError, login failure -> RobotError AUTH, 4xx / connection problems -> SystemError_. Not retried
    (a retry could send twice). Returns refused recipients."""
    if dry_run:
        print(f"[dry-run] would send '{msg['Subject']}' to {msg['To']}")
        return {}
    try:
        with smtp_cls(host, port, timeout=timeout) as s:
            if starttls:
                s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, password)
            return s.send_message(msg)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
        raise BusinessError(f"SMTP refused: {e}", code="SMTP_REFUSED") from e
    except smtplib.SMTPAuthenticationError as e:
        raise RobotError("SMTP login failed", code="AUTH") from e
    except smtplib.SMTPResponseException as e:
        if 500 <= e.smtp_code < 600:
            raise BusinessError(f"SMTP rejected the message: {e.smtp_code} {e.smtp_error!r}"[:300],
                                code="SMTP_REJECTED") from e
        raise SystemError_(f"SMTP {host}:{port} temporary failure {e.smtp_code}", code="SMTP") from e
    except (smtplib.SMTPException, OSError) as e:
        raise SystemError_(f"SMTP {host}:{port} failed: {type(e).__name__}: {e}", code="SMTP") from e
