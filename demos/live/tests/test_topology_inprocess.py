"""In-memory HTTP handler integration; does not verify TCP transport.

Run independently of the real localhost suite:
    python -m unittest discover -s tests -p test_topology_inprocess.py -v

The shared scenarios from test_topology_http are exercised, while actual
BaseHTTPRequestHandler parsing, routing, authentication and JSON serialization
operate on BytesIO streams. No socket is opened and no runtime file is written.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_topology_http as localhost_tests


class MemorySocket:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()

    def makefile(self, *_args, **_kwargs):
        return self.request

    def sendall(self, data):
        self.response.extend(data)


class InMemoryTopologyHTTPTests(localhost_tests.TopologyHTTPTests):
    @classmethod
    def setUpClass(cls):
        cls.console = localhost_tests.Console()
        cls.token = cls.console.token
        cls.request_handler = localhost_tests.handler(cls.console)
        cls.request_handler.log_message = lambda *_args: None
        # This number is only parsed by Host validation; no port is bound.
        cls.server = SimpleNamespace(server_port=18765)

    @classmethod
    def tearDownClass(cls):
        cls.console.close()

    def http(self, method, path, payload=None, *, token=True):
        body = json.dumps(payload if payload is not None else {}).encode() if method == "POST" else b""
        headers = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1:18765", "Connection: close"]
        if method == "POST":
            headers += ["Content-Type: application/json", f"Content-Length: {len(body)}"]
            if token:
                headers += [f"X-Demo-Token: {self.token}"]
        stream = MemorySocket(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
        type(self).request_handler(stream, ("127.0.0.1", 12345), self.server)
        head, response = bytes(stream.response).split(b"\r\n\r\n", 1)
        return int(head.split(b" ", 2)[1]), json.loads(response)


if __name__ == "__main__":
    unittest.main(verbosity=2)
