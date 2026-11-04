"""HTTP / REST APIs with requests: sessions with retry + corporate proxy/CA, JSON calls with classified errors,
pagination (next-link / Link header / page / offset / cursor), OAuth2 tokens, downloads/uploads, rate limiting,
Microsoft Graph helpers, signed webhooks (also Power Automate / Teams workflow HTTP triggers)."""
import base64
import hashlib
import hmac
import json
import os
import re
import shlex
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import pandas as pd

from .robust import BusinessError, RobotError, SystemError_

GRAPH = "https://graph.microsoft.com/v1.0"


def http_session(retries=3, backoff=0.5, timeout=30, verify=None, proxies=None, headers=None, auth=None,
                 retry_post=False, status_forcelist=(429, 500, 502, 503, 504)):
    """requests.Session with automatic retry + backoff (GET/PUT/DELETE; POST only if retry_post), a default
    timeout, corporate CA bundle (verify='C:/certs/corp.pem', or env REQUESTS_CA_BUNDLE) and proxies."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    class _Session(requests.Session):
        def request(self, method, url, **kw):
            kw.setdefault("timeout", timeout)
            return super().request(method, url, **kw)

    methods = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"} | ({"POST", "PATCH"} if retry_post else set())
    retry = Retry(total=retries, connect=retries, read=retries, status=retries, backoff_factor=backoff,
                  status_forcelist=status_forcelist, allowed_methods=frozenset(methods),
                  respect_retry_after_header=True, raise_on_status=False)
    s = _Session()
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if verify is not None:
        s.verify = verify
    if proxies:
        s.proxies.update(proxies)
    if headers:
        s.headers.update(headers)
    if auth is not None:
        s.auth = auth
    return s


def raise_for_status(status, url="", body=""):
    """Classified error for an HTTP status: 5xx/429 -> SystemError_ (retry later), 401/403 -> RobotError AUTH
    (fix credentials/permissions), other 4xx -> BusinessError (fix the request/data). < 400 -> no error."""
    if status < 400:
        return
    ctx = {"status": status, "url": url}
    body = (body or "")[:300]
    if status in (401, 403):
        raise RobotError(f"HTTP {status}: not authorised", code="AUTH", **ctx)
    if status == 429 or status >= 500:
        raise SystemError_(f"HTTP {status}: {body}", code="HTTP_RETRYABLE", **ctx)
    raise BusinessError(f"HTTP {status}: {body}", code=f"HTTP_{status}", **ctx)


def check_response(resp):
    """raise_for_status for a requests.Response; returns resp when OK."""
    if resp.status_code >= 400:
        raise_for_status(resp.status_code, resp.url, resp.text)
    return resp


def api_call(session, method, url, *, json_body=None, params=None, headers=None, data=None, files=None,
             expect_json=True, **kw):
    """One request -> parsed JSON (None for empty body), or the Response if expect_json=False.
    Network failures -> SystemError_; SSL certificate problems -> RobotError SSL (set verify= to the corporate CA)."""
    import requests
    try:
        r = session.request(method.upper(), url, json=json_body, params=params, headers=headers, data=data,
                            files=files, **kw)
    except requests.exceptions.SSLError as e:
        raise RobotError(f"SSL error calling {url}: set verify='path/to/corporate-ca.pem' ({e})", code="SSL") from e
    except requests.exceptions.RequestException as e:
        raise SystemError_(f"{method.upper()} {url} failed: {type(e).__name__}: {e}", code="HTTP_NETWORK") from e
    check_response(r)
    if not expect_json:
        return r
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError as e:
        raise BusinessError(f"{url} did not return JSON", code="NOT_JSON", status=r.status_code) from e


def api_get(session, url, params=None, **kw):
    """GET -> JSON (see api_call)."""
    return api_call(session, "GET", url, params=params, **kw)


def api_post(session, url, json_body=None, **kw):
    """POST JSON -> JSON (not retried unless the session was built with retry_post=True)."""
    return api_call(session, "POST", url, json_body=json_body, **kw)


def _dig(data, key):
    if isinstance(data, dict) and key in data:       # whole key first: '@odata.nextLink' contains a dot
        return data[key]
    cur = data
    for part in str(key).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _items(data, items_key):
    if items_key is not None:
        found = _dig(data, items_key) if isinstance(data, dict) else None
        return found or []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("value", "data", "items", "results", "records", "rows"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def paginate(session, url, params=None, style="next_link", items_key=None, next_key="@odata.nextLink",
             page_param="page", size_param=None, size=100, start_page=1, offset_param="offset",
             limit_param="limit", cursor_param="cursor", cursor_key="next_cursor", max_pages=10_000, headers=None):
    """Yield every record across pages. style:
    'next_link'   response holds the next URL (Graph/OData '@odata.nextLink', or next_key='links.next')
    'link_header' HTTP Link: <...>; rel="next" (GitHub-style)
    'page'        page=1,2,... until an empty page (size_param='pageSize' sends the size)
    'offset'      offset/limit until a short page
    'cursor'      cursor token from response key cursor_key sent back as cursor_param
    items_key: where the list is ('value', 'data.items'); None = auto-detect common keys or a bare list."""
    params = dict(params or {})
    pages = 0
    if style in ("next_link", "link_header"):
        next_url, next_params = url, params
        while next_url and pages < max_pages:
            r = api_call(session, "GET", next_url, params=next_params, headers=headers, expect_json=False)
            data = r.json() if r.content else {}
            yield from _items(data, items_key)
            pages += 1
            next_params = None   # the next link already carries the query string
            nxt = (_dig(data, next_key) if isinstance(data, dict) else None) if style == "next_link" \
                else r.links.get("next", {}).get("url")
            next_url = urljoin(r.url, nxt) if nxt else None
    elif style == "page":
        page = start_page
        while pages < max_pages:
            p = {**params, page_param: page}
            if size_param:
                p[size_param] = size
            batch = _items(api_get(session, url, params=p, headers=headers), items_key)
            if not batch:
                break
            yield from batch
            pages += 1
            if size_param and len(batch) < size:
                break
            page += 1
    elif style == "offset":
        offset = 0
        while pages < max_pages:
            batch = _items(api_get(session, url, params={**params, offset_param: offset, limit_param: size},
                                   headers=headers), items_key)
            if not batch:
                break
            yield from batch
            pages += 1
            if len(batch) < size:
                break
            offset += len(batch)
    elif style == "cursor":
        cursor = None
        while pages < max_pages:
            p = dict(params)
            if cursor:
                p[cursor_param] = cursor
            data = api_get(session, url, params=p, headers=headers)
            batch = _items(data, items_key)
            yield from batch
            pages += 1
            cursor = _dig(data, cursor_key) if isinstance(data, dict) else None
            if not cursor or not batch:
                break
    else:
        raise ValueError(f"unknown pagination style {style!r}")


def api_to_df(records, sep="."):
    """List/generator of JSON records -> flat DataFrame ('a.b' columns for nested fields)."""
    return pd.json_normalize(list(records), sep=sep)


class TokenCache:
    """OAuth2 client-credentials token that refreshes itself 60 s before expiry. Azure AD / Entra ID:
    token_url=f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', scope='https://graph.microsoft.com/.default'."""

    def __init__(self, token_url, client_id, client_secret, scope=None, session=None, extra=None, leeway_s=60):
        self.token_url, self.client_id, self.client_secret = token_url, client_id, client_secret
        self.scope, self.session, self.extra, self.leeway = scope, session, extra or {}, leeway_s
        self._token, self._expires, self._lock = None, 0.0, threading.Lock()

    def get(self):
        """Current access token (fetches a new one when missing or about to expire)."""
        with self._lock:
            if self._token and time.time() < self._expires - self.leeway:
                return self._token
            form = {"grant_type": "client_credentials", "client_id": self.client_id,
                    "client_secret": self.client_secret, **self.extra}
            if self.scope:
                form["scope"] = self.scope
            j = api_call(self.session or http_session(), "POST", self.token_url, data=form)
            self._token = j["access_token"]
            self._expires = time.time() + float(j.get("expires_in", 3600))
            return self._token

    def headers(self):
        return {"Authorization": f"Bearer {self.get()}"}


def bearer_session(token, **session_kw):
    """Session that sends 'Authorization: Bearer <token>' on every request; token = string, TokenCache or callable."""
    import requests

    class _Bearer(requests.auth.AuthBase):
        def __call__(self, r):
            t = token.get() if hasattr(token, "get") else (token() if callable(token) else token)
            r.headers["Authorization"] = f"Bearer {t}"
            return r

    return http_session(auth=_Bearer(), **session_kw)


def api_key_session(key, header="X-API-Key", **session_kw):
    """Session that sends an API key header on every request."""
    return http_session(headers={header: key}, **session_kw)


def windows_auth_session(**session_kw):
    """Session using your Windows login for intranet sites (Kerberos/NTLM SSO).
    pip install requests-negotiate-sspi (preferred on Windows) or requests_ntlm."""
    try:
        from requests_negotiate_sspi import HttpNegotiateAuth
        return http_session(auth=HttpNegotiateAuth(), **session_kw)
    except ImportError:
        from requests_ntlm import HttpNtlmAuth
        user, pw = os.environ["NTLM_USER"], os.environ["NTLM_PASSWORD"]
        return http_session(auth=HttpNtlmAuth(user, pw), **session_kw)


def download_file(session, url, dest, sha256=None, chunk=1 << 16, **kw):
    """Stream a URL to dest atomically (temp file, then rename); optional SHA-256 check. Returns the Path."""
    import requests
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = session.get(url, stream=True, **kw)
    except requests.exceptions.RequestException as e:
        raise SystemError_(f"download {url} failed: {e}", code="HTTP_NETWORK") from e
    check_response(r)
    h = hashlib.sha256()
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".dl-")
    try:
        with os.fdopen(fd, "wb") as fh:
            for part in r.iter_content(chunk):
                fh.write(part)
                h.update(part)
        if sha256 and h.hexdigest() != sha256.lower():
            raise BusinessError(f"checksum mismatch for {url}", code="CHECKSUM")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dest


def upload_file(session, url, path, field="file", data=None, **kw):
    """Multipart file upload (POST form field `field`) -> JSON response."""
    path = Path(path)
    with open(path, "rb") as fh:
        return api_call(session, "POST", url, files={field: (path.name, fh)}, data=data, **kw)


class RateLimiter:
    """At most `rate` calls per `per` seconds (thread-safe). Call .wait() before each request."""

    def __init__(self, rate, per=1.0, clock=time.monotonic, sleep=time.sleep):
        self.capacity, self.per, self.clock, self.sleep = float(rate), float(per), clock, sleep
        self.tokens, self.last, self.lock = float(rate), clock(), threading.Lock()

    def wait(self):
        while True:
            with self.lock:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.capacity / self.per)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                need = (1 - self.tokens) * self.per / self.capacity
            self.sleep(need)


# ---------- signed webhooks ----------

def sign_body(body, secret):
    """'sha256=<hex>' HMAC of the raw body bytes with a shared secret."""
    body = body if isinstance(body, bytes) else str(body).encode("utf-8")
    return "sha256=" + hmac.new(str(secret).encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body, secret, signature):
    """Constant-time check of a 'sha256=<hex>' signature against the raw body."""
    return bool(signature) and hmac.compare_digest(sign_body(body, secret), str(signature))


def post_webhook(url, payload, secret=None, session=None, header="X-Signature", dry_run=False):
    """POST JSON to a webhook (internal service, Power Automate 'When an HTTP request is received', Teams workflow).
    With secret, adds an HMAC signature header the receiver can verify. Not retried (may have been delivered)."""
    body = json.dumps(payload, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret:
        headers[header] = sign_body(body, secret)
    if dry_run:
        print(f"[dry-run] would POST {len(body)} bytes to webhook")
        return None
    return api_call(session or http_session(), "POST", url, data=body, headers=headers)


# ---------- Microsoft Graph (Outlook / SharePoint / OneDrive / Teams) ----------

def graph_get_all(session, path, params=None, base=GRAPH):
    """All records from a Graph list endpoint ('/sites/{id}/drives', '/users/{id}/messages'), following nextLink."""
    return list(paginate(session, f"{base}{path}", params=params, style="next_link", items_key="value"))


def graph_download(session, drive_id, item_path, dest, base=GRAPH):
    """Download a SharePoint/OneDrive file by path inside a drive."""
    return download_file(session, f"{base}/drives/{drive_id}/root:/{item_path.lstrip('/')}:/content", dest)


def graph_upload(session, drive_id, item_path, local, base=GRAPH):
    """Upload a file (< 4 MB) to a SharePoint/OneDrive path; returns the drive item JSON."""
    with open(local, "rb") as fh:
        return api_call(session, "PUT", f"{base}/drives/{drive_id}/root:/{item_path.lstrip('/')}:/content",
                        data=fh.read(), headers={"Content-Type": "application/octet-stream"})


def graph_send_mail(session, sender, to, subject, html, cc=(), attachments=(), base=GRAPH, dry_run=False):
    """Send mail from a mailbox the app may send as (Mail.Send). Attachments are file paths."""
    to, cc = list(to) if not isinstance(to, str) else [to], list(cc) if not isinstance(cc, str) else [cc]
    atts = [{"@odata.type": "#microsoft.graph.fileAttachment", "name": Path(p).name,
             "contentBytes": base64.b64encode(Path(p).read_bytes()).decode("ascii")} for p in attachments]
    msg = {"message": {"subject": subject, "body": {"contentType": "HTML", "content": html},
                       "toRecipients": [{"emailAddress": {"address": a}} for a in to],
                       "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
                       "attachments": atts}, "saveToSentItems": True}
    if dry_run:
        print(f"[dry-run] would send '{subject}' to {len(to)} recipient(s)")
        return None
    return api_call(session, "POST", f"{base}/users/{sender}/sendMail", json_body=msg)


# ---------- reuse what the browser does (DevTools) ----------

_CURL_DATA = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json"}
_CURL_SKIP_ARG = {"-o", "--output", "-m", "--max-time", "--connect-timeout", "-A", "--user-agent", "-e", "--referer",
                  "-x", "--proxy", "-u", "--user", "-F", "--form", "--retry"}


def curl_to_request(curl_text, drop=("cookie", "authorization", "content-length", "host")):
    """DevTools 'Copy as cURL' (bash or cmd) -> dict(method, url, params, headers, json | data) for api_call.
    Cookie/Authorization are dropped by default: authenticate with your own session, never paste secrets in code."""
    text = curl_text.strip()
    if '^"' in text:                                   # Windows 'Copy as cURL (cmd)' escaping
        text = re.sub(r"\^\r?\n", " ", text)
        text = re.sub(r"\^(.)", r"\1", text)
    text = re.sub(r"\\\r?\n", " ", text)
    tokens = shlex.split(text)
    if not tokens or tokens[0].lower() not in ("curl", "curl.exe"):
        raise ValueError("expected text starting with 'curl'")
    method, url, headers, data, is_json, get = None, None, {}, None, False, False
    it = iter(tokens[1:])
    for tok in it:
        if tok in ("-X", "--request"):
            method = next(it).upper()
        elif tok in ("-H", "--header"):
            name, _, value = next(it).partition(":")
            headers[name.strip()] = value.strip()
        elif tok in ("-b", "--cookie"):
            headers["Cookie"] = next(it)
        elif tok in _CURL_DATA:
            part = next(it)
            data = part if data is None else f"{data}&{part}"
            is_json = is_json or tok == "--json"
        elif tok in ("-G", "--get"):
            get = True
        elif tok == "--url":
            url = next(it)
        elif tok in _CURL_SKIP_ARG:
            next(it, None)
        elif tok.startswith("-"):
            continue                                   # --compressed, -s, -L, -k/--insecure (never copied over)
        elif url is None:
            url = tok
    if url is None:
        raise ValueError("no URL found in the curl command")
    parts = urlsplit(url)
    params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    if get and data:
        params.update({k: v[0] if len(v) == 1 else v for k, v in parse_qs(data, keep_blank_values=True).items()})
        data = None
    out = {"method": method or ("POST" if data is not None else "GET"),
           "url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")), "params": params,
           "headers": {k: v for k, v in headers.items() if k.lower() not in {d.lower() for d in drop}}}
    ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    if data is not None:
        if is_json or "json" in ctype:
            try:
                out["json"] = json.loads(data)
            except ValueError:
                out["data"] = data
        else:
            out["data"] = data
    return out


def _har(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)["log"]["entries"]


def har_entries(path):
    """HAR file (Playwright record_har, or Edge DevTools > Network > Export HAR (sanitized)) -> DataFrame of
    calls: started, method, url, status, mime, type, size, ms. Find the JSON API behind a page."""
    rows = []
    for e in _har(path):
        req, resp = e.get("request", {}), e.get("response", {})
        content = resp.get("content", {})
        rows.append({"started": e.get("startedDateTime"), "method": req.get("method"), "url": req.get("url"),
                     "status": resp.get("status"), "mime": content.get("mimeType", ""),
                     "type": e.get("_resourceType", ""), "size": content.get("size"), "ms": e.get("time")})
    return pd.DataFrame(rows, columns=["started", "method", "url", "status", "mime", "type", "size", "ms"])


def har_json(path, url_re=".*"):
    """JSON bodies recorded in a HAR whose URL matches url_re: [{'url', 'method', 'status', 'data'}]."""
    rx, out = re.compile(url_re), []
    for e in _har(path):
        url = e.get("request", {}).get("url", "")
        content = e.get("response", {}).get("content", {})
        text = content.get("text")
        if not rx.search(url) or "json" not in content.get("mimeType", "") or text is None:
            continue
        if content.get("encoding") == "base64":
            text = base64.b64decode(text).decode("utf-8", "replace")
        try:
            out.append({"url": url, "method": e["request"].get("method"), "status": e["response"].get("status"),
                        "data": json.loads(text)})
        except ValueError:
            continue
    return out
