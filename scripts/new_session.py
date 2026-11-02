#!/usr/bin/env python3
"""Single-thread Codex 0.166 app-server dispatcher; Python 3.10 standard library."""
import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parent
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def safe(value):
    """Do not persist raw diagnostics, credentials, or proxy URL userinfo."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+(?::[^\s/@]*)?@",
                  r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(
        r'''(?ix)((?:["']?)(?:[\w-]*(?:api[_-]?key|token|secret|password|proxy[_-]?(?:user(?:name)?|account|pass(?:word)?)))["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}&]+)''',
        r"\1[REDACTED]", text)
    for key, value in os.environ.items():
        if len(value) >= 8 and re.search(r"(?i)(key|token|secret|password|proxy)", key):
            text = text.replace(value, "[REDACTED]")
    return text


def say(value, *, error=False):
    print(safe(value), file=sys.stderr if error else sys.stdout, flush=True)


def find_codex(env):
    base = Path(os.environ["LOCALAPPDATA"]) / "OpenAI/Codex/bin"
    candidates = sorted(base.glob("*/codex.exe"),
                        key=lambda p: p.stat().st_mtime_ns, reverse=True)
    failures = []
    for path in candidates:
        try:
            result = subprocess.run([str(path), "--version"], env=env,
                                    capture_output=True, encoding="utf-8", errors="replace",
                                    timeout=10, creationflags=HIDDEN, check=False)
            if result.returncode == 0 and result.stdout.strip():
                return path, result.stdout.strip()
            failures.append(f"{path}: {result.stderr.strip() or result.returncode}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{path}: {exc}")
    raise RuntimeError("没有找到能运行 --version 的 codex.exe；" + "; ".join(failures))


class RPC:
    def __init__(self, exe, cwd, env, deadline):
        self.deadline = deadline
        self.step = "启动 app-server"
        self.events = queue.Queue()
        self.pending = deque()
        self.stderr = deque(maxlen=20)
        self.denied = []
        self.next_id = 0
        self.process = subprocess.Popen(
            [str(exe), "app-server", "--stdio"], cwd=str(cwd), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=HIDDEN)
        self.readers = [threading.Thread(target=self._stdout, daemon=True),
                        threading.Thread(target=self._stderr, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _stdout(self):
        try:
            for line in self.process.stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("JSON-RPC 消息必须是对象")
                    self.events.put(message)
                except ValueError as exc:
                    self.events.put(RuntimeError(f"无效 JSON-RPC 行: {exc}"))
                    return
        finally:
            self.events.put(EOFError("app-server stdout 已关闭"))

    def _stderr(self):
        for line in self.process.stderr:
            self.stderr.append(safe(line.rstrip()))

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def receive(self, deadline):
        # Blocking reads and queue waits, never status polling or request retries.
        while True:
            remaining = min(self.deadline, deadline) - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待服务端消息超时")
            try:
                message = self.events.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("等待服务端消息超时") from None
            if isinstance(message, BaseException):
                raise message
            if "method" in message and "id" in message:
                self.deny(message)
                continue
            return message

    def deny(self, message):
        method = message["method"]
        record = {"method": method, "requestId": message["id"], "decision": "denied"}
        self.denied.append(record)  # Never record approval arguments or commands.
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            result = {"decision": "decline"}
        elif method in ("execCommandApproval", "applyPatchApproval"):
            result = {"decision": {"denied": {"rejection": "Dispatcher denies all approvals"}}}
        elif method == "item/permissions/requestApproval":
            result = {"permissions": {}, "scope": "turn"}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "decline"}
        else:
            self.send({"id": message["id"], "error": {
                "code": -32601, "message": "Dispatcher refuses server-initiated requests"}})
            return
        self.send({"id": message["id"], "result": result})

    def request(self, method, params):
        self.step = method
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = min(self.deadline, time.monotonic() + 60)
        while True:
            message = self.receive(deadline)
            if "method" in message:
                self.pending.append(message)
                continue
            if type(message.get("id")) is not int or message["id"] != request_id:
                raise RuntimeError(f"响应 id 不匹配，期望 {request_id}，收到 {message.get('id')!r}")
            if "error" in message:
                raise RuntimeError("服务端错误: " + safe(message["error"]))
            if "result" not in message:
                raise RuntimeError("响应缺少 result/error")
            return message["result"]

    def notification(self):
        message = self.pending.popleft() if self.pending else self.receive(self.deadline)
        if "method" not in message:
            raise RuntimeError("没有待响应请求时收到响应")
        return message

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for reader in self.readers:
            reader.join(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()


def verify(model, effort, args):
    if (model, effort) != (args.model, args.effort):
        raise RuntimeError(f"实际 model/effort={model!r}/{effort!r}，"
                           f"要求={args.model!r}/{args.effort!r}；已停止，不回退")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("cwd", "model", "effort", "name"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--message-file", type=Path)
    parser.add_argument("--sandbox", choices=("workspace-write", "read-only"),
                        default="workspace-write")
    parser.add_argument("--timeout-min", type=float, default=120)
    parser.add_argument("--check", action="store_true", help="只握手及列模型，不创建会话")
    args = parser.parse_args()
    args.cwd = Path(args.cwd).resolve(strict=True)
    if not args.cwd.is_dir():
        parser.error("--cwd 必须是已存在的目录")
    if not all(getattr(args, key).strip() for key in ("model", "effort", "name")):
        parser.error("model、effort 和 name 不能为空")
    if not math.isfinite(args.timeout_min) or args.timeout_min <= 0:
        parser.error("--timeout-min 必须是有限正数")
    if args.check and args.message_file:
        parser.error("--check 不能与 --message-file 同用")
    args.message = None
    if args.message_file:
        args.message = args.message_file.read_text(encoding="utf-8-sig")
        if not args.message.strip():
            parser.error("消息文件不能为空")
    return args


def main():
    rpc = None
    step = "参数校验"
    thread_id = None
    answers = {}
    outcome = "未完成"
    code = 1
    try:
        args = arguments()
        deadline = time.monotonic() + args.timeout_min * 60
        # Preserve normal auth/config, but never inherit internal client impersonation.
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CODEX_INTERNAL_")}
        step = "查找 codex.exe / --version"
        exe, version = find_codex(env)
        say(f"codex.exe: {exe}\n版本: {version}")
        step = "启动 app-server"
        rpc = RPC(exe, args.cwd, env, deadline)
        rpc.request("initialize", {
            "clientInfo": {"name": "claude_local_dispatcher", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True}})
        rpc.step = "initialized"
        rpc.send({"method": "initialized", "params": {}})
        # Cursor pagination is a finite catalog traversal, not status polling.
        models, cursors, cursor = [], set(), None
        while True:
            params = {"includeHidden": True, "limit": 1000}
            if cursor is not None:
                params["cursor"] = cursor
            page = rpc.request("model/list", params)
            models.extend(page["data"])
            cursor = page.get("nextCursor")
            if cursor is None:
                break
            if cursor in cursors:
                raise RuntimeError("model/list 返回重复游标；停止，不重试")
            cursors.add(cursor)
        rpc.step = "model/list 校验"
        for model in models:
            efforts = [e["reasoningEffort"] for e in model["supportedReasoningEfforts"]]
            say(f"model={model['model']}  id={model['id']}  efforts={','.join(efforts)}")
        matches = [m for m in models if m["model"] == args.model]
        if not matches:
            raise RuntimeError(f"model/list 未列出精确 model={args.model!r}；请使用 model 字段")
        if not any(args.effort in [e["reasoningEffort"] for e in m["supportedReasoningEfforts"]]
                   for m in matches):
            raise RuntimeError(f"model={args.model!r} 不支持 effort={args.effort!r}")
        if args.check:
            outcome = "--check 通过；未创建会话、未发送消息"
        else:
            result = rpc.request("thread/start", {
                "cwd": str(args.cwd), "model": args.model,
                "allowProviderModelFallback": False,
                "config": {"model_reasoning_effort": args.effort},
                "sandbox": args.sandbox, "approvalPolicy": "on-request",
                "approvalsReviewer": "user", "ephemeral": False})
            thread = result["thread"]
            candidate_id = thread["id"]
            uuid.UUID(candidate_id)  # Server IDs must not escape the output directory.
            if str(uuid.UUID(candidate_id)) != candidate_id:
                raise RuntimeError("服务端 threadId 不是规范 UUID")
            thread_id = candidate_id
            say(f"threadId: {thread_id}")
            say(f"会话 jsonl: {thread.get('path') or '服务端未返回路径'}")
            rpc.step = "thread/start 核对"
            verify(result.get("model"), result.get("reasoningEffort"), args)
            if thread.get("ephemeral") is not False:
                raise RuntimeError("服务端没有确认非临时会话")
            if result.get("approvalPolicy") != "on-request" or result.get("approvalsReviewer") != "user":
                raise RuntimeError("服务端审批策略与要求不一致")
            expected_sandbox = {"workspace-write": "workspaceWrite", "read-only": "readOnly"}[args.sandbox]
            if result.get("sandbox", {}).get("type") != expected_sandbox:
                raise RuntimeError("服务端实际 sandbox 与要求不一致")
            if Path(result["cwd"]).resolve() != args.cwd:
                raise RuntimeError("服务端实际 cwd 与要求不一致")
            rpc.request("thread/name/set", {"threadId": thread_id, "name": args.name})
            rpc.request("thread/settings/update", {
                "threadId": thread_id, "model": args.model, "effort": args.effort})
            readback = rpc.request("thread/read", {
                "threadId": thread_id, "includeTurns": False})
            rpc.step = "thread/read 设置核对"
            actual = readback["thread"]
            model, effort = actual.get("model"), actual.get("reasoningEffort")
            # Schema: null means unavailable; still reject any available mismatch.
            if ((model is not None and model != args.model)
                    or (effort is not None and effort != args.effort)):
                verify(model, effort, args)
            if model is not None and effort is not None:
                say(f"服务端已确认 model={model} effort={effort}；核对依据：thread/read")
            else:
                # UpdateResponse is an empty object: RPC success is the acknowledgement.
                say(f"已提交 model={args.model} effort={args.effort}；核对依据：update 响应"
                    "（thread/read 设置字段缺失或为 null，未完整读回核对）")
            outcome = "已创建会话并设置名字、模型及推理等级"
            if args.message is not None:
                started = rpc.request("turn/start", {
                    "threadId": thread_id, "model": args.model, "effort": args.effort,
                    "input": [{"type": "text", "text": args.message}]})
                turn_id = started["turn"]["id"]
                rpc.step = "等待 turn/completed"
                while True:
                    event = rpc.notification()
                    params = event.get("params", {})
                    if params.get("threadId") != thread_id:
                        continue
                    if event["method"] == "item/completed" and params.get("turnId") == turn_id:
                        item = params["item"]
                        if item.get("type") == "agentMessage" and item.get("delivery") != "async":
                            answers[item["id"]] = item
                    if event["method"] == "thread/settings/updated":
                        # Opportunistic check only; never wait for this notification.
                        settings = params["threadSettings"]
                        verify(settings.get("model"), settings.get("effort"), args)
                    if event["method"] == "turn/completed" and params["turn"]["id"] == turn_id:
                        turn = params["turn"]
                        for item in turn.get("items", []):
                            if item.get("type") == "agentMessage" and item.get("delivery") != "async":
                                answers[item["id"]] = item
                        if turn["status"] != "completed":
                            raise RuntimeError("轮次未成功完成: " + safe({
                                "status": turn["status"], "error": turn.get("error")}))
                        outcome = "本轮已完成"
                        break
        code = 0
    except KeyboardInterrupt:
        outcome = "用户中断；未重试"
        code = 130
        say(f"失败步骤: {rpc.step if rpc else step}；{outcome}", error=True)
    except Exception as exc:
        outcome = str(exc)
        say(f"失败步骤: {rpc.step if rpc else step}；{outcome}", error=True)
    finally:
        if rpc:
            try:
                rpc.close()
            except Exception as exc:
                code = 1
                say(f"失败步骤: 关闭 app-server；{exc}", error=True)
            if code and rpc.stderr:
                say("服务端 stderr（末尾，已脱敏）:\n" + "\n".join(rpc.stderr), error=True)
            say(f"已拒绝服务端请求: {len(rpc.denied)}")
            for denial in rpc.denied:
                say(denial)
            if thread_id and args.message is not None:
                try:
                    output = ROOT / "输出" / f"{thread_id}.md"
                    output.parent.mkdir(exist_ok=True)
                    finals = [i["text"] for i in answers.values() if i.get("phase") == "final_answer"]
                    unknown = [i["text"] for i in answers.values() if i.get("phase") is None]
                    body = "\n\n".join(finals or unknown[-1:])
                    if not body:
                        body = "（未收到助手最终回复）"
                    report = f"\n\n---\n运行结果：{outcome}\n已拒绝服务端请求：{len(rpc.denied)}\n"
                    report += "\n".join(json.dumps(d, ensure_ascii=False) for d in rpc.denied)
                    output.write_text(safe(body + report) + "\n", encoding="utf-8")
                    say(f"输出: {output}")
                except Exception as exc:
                    code = 1
                    say(f"失败步骤: 保存输出；{exc}", error=True)
        if code == 0:
            say(outcome)
    return code


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
