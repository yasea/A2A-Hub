#!/usr/bin/env python3
"""
公开 URL 自注册测试。

这个脚本不使用 service account 密钥，模拟 OpenClaw 插件拿到公开 URL 后：
1. 读取 /v1/agent-link/manifest
2. 提交 USER.md owner profile 到 /v1/agent-link/self-register
3. 验证平台返回 Agent Link token、MQTT broker/topic、presence_url
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from remote_api_common import ApiClient, 默认平台地址, 打印分隔, 打印成功, 公开自注册, 读取公开清单


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试公开 Agent Link URL 自注册流程")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", 默认平台地址))
    parser.add_argument("--agent-id", default=os.environ.get("AGENT_ID", "mia"))
    parser.add_argument("--agent-summary", default=os.environ.get("AGENT_SUMMARY", ""))
    parser.add_argument("--user-md-file", default=os.environ.get("USER_MD_FILE", ""))
    parser.add_argument("--verify-tls", action="store_true", help="校验 TLS 证书。HTTP 或自签证书场景不需要开启")
    return parser.parse_args()


def 读取用户信息(args: argparse.Namespace) -> dict:
    """读取 USER.md；没有文件时使用最小 owner profile。"""
    if args.user_md_file:
        path = Path(args.user_md_file).expanduser()
        raw_text = path.read_text(encoding="utf-8")
        return {
            "source": "openclaw-user-md",
            "user_md_path": str(path),
            "raw_text": raw_text,
            "local_agent_id": args.agent_id.split(":")[-1],
        }
    return {
        "source": "manual-test",
        "name": "远端公开自注册测试用户",
        "local_agent_id": args.agent_id.split(":")[-1],
    }


def main() -> int:
    args = parse_args()
    client = ApiClient(args.api_base, verify_tls=args.verify_tls)

    打印分隔("步骤 1：读取公开 manifest")
    manifest = 读取公开清单(client)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

    打印分隔("步骤 2：提交自注册")
    owner_profile = 读取用户信息(args)
    local_agent_id = args.agent_id.split(":")[-1]
    resp = 公开自注册(
        client,
        agent_id=args.agent_id,
        display_name=local_agent_id.upper(),
        local_agent_id=local_agent_id,
        owner_profile=owner_profile,
        agent_summary=args.agent_summary or f"OpenClaw agent {local_agent_id}",
    )
    print(json.dumps({
        "agent_id": resp["agent_id"],
        "tenant_id": resp["tenant_id"],
        "agent_summary": resp.get("agent_summary"),
        "mqtt_broker_url": resp["mqtt_broker_url"],
        "mqtt_command_topic": resp["mqtt_command_topic"],
        "presence_url": resp["presence_url"],
        "auth_token_prefix": resp["auth_token"][:24] + "...",
    }, ensure_ascii=False, indent=2))

    if not resp.get("auth_token") or not resp.get("mqtt_command_topic"):
        raise RuntimeError("自注册响应缺少 auth_token 或 mqtt_command_topic")

    打印分隔("完成")
    打印成功("公开 URL 自注册流程可用")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise
