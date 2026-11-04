"""Tiny local HTTP server for tests (no internet needed)."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


@contextmanager
def serve(routes):
    """routes: {(method, path): fn(req) -> (status, headers, body)}; req has .query, .body, .headers, .path."""
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _go(self, method):
            parts = urlsplit(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            self.body = self.rfile.read(n) if n else b""
            self.query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            self.route = parts.path
            calls.append((method, parts.path, self.query, dict(self.headers)))
            fn = routes.get((method, parts.path))
            if fn is None:
                status, headers, body = 404, {}, b"not found"
            else:
                status, headers, body = fn(self)
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
                headers = {"Content-Type": "application/json", **headers}
            elif isinstance(body, str):
                body = body.encode()
                headers = {"Content-Type": "text/html; charset=utf-8", **headers}
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def do_PUT(self):
            self._go("PUT")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", calls
    finally:
        srv.shutdown()
        srv.server_close()
