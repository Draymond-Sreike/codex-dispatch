#!/usr/bin/env python3
"""Send a message to (or steer a running turn of) an existing Codex thread through the shared
local app-server daemon (`codex app-server proxy`, WebSocket framing). Python 3.10 stdlib."""
import argparse
import base64
import json
import os
import struct
import subprocess
import sys
import time
import uuid

from new_session import HIDDEN, find_codex, safe, say


class WS:
    """Minimal WebSocket client over the stdio of `codex app-server proxy`."""

    def __init__(self, exe, env):
        self.p = subprocess.Popen([str(exe), "app-server", "proxy"], env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  creationflags=HIDDEN)
        key = base64.b64encode(os.urandom(16)).decode()
        self.p.stdin.write(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                            "Sec-WebSocket-Version: 13\r\n\r\n" % key).encode())
        self.p.stdin.flush()
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            byte = self.p.stdout.read(1)
            if not byte:
                raise RuntimeError("daemon 没有响应；先运行 codex app-server daemon start")
            head += byte
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败: " + safe(head.decode(errors="replace")[:200]))
        self.next_id = 0

    def _write(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else (b"\xfe" + struct.pack(">H", n) if n < 65536
                                                    else b"\xff" + struct.pack(">Q", n))
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.p.stdin.write(head + mask + body)
        self.p.stdin.flush()

    def _read(self, n):
        data = self.p.stdout.read(n)
        if len(data) != n:
            raise EOFError("daemon 连接已关闭")
        return data

    def recv(self):
        text = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            data = self._read(n)
            op = b0 & 0x0F
            if op == 9:
                self._write(10, data)
            elif op == 8:
                raise EOFError("daemon 关闭了连接")
            elif op in (1, 0):
                text += data
                if b0 & 0x80:
                    return json.loads(text.decode("utf-8"))

    def request(self, method, params):
        self.next_id += 1
        rid = self.next_id
        self._write(1, json.dumps({"id": rid, "method": method, "params": params},
                                  ensure_ascii=False).encode("utf-8"))
        while True:
            msg = self.recv()
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise RuntimeError("服务端错误: " + safe(msg["error"]))
                return msg.get("result", {})

    def notify(self, method):
        self._write(1, json.dumps({"method": method, "params": {}}).encode())

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", required=True, help="会话 UUID")
    parser.add_argument("--message-file", required=True)
    parser.add_argument("--steer", metavar="TURN_ID", help="对正在运行的 turn 插话，而不是开新一轮")
    parser.add_argument("--wait", action="store_true", help="等本轮完成（默认发出就返回）")
    parser.add_argument("--timeout-min", type=float, default=30)
    args = parser.parse_args()
    args.thread = str(uuid.UUID(args.thread))
    with open(args.message_file, encoding="utf-8-sig") as handle:
        args.message = handle.read()
    if not args.message.strip():
        parser.error("消息文件不能为空")
    return args


def main():
    args = arguments()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CODEX_INTERNAL_")}
    exe, _ = find_codex(env)
    ws = WS(exe, env)
    deadline = time.monotonic() + args.timeout_min * 60
    try:
        ws.request("initialize", {"clientInfo": {"name": "claude_local_dispatcher", "version": "0.2.0"},
                                  "capabilities": {"experimentalApi": True}})
        ws.notify("initialized")
        ws.request("thread/resume", {"threadId": args.thread})
        text = [{"type": "text", "text": args.message}]
        if args.steer:
            ws.request("turn/steer", {"threadId": args.thread, "expectedTurnId": args.steer,
                                      "input": text})
            say(f"已插话 turn={args.steer}")
            return 0
        started = ws.request("turn/start", {"threadId": args.thread, "input": text})
        turn_id = started["turn"]["id"]
        say(f"已发出 turn={turn_id}")
        while args.wait:
            if time.monotonic() > deadline:
                raise TimeoutError("等待本轮完成超时")
            msg = ws.recv()
            params = msg.get("params", {})
            if msg.get("method") == "turn/completed" and params["turn"]["id"] == turn_id:
                say(f"本轮结束: {params['turn']['status']}")
                break
        return 0
    except Exception as exc:
        say(f"失败: {exc}", error=True)
        return 1
    finally:
        ws.close()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
