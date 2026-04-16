#!/usr/bin/env python3
"""
远端 A2A Hub 联调脚本公共工具。

只使用 Python 标准库，避免在内网服务器上额外安装依赖。
"""

from __future__ import annotations

import json
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


默认平台地址 = os.environ.get("API_BASE", "http://127.0.0.1:1880")
默认租户 = "tenant_001"
默认签发密钥 = "241-issuer-secret"



class ApiError(RuntimeError):
    """HTTP 调用失败时抛出，保留状态码和响应体，便于定位问题。"""

    def __init__(self, method: str, url: str, status: int | None, body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"{method} {url} failed status={status}: {body}")


@dataclass
class ApiClient:
    """简单 HTTP client，统一处理 JSON、Bearer token 和自签证书。"""

    base_url: str
    token: str | None = None
    verify_tls: bool = False
    timeout: int = 30

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.ssl_context = None if self.verify_tls else ssl._create_unverified_context()

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)


def 打印分隔(title: str) -> None:
    print()
    print(f"========== {title} ==========")


def 打印成功(message: str) -> None:
    print(f"[成功] {message}")


def 打印提示(message: str) -> None:
    print(f"[提示] {message}")


def 签发服务账号令牌(
    api_base: str,
    issuer_secret: str,
    tenant_id: str,
    service_account_id: str = "platform-e2e-tester",
    component_type: str = "e2e_tester",
    scopes: list[str] | None = None,
    verify_tls: bool = False,
) -> str:
    """通过平台 API 签发 service account token。"""
    client = ApiClient(api_base, verify_tls=verify_tls)
    url = f"{client.base_url}/v1/service-accounts/token"
    payload = {
        "tenant_id": tenant_id,
        "service_account_id": service_account_id,
        "component_type": component_type,
        "scopes": scopes or ["messages:send"],
        "metadata": {"source": "remote-e2e"},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Service-Account-Issuer-Secret": issuer_secret,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30, context=client.ssl_context) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ApiError("POST", url, exc.code, raw) from exc

    if data.get("error"):
        raise ApiError("POST", url, None, json.dumps(data["error"], ensure_ascii=False))
    return data["data"]["access_token"]


def 创建上下文(client: ApiClient, source_agent_id: str, target_agent_id: str, title: str) -> str:
    """创建一次会话 context，后续消息任务都挂在这个 context 下。"""
    resp = client.post(
        "/v1/contexts",
        {
            "source_channel": "remote-e2e",
            "source_conversation_id": f"remote-e2e-{uuid.uuid4().hex[:12]}",
            "title": title,
            "metadata": {
                "source_agent_id": source_agent_id,
                "target_agent_id": target_agent_id,
                "test": "remote-e2e",
            },
        },
    )
    return resp["data"]["context_id"]


def 发送平台消息(
    client: ApiClient,
    context_id: str,
    target_agent_id: str,
    message: str,
    source_agent_id: str,
) -> str:
    """使用平台标准入口 /v1/messages/send 发送消息。"""
    resp = client.post(
        "/v1/messages/send",
        {
            "context_id": context_id,
            "target_agent_id": target_agent_id,
            "parts": [{"type": "text/plain", "text": message}],
            "metadata": {
                "source": "remote-platform-test",
                "source_agent_id": source_agent_id,
                "target_agent_id": target_agent_id,
            },
            "idempotency_key": f"remote-platform-{uuid.uuid4().hex}",
        },
    )
    return resp["data"]["task_id"]


def 发送Agent消息(
    client: ApiClient,
    context_id: str,
    target_agent_id: str,
    message: str,
    source_agent_id: str,
) -> str:
    """使用 Agent Link 入口 /v1/agent-link/messages/send 模拟 agent-to-agent。"""
    resp = client.post(
        "/v1/agent-link/messages/send",
        {
            "context_id": context_id,
            "target_agent_id": target_agent_id,
            "parts": [{"type": "text/plain", "text": message}],
            "metadata": {
                "source": "remote-agent-to-agent-test",
                "source_agent_id": source_agent_id,
                "target_agent_id": target_agent_id,
            },
            "idempotency_key": f"remote-agent-{uuid.uuid4().hex}",
        },
    )
    return resp["data"]["task_id"]


def 等待任务完成(client: ApiClient, task_id: str, wait_seconds: int, poll_interval: float) -> dict[str, Any]:
    """轮询任务状态，直到 COMPLETED/FAILED 等终态。"""
    deadline = time.time() + wait_seconds
    attempt = 0
    last_task: dict[str, Any] | None = None
    while time.time() < deadline:
        attempt += 1
        task = client.get(f"/v1/tasks/{task_id}")["data"]
        last_task = task
        print(f"第 {attempt} 次查询，任务状态：{task['state']}")
        if task["state"] in {"COMPLETED", "FAILED", "CANCELED", "EXPIRED"}:
            return task
        time.sleep(poll_interval)
    raise RuntimeError(f"等待任务完成超时，最后状态：{json.dumps(last_task, ensure_ascii=False)}")


def 读取任务消息(client: ApiClient, task_id: str) -> list[dict[str, Any]]:
    """读取任务消息列表，用于确认 assistant 回复内容。"""
    return client.get(f"/v1/tasks/{task_id}/messages")["data"]


def 读取公开清单(client: ApiClient) -> dict[str, Any]:
    """读取公开 Agent Link manifest。"""
    return client.get("/v1/agent-link/manifest")["data"]


def 公开自注册(
    client: ApiClient,
    agent_id: str,
    display_name: str,
    local_agent_id: str,
    owner_profile: dict[str, Any],
    agent_summary: str | None = None,
) -> dict[str, Any]:
    """通过公开入口完成 Agent Link 自注册，并返回响应 data。"""
    normalized_agent_id = agent_id if ":" in agent_id else f"openclaw:{agent_id}"
    resp = client.post(
        "/v1/agent-link/self-register",
        {
            "agent_id": normalized_agent_id,
            "display_name": display_name,
            "capabilities": {"analysis": True, "generic": True},
            "agent_summary": agent_summary or f"OpenClaw agent {local_agent_id}",
            "config_json": {
                "workspace": local_agent_id,
                "local_agent_id": local_agent_id,
                "agent_summary": agent_summary or f"OpenClaw agent {local_agent_id}",
            },
            "owner_profile": owner_profile,
        },
    )
    return resp["data"]


def 发布服务(
    client: ApiClient,
    handler_agent_id: str,
    title: str,
    summary: str,
    service_id: str | None = None,
) -> dict[str, Any]:
    """发布一个可公开发现的 service。"""
    payload = {
        "handler_agent_id": handler_agent_id,
        "title": title,
        "summary": summary,
        "visibility": "listed",
        "contact_policy": "auto_accept",
        "allow_agent_initiated_chat": True,
    }
    if service_id:
        payload["service_id"] = service_id
    return client.post("/v1/services", payload)["data"]


def 创建服务会话(
    client: ApiClient,
    service_id: str,
    opening_message: str,
    initiator_agent_id: str | None = None,
) -> dict[str, Any]:
    """通过 service 发起一轮新会话。"""
    payload = {"opening_message": opening_message}
    if initiator_agent_id:
        payload["initiator_agent_id"] = initiator_agent_id
    return client.post(f"/v1/services/{service_id}/threads", payload)["data"]


def 继续服务会话(
    client: ApiClient,
    thread_id: str,
    text: str,
    initiator_agent_id: str | None = None,
) -> dict[str, Any]:
    """对已有 service thread 继续发送下一轮消息。"""
    payload = {"text": text}
    if initiator_agent_id:
        payload["initiator_agent_id"] = initiator_agent_id
    return client.post(f"/v1/service-threads/{thread_id}/messages", payload)["data"]


def 读取服务会话消息(client: ApiClient, thread_id: str) -> list[dict[str, Any]]:
    """读取 service thread 消息列表。"""
    return client.get(f"/v1/service-threads/{thread_id}/messages")["data"]
