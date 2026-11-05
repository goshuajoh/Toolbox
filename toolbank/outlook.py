"""Desktop Outlook via COM (Windows, signed-in Outlook; pip install pywin32)."""
import re
from pathlib import Path


def _ns():
    import win32com.client
    return win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")


def outlook_folder(path=("Inbox",), mailbox=None):
    """Folder by path, e.g. ("Inbox", "Confirms"); mailbox = shared mailbox display name, else your own."""
    ns = _ns()
    if mailbox:                                    # shared mailbox: start at its root, then walk the path
        folder, names = ns.Folders.Item(mailbox), list(path)
    elif path and str(path[0]).lower() == "inbox":
        folder, names = ns.GetDefaultFolder(6), list(path[1:])     # 6 = your Inbox (any Outlook language)
    else:
        folder, names = ns.GetDefaultFolder(6).Parent, list(path)  # your mailbox root, e.g. ("Sent Items",)
    for name in names:
        folder = folder.Folders.Item(name)
    return folder


def sender_smtp(mail):
    """Sender's SMTP address, also for internal Exchange senders (whose SenderEmailAddress is an /O=... path)."""
    try:
        if getattr(mail, "SenderEmailType", "") == "EX":
            user = mail.Sender.GetExchangeUser()
            if user is not None:
                return user.PrimarySmtpAddress
        return mail.SenderEmailAddress
    except Exception:
        return getattr(mail, "SenderEmailAddress", "")


def outlook_mails(folder=None, unread_only=False, subject_re=None, sender_re=None, newest=100):
    """Yield mail items newest first, filtered by unread / subject regex / sender regex."""
    folder = folder or outlook_folder()
    items = folder.Items
    items.Sort("[ReceivedTime]", True)
    seen = 0
    for m in items:
        if seen >= newest:
            break
        if getattr(m, "Class", None) != 43:   # 43 = MailItem (skip meeting requests etc.)
            continue
        seen += 1
        if unread_only and not m.UnRead:
            continue
        if subject_re and not re.search(subject_re, m.Subject or "", re.I):
            continue
        if sender_re and not re.search(sender_re, sender_smtp(m) or "", re.I):
            continue
        yield m


def outlook_save_attachments(mail, out_dir, pattern=r".*\.(xlsx|xlsm|xls|csv|pdf)$"):
    """Save attachments whose name matches pattern (safe names; two files with the same name never overwrite
    each other); returns paths."""
    from .files import unique_path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for att in mail.Attachments:
        if re.match(pattern, att.FileName, re.I):
            p = unique_path(out_dir, att.FileName).resolve()
            att.SaveAsFile(str(p))
            out.append(p)
    return out


def outlook_mark_read(mail, read=True):
    """Mark a mail read/unread."""
    mail.UnRead = not read
    mail.Save()


def outlook_mail_info(mail):
    """Plain dict of a mail item: subject, sender, received, body text, attachment names."""
    return {"subject": mail.Subject, "sender": sender_smtp(mail), "received": str(mail.ReceivedTime),
            "body": mail.Body, "attachments": [a.FileName for a in mail.Attachments], "entry_id": mail.EntryID}


def outlook_move(mail, folder):
    """Move a mail to another folder (e.g. 'Processed') so it is not picked up twice."""
    return mail.Move(folder)


def outlook_send(to, subject, body_html, attachments=(), cc="", send_as=None, dry_run=False, display_only=False):
    """Send (or display for review) an email from the signed-in profile."""
    attachments = list(attachments)
    if dry_run:
        print(f"[dry-run] would send '{subject}' to {to} with {len(attachments)} attachment(s)")
        return None
    import win32com.client
    m = win32com.client.Dispatch("Outlook.Application").CreateItem(0)
    m.To, m.CC, m.Subject, m.HTMLBody = to, cc, subject, body_html
    if send_as:
        m.SentOnBehalfOfName = send_as
    for a in attachments:
        m.Attachments.Add(str(Path(a).resolve()))
    m.Display() if display_only else m.Send()
    return m


def df_to_html(df, max_rows=50):
    """DataFrame -> simple HTML table for an email body."""
    return df.head(max_rows).to_html(index=False, border=1, justify="left", na_rep="")


def outlook_poll(handler, folder=None, subject_re=None, sender_re=None, processed_folder=None, newest=200):
    """Email trigger: for each UNREAD matching mail call handler(mail); on success mark it read and (optionally)
    move it to processed_folder so it is never handled twice. Failed mails stay unread for the next poll.
    Returns (handled_count, failures) where failures = [{'subject', 'sender', 'error'}]."""
    handled, failures = 0, []
    batch = list(outlook_mails(folder, unread_only=True, subject_re=subject_re, sender_re=sender_re, newest=newest))
    for m in batch:   # materialise first: moving items while iterating a live COM collection skips some
        try:
            handler(m)
        except Exception as e:
            failures.append({"subject": m.Subject, "sender": sender_smtp(m), "error": f"{type(e).__name__}: {e}"})
            continue
        outlook_mark_read(m)
        if processed_folder is not None:
            outlook_move(m, processed_folder)
        handled += 1
    return handled, failures
