"""Tiny localhost HTTP server that publishes the Orbiter GET state for the browser extension."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BRIDGE_PORT = 8765


class Bridge:
    def __init__(self, port: int = BRIDGE_PORT):
        self.port = port
        self._lock = threading.Lock()
        self._state = {"get": None, "accel": 1.0, "running": False, "paused": None, "seq": 0,
                       "offset": 0.0, "t": time.monotonic()}
        self._server: ThreadingHTTPServer | None = None
        self.error: str | None = None

    def update(
        self, get: float | None, accel: float, running: bool, seq: int, offset: float = 0.0,
        paused: bool | None = None,
    ) -> None:
        with self._lock:
            self._state = {
                "get": get, "accel": accel, "running": running, "paused": paused, "seq": seq,
                "offset": offset, "t": time.monotonic(),
            }

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self._state)
        if s["get"] is not None and s["running"]:
            s["get"] += (time.monotonic() - s["t"]) * s["accel"]
        s.pop("t")
        return s

    def start(self) -> None:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(bridge.snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as e:
            self.error = str(e)
            return
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
