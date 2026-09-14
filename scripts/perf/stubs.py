"""Loopback GitHub and AI substitutes with delay, timeout, disconnect, and cancel."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


def stub_payload(kind: str) -> dict[str, Any]:
    if kind == "github":
        return {
            "id": 1,
            "name": "fixture-plugin",
            "full_name": "perf-fixture/plugin",
            "description": "Isolated GitHub substitute",
            "stargazers_count": 1,
            "topics": ["counterstrikesharp", "cs2"],
        }
    return {"id": "fixture-completion", "choices": [{"message": {"content": "ok"}}]}


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        mode = str((query.get("mode") or ["ok"])[0])
        if parsed.path in {"/health", "/"}:
            self._send_json({"status": "ok"}, 200)
            return
        if mode == "disconnect":
            self.close_connection = True
            self.connection.close()
            return
        if mode == "timeout":
            threading.Event().wait(timeout=30)
            return
        if mode == "delay":
            threading.Event().wait(timeout=_delay_seconds(query))
        kind = "github" if parsed.path.startswith("/github") else "ai"
        self._send_json(stub_payload(kind), 200)

    def _send_json(self, payload: dict[str, Any], status: int) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _delay_seconds(query: dict[str, list[str]]) -> float:
    raw = (query.get("delay") or ["0.05"])[0]
    try:
        return min(5.0, max(0.0, float(raw)))
    except ValueError:
        return 0.05


class StubServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 18080) -> None:
        self._httpd = ThreadingHTTPServer((host, port), StubHandler)
        self._thread: threading.Thread | None = None

    @property
    def origin(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
