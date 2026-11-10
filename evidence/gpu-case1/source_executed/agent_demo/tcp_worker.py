"""Small localhost TCP data plane used by the real-network experiment.

The receiver and each forwarding channel run in separate processes. Probes do
not increment payload counters; acknowledged payload IDs are deduplicated.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import socket
import socketserver
import sys
import threading

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_demo.common import atomic_json

MAX_LINE = 1024 * 1024


def exchange(port: int, request: dict, timeout: float = 2.0) -> dict:
    encoded = json.dumps(request, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_LINE:
        raise ValueError("Request exceeds protocol limit")
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(encoded)
        with sock.makefile("rb") as reader:
            line = reader.readline(MAX_LINE + 1)
    if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
        raise ValueError("Incomplete or oversized TCP response")
    result = json.loads(line)
    if not isinstance(result, dict):
        raise ValueError("TCP response must be an object")
    return result


class Receiver:
    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.lock = threading.Lock()
        self.chunks: dict[str, tuple[str, int]] = {}
        self.duplicate_requests = 0
        self.rejected_requests = 0
        self._save()

    def stats(self) -> dict:
        return {"unique_chunks": len(self.chunks),
                "unique_bytes": sum(size for _, size in self.chunks.values()),
                "duplicate_requests": self.duplicate_requests,
                "rejected_requests": self.rejected_requests}

    def _save(self) -> None:
        atomic_json(self.state_path, self.stats())

    def request(self, request: dict) -> dict:
        if request.get("op") == "ping":
            return {"ok": True, "kind": "receiver", "probe": True}
        with self.lock:
            if request.get("op") == "stats":
                return {"ok": True, **self.stats()}
            if request.get("op") != "put":
                return {"ok": False, "error": "unknown_operation"}
            try:
                chunk_id = request["chunk_id"]
                if not isinstance(chunk_id, str) or not 1 <= len(chunk_id) <= 128:
                    raise ValueError("Invalid chunk ID")
                raw = base64.b64decode(request["payload"], validate=True)
                digest = hashlib.sha256(raw).hexdigest()
                if digest != request.get("sha256"):
                    raise ValueError("Payload checksum mismatch")
                previous = self.chunks.get(chunk_id)
                if previous is not None and previous != (digest, len(raw)):
                    raise ValueError("Chunk ID reused for different data")
                duplicate = previous is not None
                if duplicate:
                    self.duplicate_requests += 1
                else:
                    self.chunks[chunk_id] = (digest, len(raw))
                self._save()
                return {"ok": True, "chunk_id": chunk_id, "sha256": digest,
                        "bytes": len(raw), "duplicate": duplicate,
                        "unique_chunks": len(self.chunks)}
            except (KeyError, ValueError, TypeError) as exc:
                self.rejected_requests += 1
                self._save()
                return {"ok": False, "error": str(exc)}


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("receiver", "proxy"))
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--target-port", type=int)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    if args.mode == "receiver" and args.state is None:
        parser.error("receiver needs --state")
    if args.mode == "proxy" and not (args.target_port and 0 < args.target_port < 65536):
        parser.error("proxy needs a valid --target-port")
    receiver = Receiver(args.state) if args.mode == "receiver" else None

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(args.timeout)
            try:
                line = self.rfile.readline(MAX_LINE + 1)
                if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise ValueError("Incomplete or oversized request")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Request must be an object")
                response = receiver.request(request) if receiver else exchange(
                    args.target_port, request, args.timeout)
            except (OSError, ValueError, TypeError) as exc:
                response = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
            try:
                self.wfile.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
            except OSError:
                pass  # A lost acknowledgement is handled by chunk-ID retry.

    try:
        with Server(("127.0.0.1", 0), Handler) as server:
            atomic_json(args.ready, {"ready": True, "host": "127.0.0.1",
                                     "port": server.server_address[1]})
            server.serve_forever(poll_interval=0.1)
    except OSError as exc:
        atomic_json(args.ready, {"ready": False, "error": str(exc), "errno": exc.errno})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
