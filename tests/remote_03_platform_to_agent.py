#!/bin/sh
"exec" "python3" "$0" "$@"
"""
远端平台 -> OpenClaw agent 全流程测试。

流程：
1. 签发 service account token，模拟平台内部组件
2. 创建 context
3. 调 /v1/messages/send 给 openclaw:mia 发消息
4. 轮询任务状态
5. 读取 /v1/tasks/{task_id}/messages，确认 assistant 回复
"""

import argparse
import json
import os
import sys

from remote_api_common import (
    ApiClient,
    默认平台地址,
    默认租户,
    默认签发密钥,
    创建上下文,
    发送平台消息,
    打印分隔,
    打印成功,
    等待任务完成,
    读取任务消息,
    签发服务账号令牌,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="模拟平台内部组件给 OpenClaw agent 发消息")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE") or os.environ.get("API") or 默认平台地址)
    parser.add_argument("--tenant-id", default=os.environ.get("TENANT_ID", 默认租户))
    parser.add_argument("--issuer-secret", default=os.environ.get("SERVICE_ACCOUNT_ISSUER_SECRET", 默认签发密钥))
    parser.add_argument("--target-agent-id", default=os.environ.get("AGENT_ID", "openclaw:mia"))
    parser.add_argument("--source-agent-id", default=os.environ.get("SOURCE_AGENT_ID", "service:remote-platform"))
    parser.add_argument("--message", default=os.environ.get("MESSAGE_TEXT", "请只回复：REMOTE_PLATFORM_TO_MIA_OK，1+2323=?"))
    parser.add_argument("--expect", default=os.environ.get("EXPECT_TEXT", "REMOTE_PLATFORM_TO_MIA_OK"))
    parser.add_argument("--wait-seconds", type=int, default=int(os.environ.get("TASK_WAIT_SECONDS", "180")))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("TASK_POLL_INTERVAL", "1")))
    parser.add_argument("--verify-tls", action="store_true", help="校验 TLS 证书。HTTP 或自签证书场景不需要开启")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    打印分隔("步骤 1：签发 service account token")
    token = 签发服务账号令牌(
        api_base=args.api_base,
        issuer_secret=args.issuer_secret,
        tenant_id=args.tenant_id,
        service_account_id="platform-remote-message-tester",
        component_type="message_router",
        scopes=["messages:send"],
        verify_tls=args.verify_tls,
    )
    client = ApiClient(args.api_base, token=token, verify_tls=args.verify_tls)
    打印成功("token 已准备")

    task_id = ""
    if not task_id:
        打印分隔("步骤 2：创建 context")
        context_id = 创建上下文(client, args.source_agent_id, args.target_agent_id, "远端平台到 OpenClaw 联调")
        打印成功(f"context_id={context_id}")

        打印分隔("步骤 3：发送消息")
        print(f"source_agent_id={args.source_agent_id}")
        print(f"target_agent_id={args.target_agent_id}")
        print(f"message={args.message}")
        task_id = 发送平台消息(client, context_id, args.target_agent_id, args.message, args.source_agent_id)
    打印成功(f"task_id={task_id}")

    打印分隔("步骤 4：等待任务完成")
    task = 等待任务完成(client, task_id, args.wait_seconds, args.poll_interval)
    print(json.dumps(task, ensure_ascii=False, indent=2))
    if task["state"] != "COMPLETED":
        raise RuntimeError(f"任务未成功完成：{task['state']}")

    打印分隔("步骤 5：读取消息列表")
    messages = 读取任务消息(client, task_id)
    for message in messages:
        content = message.get("content_text") or json.dumps(message.get("content_json"), ensure_ascii=False)
        print(f"[{message['seq_no']}] {message['role']}: {content}")

    assistant_messages = [message for message in messages if message["role"] == "assistant"]
    if not assistant_messages:
        raise RuntimeError("任务已完成，但没有 assistant 消息")
    reply = assistant_messages[-1].get("content_text") or ""
    if args.expect and args.expect not in reply:
        raise RuntimeError(f"回复未包含期望文本：expect={args.expect}, reply={reply}")

    打印分隔("完成")
    打印成功(f"已收到 {args.target_agent_id} 回复：{reply}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise
