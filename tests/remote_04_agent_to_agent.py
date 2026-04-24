#!/bin/sh
"exec" "python3" "$0" "$@"
"""
远端 agent-to-agent 全流程测试。

流程：
1. source agent 通过公开自注册拿到自己的 Agent Link token
2. 用 source agent token 创建 context
3. 调 /v1/agent-link/messages/send，模拟 source agent 给 target agent 发消息
4. 轮询任务并读取 assistant 回复

说明：
- 这个脚本不要求 source agent 插件真实在线，因为这里只用它的 Agent Link token 模拟发起方。
- target agent 必须已经通过插件接入并在线，例如 openclaw:mia。
"""

import argparse
import json
import os
import sys

from remote_api_common import (
    ApiClient,
    默认平台地址,
    创建上下文,
    发送Agent消息,
    打印分隔,
    打印成功,
    公开自注册,
    等待任务完成,
    读取任务消息,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="模拟一个已注册 agent 给另一个 OpenClaw agent 发消息")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE") or os.environ.get("API") or 默认平台地址)
    parser.add_argument("--source-agent-id", default=os.environ.get("SOURCE_AGENT_ID", "openclaw:ava"))
    parser.add_argument("--target-agent-id", default=os.environ.get("TARGET_AGENT_ID", "openclaw:mia"))
    parser.add_argument("--source-user-md-file", default=os.environ.get("SOURCE_USER_MD_FILE", ""))
    parser.add_argument("--message", default=os.environ.get("MESSAGE_TEXT", "请只回复：REMOTE_AGENT_TO_AGENT_OK"))
    parser.add_argument("--expect", default=os.environ.get("EXPECT_TEXT", "REMOTE_AGENT_TO_AGENT_OK"))
    parser.add_argument("--wait-seconds", type=int, default=int(os.environ.get("TASK_WAIT_SECONDS", "180")))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("TASK_POLL_INTERVAL", "1")))
    parser.add_argument("--verify-tls", action="store_true", help="校验 TLS 证书。HTTP 或自签证书场景不需要开启")
    return parser.parse_args()


def 构造owner_profile(path_text: str, local_agent_id: str) -> dict:
    if path_text:
        raw_text = open(os.path.expanduser(path_text), "r", encoding="utf-8").read()
        return {
            "source": "openclaw-user-md",
            "user_md_path": os.path.expanduser(path_text),
            "raw_text": raw_text,
            "local_agent_id": local_agent_id,
        }
    return {
        "source": "remote-agent-to-agent-test",
        "name": f"{local_agent_id} owner",
        "local_agent_id": local_agent_id,
    }


def main() -> int:
    args = parse_args()

    打印分隔("步骤 1：source agent 公开自注册，获取 Agent Link token")
    public_client = ApiClient(args.api_base, verify_tls=args.verify_tls)
    local_agent_id = args.source_agent_id.split(":", 1)[-1]
    source_bootstrap = 公开自注册(
        public_client,
        agent_id=args.source_agent_id,
        display_name=local_agent_id.upper(),
        local_agent_id=local_agent_id,
        owner_profile=构造owner_profile(args.source_user_md_file, local_agent_id),
    )
    source_agent_token = source_bootstrap["auth_token"]
    打印成功(f"source agent token 已准备：{source_bootstrap['agent_id']}")

    打印分隔("步骤 2：source agent 创建 context")
    agent_client = ApiClient(args.api_base, token=source_agent_token, verify_tls=args.verify_tls)
    context_id = 创建上下文(agent_client, args.source_agent_id, args.target_agent_id, "远端 agent-to-agent 联调")
    打印成功(f"context_id={context_id}")

    打印分隔("步骤 3：source agent 给 target agent 发消息")
    print(f"source_agent_id={args.source_agent_id}")
    print(f"target_agent_id={args.target_agent_id}")
    print(f"message={args.message}")
    task_id = 发送Agent消息(agent_client, context_id, args.target_agent_id, args.message, args.source_agent_id)
    打印成功(f"task_id={task_id}")

    打印分隔("步骤 4：等待 target agent 回复")
    task = 等待任务完成(agent_client, task_id, args.wait_seconds, args.poll_interval)
    print(json.dumps(task, ensure_ascii=False, indent=2))
    if task["state"] != "COMPLETED":
        raise RuntimeError(f"任务未成功完成：{task['state']}")

    打印分隔("步骤 5：读取消息列表")
    messages = 读取任务消息(agent_client, task_id)
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
    打印成功(f"{args.source_agent_id} 已收到 {args.target_agent_id} 回复：{reply}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise
