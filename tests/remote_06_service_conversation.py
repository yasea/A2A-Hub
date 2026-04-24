#!/bin/sh
"exec" "python3" "$0" "$@"
"""
服务发布与跨租户多轮对话联调脚本。

流程：
1. provider 租户发布一个公开 service，绑定本租户的 runtime agent
2. consumer 租户发现该 service
3. consumer 发起 service thread，并发送第一轮消息
4. 轮询 thread 消息，确认 service 背后的 agent 回复
5. 可选：继续第二轮多轮对话
"""

import argparse
import json
import os
import sys
import time

from remote_api_common import (
    ApiClient,
    默认平台地址,
    默认签发密钥,
    创建服务会话,
    发布服务,
    打印分隔,
    打印成功,
    继续服务会话,
    读取服务会话消息,
    签发服务账号令牌,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="联调 service directory 与 service thread 多轮对话")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE") or os.environ.get("API") or 默认平台地址)
    parser.add_argument("--issuer-secret", default=os.environ.get("SERVICE_ACCOUNT_ISSUER_SECRET", 默认签发密钥))
    parser.add_argument("--provider-tenant-id", default=os.environ.get("PROVIDER_TENANT_ID", "owner_provider"))
    parser.add_argument("--consumer-tenant-id", default=os.environ.get("CONSUMER_TENANT_ID", "owner_consumer"))
    parser.add_argument("--handler-agent-id", default=os.environ.get("HANDLER_AGENT_ID", "openclaw:mia"))
    parser.add_argument("--initiator-agent-id", default=os.environ.get("INITIATOR_AGENT_ID", "openclaw:ava"))
    parser.add_argument("--service-id", default=os.environ.get("SERVICE_ID", ""))
    parser.add_argument("--first-message", default=os.environ.get("FIRST_MESSAGE", "请只回复：REMOTE_SERVICE_THREAD_OK"))
    parser.add_argument("--first-expect", default=os.environ.get("FIRST_EXPECT", "REMOTE_SERVICE_THREAD_OK"))
    parser.add_argument("--second-message", default=os.environ.get("SECOND_MESSAGE", ""))
    parser.add_argument("--second-expect", default=os.environ.get("SECOND_EXPECT", ""))
    parser.add_argument("--wait-seconds", type=int, default=int(os.environ.get("WAIT_SECONDS", "180")))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("POLL_INTERVAL", "2")))
    parser.add_argument("--verify-tls", action="store_true", help="校验 TLS 证书。HTTP 或自签证书场景不需要开启")
    return parser.parse_args()


def 等待回复(client: ApiClient, thread_id: str, expect: str, wait_seconds: int, poll_interval: float) -> str:
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        messages = 读取服务会话消息(client, thread_id)
        assistant_messages = [item for item in messages if item["role"] == "assistant"]
        for item in assistant_messages[::-1]:
            reply = item.get("content_text") or ""
            if not expect or expect in reply:
                return reply
        time.sleep(poll_interval)
    raise RuntimeError(f"等待 service thread 回复超时，thread_id={thread_id}")


def main() -> int:
    args = parse_args()

    打印分隔("步骤 1：准备 provider 和 consumer token")
    provider_token = 签发服务账号令牌(
        api_base=args.api_base,
        issuer_secret=args.issuer_secret,
        tenant_id=args.provider_tenant_id,
        service_account_id="provider-service-publisher",
        component_type="service_publisher",
        scopes=["messages:send"],
        verify_tls=args.verify_tls,
    )
    consumer_token = 签发服务账号令牌(
        api_base=args.api_base,
        issuer_secret=args.issuer_secret,
        tenant_id=args.consumer_tenant_id,
        service_account_id="consumer-service-tester",
        component_type="service_tester",
        scopes=["messages:send"],
        verify_tls=args.verify_tls,
    )
    provider = ApiClient(args.api_base, token=provider_token, verify_tls=args.verify_tls)
    consumer = ApiClient(args.api_base, token=consumer_token, verify_tls=args.verify_tls)
    打印成功("provider/consumer token 已准备")

    打印分隔("步骤 2：provider 发布 service")
    service = 发布服务(
        provider,
        handler_agent_id=args.handler_agent_id,
        title="Remote Service Conversation",
        summary="用于验证跨租户 service thread 多轮对话",
        service_id=args.service_id or None,
    )
    print(json.dumps(service, ensure_ascii=False, indent=2))

    打印分隔("步骤 3：consumer 读取服务详情")
    detail = consumer.get(f"/v1/services/{service['service_id']}")["data"]
    print(json.dumps(detail, ensure_ascii=False, indent=2))

    打印分隔("步骤 4：consumer 发起第一轮对话")
    thread_resp = 创建服务会话(
        consumer,
        service_id=service["service_id"],
        opening_message=args.first_message,
        initiator_agent_id=args.initiator_agent_id,
    )
    print(json.dumps(thread_resp, ensure_ascii=False, indent=2))
    thread_id = thread_resp["thread"]["thread_id"]
    first_reply = 等待回复(consumer, thread_id, args.first_expect, args.wait_seconds, args.poll_interval)
    打印成功(f"第一轮已收到回复：{first_reply}")

    if args.second_message:
        打印分隔("步骤 5：consumer 继续第二轮")
        second_resp = 继续服务会话(
            consumer,
            thread_id=thread_id,
            text=args.second_message,
            initiator_agent_id=args.initiator_agent_id,
        )
        print(json.dumps(second_resp, ensure_ascii=False, indent=2))
        second_reply = 等待回复(consumer, thread_id, args.second_expect, args.wait_seconds, args.poll_interval)
        打印成功(f"第二轮已收到回复：{second_reply}")

    打印分隔("完成")
    打印成功(f"service thread 联调成功，thread_id={thread_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise
