#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
 
远端 Agent Link 接入准备。

用途：
1. 使用 service account token 调平台 API 生成 openclaw:mia 的 connect_url
2. 读取 bootstrap 配置，确认 MQTT broker、topic、presence_url
3. 可选：把 connect_url 写入本机 OpenClaw 插件文件
4. 可选：等待本机 OpenClaw 插件 state.json 进入 online
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from remote_api_common import (
    ApiClient,
    默认平台地址,
    默认租户,
    默认签发密钥,
    打印分隔,
    打印成功,
    打印提示,
    生成接入链接,
    读取Bootstrap,
    签发服务账号令牌,
)


默认连接文件 = os.path.expanduser("~/.openclaw/channels/dbim_mqtt/connect-url.txt")
默认状态文件 = os.path.expanduser("~/.openclaw/channels/dbim_mqtt/state.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成远端 Agent Link connect_url，并可等待本机插件上线")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", 默认平台地址))
    parser.add_argument("--tenant-id", default=os.environ.get("TENANT_ID", 默认租户))
    parser.add_argument("--issuer-secret", default=os.environ.get("SERVICE_ACCOUNT_ISSUER_SECRET", 默认签发密钥))
    parser.add_argument("--agent-id", default=os.environ.get("AGENT_ID", "openclaw:mia"))
    parser.add_argument("--display-name", default=os.environ.get("DISPLAY_NAME", "MIA"))
    parser.add_argument("--workspace-name", default=os.environ.get("WORKSPACE_NAME", "mia"))
    parser.add_argument("--write-connect-url-file", default=os.environ.get("PLUGIN_CONNECT_URL_FILE", ""))
    parser.add_argument("--wait-state-file", default=os.environ.get("PLUGIN_STATE_FILE", ""))
    parser.add_argument("--wait-seconds", type=int, default=int(os.environ.get("PLUGIN_WAIT_SECONDS", "60")))
    parser.add_argument("--verify-tls", action="store_true", help="校验 TLS 证书。HTTP 或自签证书场景不需要开启")
    return parser.parse_args()


def 写入连接文件(path_text: str, connect_url: str) -> None:
    """把 connect_url 写入本机插件监听的文件，插件会读取后完成 bootstrap。"""
    path = Path(path_text).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(connect_url + "\n", encoding="utf-8")
    打印成功(f"connect_url 已写入：{path}")


def 等待插件在线(path_text: str, agent_id: str, wait_seconds: int) -> None:
    """读取本机插件 state.json，等待 status=online。"""
    path = Path(path_text).expanduser()
    for index in range(1, wait_seconds + 1):
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
            status = state.get("status", "unknown")
            current_agent = state.get("agentId", "")
            print(f"第 {index} 秒，插件状态：{status}，agentId={current_agent}")
            if status == "online" and (not current_agent or current_agent == agent_id):
                打印成功("插件已在线")
                return
        time.sleep(1)
    raise RuntimeError(f"等待插件在线超时：{path}")


def main() -> int:
    args = parse_args()

    打印分隔("步骤 1：签发 service account token")
    token = 签发服务账号令牌(
        api_base=args.api_base,
        issuer_secret=args.issuer_secret,
        tenant_id=args.tenant_id,
        verify_tls=args.verify_tls,
    )
    client = ApiClient(args.api_base, token=token, verify_tls=args.verify_tls)
    打印成功("token 已准备")

    打印分隔("步骤 2：生成 connect_url")
    link = 生成接入链接(client, args.agent_id, args.display_name, args.workspace_name)
    print(json.dumps(link, ensure_ascii=False, indent=2))

    打印分隔("步骤 3：读取 bootstrap 配置")
    bootstrap = 读取Bootstrap(args.api_base, link["bootstrap_url"], verify_tls=args.verify_tls)
    print(json.dumps({
        "agent_id": bootstrap["agent_id"],
        "tenant_id": bootstrap["tenant_id"],
        "mqtt_broker_url": bootstrap["mqtt_broker_url"],
        "mqtt_command_topic": bootstrap["mqtt_command_topic"],
        "presence_url": bootstrap["presence_url"],
        "mqtt_client_id": bootstrap["mqtt_client_id"],
    }, ensure_ascii=False, indent=2))

    connect_file = args.write_connect_url_file
    if connect_file:
        打印分隔("步骤 4：写入本机插件 connect_url 文件")
        写入连接文件(connect_file, link["connect_url"])
    else:
        打印分隔("步骤 4：手动接入提示")
        打印提示("如 OpenClaw 插件在本机，可执行：")
        print(f"mkdir -p {Path(默认连接文件).parent}")
        print(f"printf '%s\\n' '{link['connect_url']}' > {默认连接文件}")

    state_file = args.wait_state_file
    if state_file:
        打印分隔("步骤 5：等待本机插件 online")
        等待插件在线(state_file, args.agent_id, args.wait_seconds)
    else:
        打印分隔("步骤 5：状态检查提示")
        打印提示(f"如插件在本机，可查看：cat {默认状态文件}")

    打印分隔("完成")
    打印成功("Agent Link 接入配置已生成")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise
