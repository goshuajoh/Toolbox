"""Notifications: Teams incoming webhook (no data, just status)."""


def teams_message(webhook_url, title, facts=None, color="0078D7", dry_run=False, post=None):
    """Post a card to a Teams channel webhook. facts = {"Status": "PARTIAL", "Exceptions": 3}."""
    payload = {"@type": "MessageCard", "@context": "http://schema.org/extensions", "themeColor": color,
               "summary": title, "title": title,
               "sections": [{"facts": [{"name": str(k), "value": str(v)} for k, v in (facts or {}).items()]}]}
    if dry_run:
        print(f"[dry-run] would post to Teams: {title}")
        return payload
    if post is None:
        import requests
        post = lambda url, json: requests.post(url, json=json, timeout=20).status_code  # noqa: E731
    status = post(webhook_url, payload)
    if status >= 400:
        raise RuntimeError(f"Teams webhook returned {status}")
    return payload
