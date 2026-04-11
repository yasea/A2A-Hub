#!/usr/bin/env python3
"""
纯平台 API 消息联调脚本。

目标：
1. 通过平台 API 创建 context
2. 通过平台 API 向 openclaw:mia 发送消息
3. 通过平台 API 轮询任务状态
4. 通过平台 API 读取任务消息，确认 assistant 回复

说明：
- 脚本不直接写数据库，也不读取 OpenClaw 本地 session 文件。
- 支持 service-account / agent-token 两种生产化调用身份。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def 打印分隔(title: str) -> None:
    print()
    print(f"========== {title} ==========")


def 打印成功(message: str) -> None:
    print(f"[成功] {message}")


def 打印提示(message: str) -> None:
    print(f"[提示] {message}")


def 打印错误(message: str) -> None:
    print(f"[错误] {message}", file=sys.stderr)


class ApiError(RuntimeError):
    def __init__(self, method: str, url: str, status: int | None, body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} failed status={status}: {body}")


@dataclass
class ApiClient:
    base_url: str
    token: str
    verify_tls: bool = False
    timeout: int = 30

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.ssl_context = None if self.verify_tls else ssl._create_unverified_context()

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise ApiError(method, url, exc.code, raw) from exc
        except URLError as exc:
            raise ApiError(method, url, None, str(exc)) from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(method, url, None, f"响应不是 JSON: {raw}") from exc

        if data.get("error"):
            raise ApiError(method, url, None, json.dumps(data["error"], ensure_ascii=False))
        return data

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过平台 API 向 OpenClaw agent 发送消息并接收回复")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:1880"))
    parser.add_argument("--tenant-id", default=os.environ.get("TENANT_ID", "tenant_001"))
    parser.add_argument("--target-agent-id", default=os.environ.get("AGENT_ID", "openclaw:mia"))
    parser.add_argument(
        "--message",
        default=os.environ.get("MESSAGE_TEXT", "请以 mia 的身份回复一句：平台 API 消息测试成功"),
    )
    parser.add_argument("--source-agent-id", default=os.environ.get("SOURCE_AGENT_ID", "platform:tester"))
    parser.add_argument("--title", default=os.environ.get("CONTEXT_TITLE", "平台 API 消息联调"))
    parser.add_argument("--wait-seconds", type=int, default=int(os.environ.get("TASK_WAIT_SECONDS", "120")))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("TASK_POLL_INTERVAL", "1")))
    parser.add_argument("--jwt", default=os.environ.get("HUB_JWT", ""))
    parser.add_argument(
        "--auth-mode",
        choices=["service-account", "agent-token"],
        default=os.environ.get("AUTH_MODE", "service-account"),
    )
    parser.add_argument("--service-account-id", default=os.environ.get("SERVICE_ACCOUNT_ID", "platform-message-router"))
    parser.add_argument("--component-type", default=os.environ.get("COMPONENT_TYPE", "message_router"))
    parser.add_argument("--issuer-secret", default=os.environ.get("SERVICE_ACCOUNT_ISSUER_SECRET", ""))
    parser.add_argument("--agent-token", default=os.environ.get("AGENT_TOKEN", ""))
    parser.add_argument("--verify-tls", action="store_true", help="校验证书。默认关闭，适配内网 HTTP 或自签证书")
    return parser.parse_args()


def issue_service_account_token(args: argparse.Namespace) -> str:
    if args.jwt:
        return args.jwt
    if not args.issuer_secret:
        raise RuntimeError("service-account 模式需要 --issuer-secret 或 SERVICE_ACCOUNT_ISSUER_SECRET")
    ssl_context = None if args.verify_tls else ssl._create_unverified_context()
    payload = {
        "tenant_id": args.tenant_id,
        "service_account_id": args.service_account_id,
        "component_type": args.component_type,
        "scopes": ["messages:send"],
        "metadata": {"source_agent_id": args.source_agent_id},
    }
    req = Request(
        f"{args.api_base.rstrip('/')}/v1/service-accounts/token",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Service-Account-Issuer-Secret": args.issuer_secret,
        },
        method="POST",
    )
    with urlopen(req, timeout=30, context=ssl_context) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(json.dumps(data["error"], ensure_ascii=False))
    return data["data"]["access_token"]


def resolve_token(args: argparse.Namespace) -> str:
    if args.auth_mode == "service-account":
        return issue_service_account_token(args)
    if args.auth_mode == "agent-token":
        token = args.agent_token or args.jwt
        if not token:
            raise RuntimeError("agent-token 模式需要 --agent-token/AGENT_TOKEN 或 --jwt/HUB_JWT")
        return token
    raise RuntimeError(f"不支持的 auth-mode: {args.auth_mode}")


def create_context(client: ApiClient, args: argparse.Namespace) -> str:
    resp = client.post(
        "/v1/contexts",
        {
            "source_channel": "platform-api-test",
            "source_conversation_id": f"api-test-{uuid.uuid4().hex[:12]}",
            "title": args.title,
            "metadata": {
                "source_agent_id": args.source_agent_id,
                "target_agent_id": args.target_agent_id,
                "test": "agent-message-api",
            },
        },
    )
    context_id = resp["data"]["context_id"]
    打印成功(f"context 已创建: {context_id}")
    return context_id


def send_message(client: ApiClient, args: argparse.Namespace, context_id: str) -> str:
    idempotency_key = f"api-{args.source_agent_id}-{args.target_agent_id}-{uuid.uuid4().hex}"
    path = "/v1/agent-link/messages/send" if args.auth_mode == "agent-token" else "/v1/messages/send"
    resp = client.post(
        path,
        {
            "context_id": context_id,
            "target_agent_id": args.target_agent_id,
            "parts": [{"type": "text/plain", "text": args.message}],
            "metadata": {
                "source": "platform-api-test",
                "source_agent_id": args.source_agent_id,
                "target_agent_id": args.target_agent_id,
            },
            "idempotency_key": idempotency_key,
        },
    )
    task_id = resp["data"]["task_id"]
    打印成功(f"消息已发送: task_id={task_id}")
    return task_id


def wait_task(client: ApiClient, task_id: str, wait_seconds: int, poll_interval: float) -> dict[str, Any]:
    deadline = time.time() + wait_seconds
    attempt = 0
    last_task: dict[str, Any] | None = None
    while time.time() < deadline:
        attempt += 1
        resp = client.get(f"/v1/tasks/{task_id}")
        task = resp["data"]
        last_task = task
        print(f"第 {attempt} 次查询，任务状态：{task['state']}")
        if task["state"] in {"COMPLETED", "FAILED", "CANCELED", "EXPIRED"}:
            return task
        time.sleep(poll_interval)
    raise RuntimeError(f"等待任务完成超时，最后状态: {json.dumps(last_task, ensure_ascii=False)}")


def load_messages(client: ApiClient, task_id: str) -> list[dict[str, Any]]:
    resp = client.get(f"/v1/tasks/{task_id}/messages")
    return resp["data"]


def main() -> int:
    args = parse_args()

    打印分隔("步骤 1：准备调用 token")
    token = resolve_token(args)
    打印成功(f"调用 token 已准备: auth_mode={args.auth_mode}")

    client = ApiClient(args.api_base, token, verify_tls=args.verify_tls)

    打印分隔("步骤 2：通过平台 API 创建 context")
    context_id = create_context(client, args)

    打印分隔("步骤 3：模拟平台或其他 agent 发送消息")
    print(f"source_agent_id={args.source_agent_id}")
    print(f"target_agent_id={args.target_agent_id}")
    print(f"message={args.message}")
    task_id = send_message(client, args, context_id)

    打印分隔("步骤 4：通过平台 API 等待 mia 回复")
    task = wait_task(client, task_id, args.wait_seconds, args.poll_interval)
    print(json.dumps(task, ensure_ascii=False, indent=2))
    if task["state"] != "COMPLETED":
        打印错误(f"任务未成功完成: {task['state']}")
        return 1

    打印分隔("步骤 5：通过平台 API 接收消息列表")
    messages = load_messages(client, task_id)
    for message in messages:
        print(
            f"[{message['seq_no']}] {message['role']}: "
            f"{message.get('content_text') or json.dumps(message.get('content_json'), ensure_ascii=False)}"
        )

    assistant_messages = [message for message in messages if message["role"] == "assistant"]
    if not assistant_messages:
        打印错误("任务已完成，但平台 API 未返回 assistant 消息")
        return 1

    latest_reply = assistant_messages[-1].get("content_text") or ""
    打印分隔("完成")
    打印成功(f"已通过平台 API 收到 {args.target_agent_id} 回复: {latest_reply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
