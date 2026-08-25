"""Low-memory compatibility gateway for the former Clock Sync service."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request


TARGET_URL = os.getenv(
    "CLOCK_SYNC_TARGET_URL",
    "https://severaludo-production.up.railway.app/api/clock/report",
).strip()
REPORT_PATHS = {"/v1/clock", "/api/clock/report"}
MAX_REPORT_BYTES = 2_000_000


class ClockSyncGateway(BaseHTTPRequestHandler):
    server_version = "SeveralUDOClockGateway/1.0"

    def _respond(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, payload: dict) -> None:
        self._respond(status, json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") in {"/health", "/healthz"}:
            self._json(200, {"ok": True, "service": "SeveralUDO Clock Sync gateway"})
            return
        self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.split("?", 1)[0].rstrip("/") not in REPORT_PATHS:
            self._json(404, {"ok": False, "error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"ok": False, "error": "Invalid report length"})
            return
        if length <= 0 or length > MAX_REPORT_BYTES:
            self._json(413, {"ok": False, "error": "Clock report is empty or too large"})
            return
        body = self.rfile.read(length)
        headers = {
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "User-Agent": self.server_version,
        }
        authorization = self.headers.get("Authorization")
        if authorization:
            headers["Authorization"] = authorization
        outbound = request.Request(TARGET_URL, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(outbound, timeout=30) as response:
                payload = response.read()
                self._respond(response.status, payload, response.headers.get_content_type())
        except error.HTTPError as exc:
            payload = exc.read() or json.dumps({"ok": False, "error": "Tracker rejected the report"}).encode("utf-8")
            self._respond(exc.code, payload, exc.headers.get_content_type() if exc.headers else "application/json")
        except (error.URLError, TimeoutError, OSError):
            self._json(502, {"ok": False, "error": "The tracker receiver is temporarily unavailable"})

    def log_message(self, format: str, *args) -> None:
        # Railway already records request health and status; omit report details
        # so private tokens and Sim payloads can never appear in gateway logs.
        return


def serve() -> None:
    port = int(os.getenv("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), ClockSyncGateway).serve_forever()


if __name__ == "__main__":
    serve()
