"""The executable the backend runs for QWEN_PROVIDER=command
(see app/pipeline/providers.py): request JSON on stdin, response JSON on
stdout. It forwards both to the resident daemon and never falls back.

    QWEN_COMMAND_JSON=["/abs/venv/bin/python", "-m", "qwen_runner.client"]
"""

from __future__ import annotations

import json
import os
import socket
import sys


def main() -> int:
    request = sys.stdin.buffer.read()
    path = os.environ.get("QWEN_SOCKET", "/tmp/qwen-runner.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        try:
            conn.connect(path)
        except OSError as exc:
            print(f"Qwen daemon not reachable at {path}: {exc}", file=sys.stderr)
            return 1
        conn.sendall(request)
        conn.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := conn.recv(1 << 16):
            chunks.append(chunk)
    reply = json.loads(b"".join(chunks) or b"{}")
    if "error" in reply or "events" not in reply:
        print(f"Qwen daemon error: {reply.get('error', 'empty reply')}", file=sys.stderr)
        return 1
    json.dump(reply, sys.stdout, allow_nan=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
