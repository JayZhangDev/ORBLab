"""Strategy stats dashboard on http://localhost:8767

    python -m orblab.dashboard [--port 8767]

Serves one static page plus two JSON endpoints. The journal endpoint reads
SQLite on every request, so win rate and average RR update as paper trades close.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orblab import journal

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results" / "backtest.json"


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                                   # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send((HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/backtest":
            if RESULTS.exists():
                self._send(RESULTS.read_bytes(), "application/json")
            else:
                self._send(b'{"error":"run python -m orblab.backtest first"}', "application/json", 404)
        elif path == "/api/journal":
            self._send(json.dumps(journal.snapshot(), default=str).encode(), "application/json")
        else:
            self._send(b"not found", "text/plain", 404)

    def log_message(self, *args) -> None:                       # keep the console quiet
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8767)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)   # localhost only
    print(f"Dashboard: http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
