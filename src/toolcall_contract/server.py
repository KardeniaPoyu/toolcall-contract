"""Loopback-only adversarial SSE endpoint for testing real HTTP and SDK consumers."""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .fixtures import encode_sse, wire_events
from .model import Scenario


@contextmanager
def fixture_server(port=0, mutation=None, fragment_bytes=17, delay=0.0):
    if fragment_bytes < 1 or delay < 0:
        raise ValueError("invalid fragmentation parameters")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.server.requests += 1
            if mutation == "http-429":
                self.send_response(429)
                self.send_header("Content-Length", "0")
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.close_connection = True
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_048_576:
                    raise ValueError("invalid length")
                self.connection.settimeout(5)
                data = json.loads(self.rfile.read(length))
                if self.path.endswith("/chat/completions"):
                    protocol = "chat"
                elif self.path.endswith("/responses"):
                    protocol = "responses"
                elif self.path.endswith("/messages"):
                    protocol = "anthropic"
                else:
                    self.send_error(404)
                    return
                prompt = (
                    data["input"] if protocol == "responses" else data["messages"][0]["content"]
                )
                expected = json.loads(prompt.split("\n", 1)[1])
                scenario = Scenario.from_dict({"name": "server", "calls": expected})
                wire = encode_sse(wire_events(protocol, scenario, mutation), protocol)
            except (ValueError, TypeError, KeyError, IndexError):
                self.send_error(400, "Expected a toolcall-contract synthetic request")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(wire)))
            self.send_header("Connection", "close")
            self.end_headers()
            if mutation == "disconnect":
                wire = wire[: len(wire) // 2]
            try:
                for offset in range(0, len(wire), fragment_bytes):
                    self.wfile.write(wire[offset : offset + fragment_bytes])
                    self.wfile.flush()
                    if delay:
                        time.sleep(delay)
            except (OSError, TimeoutError):
                self.server.disconnections += 1
            finally:
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.requests = 0
    server.disconnections = 0
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
