#!/bin/sh
"exec" "python3" "$0" "$@"
"""
远端 Agent Link 接入准备脚本。

当前脚本只围绕最新方案：
1. 读取公开 manifest，打印 prompt/connect/install/plugin URL
2. 可选：直接下载 /agent-link/prompt，保存成一份可转发给 agent 的文本
3. 可选：根据当前 sessionKey 或本机 agent id 生成推荐安装命令
4. 可选：轮询 workspace 安装结果镜像，确认 install-result.json 是否成功

当前统一走公开 `/agent-link/connect`，不再写本地 `connect-url.txt`。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from remote_api_common import ApiClient, 默认平台地址, 打印分隔, 打印成功, 打印提示, 读取公开清单


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备公开 Agent Link 接入信息，并可轮询安装结果镜像")
    parser.add_argument("--api-base", default=os.environ.get("API_BASE") or os.environ.get("API") or 默认平台地址)
    parser.add_argument("--agent-id", default=os.environ.get("AGENT_ID", "mia"))
    parser.add_argument("--save-prompt-file", default=os.environ.get("SAVE_PROMPT_FILE", ""))
    parser.add_argument("--wait-result-file", default=os.environ.get("WAIT_RESULT_FILE", ""))
    parser.add_argument("--wait-seconds", type=int, default=int(os.environ.get("WAIT_SECONDS", "90")))
    parser.add_argument("--verify-tls", action="store_true", help="校验 TLS 证书。HTTP 或自签证书场景不需要开启")
    return parser.parse_args()


def _读取原始文本(client: ApiClient, path: str) -> str:
    from urllib.request import Request, urlopen

    url = path if path.startswith("http://") or path.startswith("https://") else f"{client.base_url}{path}"
    req = Request(url, headers={"Accept": "text/plain, text/markdown, */*"}, method="GET")
    with urlopen(req, timeout=client.timeout, context=client.ssl_context) as resp:
        return resp.read().decode("utf-8")


def 等待安装结果(path_text: str, wait_seconds: int) -> dict:
    """轮询 workspace 镜像文件，优先以 install-result.json 判断结果。"""
    path = Path(path_text).expanduser()
    deadline = time.time() + wait_seconds
    last_data: dict = {}
    while time.time() < deadline:
        if path.exists():
            try:
                last_data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                last_data = {}
            status = str(last_data.get("status") or "").lower()
            stage = last_data.get("stage") or ""
            state = last_data.get("state") or {}
            state_status = state.get("status") or ""
            print(f"当前结果：status={status or 'unknown'} stage={stage or '-'} state.status={state_status or '-'}")
            if status == "success" or state_status == "online":
                return last_data
            if status == "failed":
                raise RuntimeError(f"安装失败：{json.dumps(last_data, ensure_ascii=False)}")
        time.sleep(2)
    raise RuntimeError(f"等待安装结果超时：{path}，最后结果={json.dumps(last_data, ensure_ascii=False)}")


def main() -> int:
    args = parse_args()
    client = ApiClient(args.api_base, verify_tls=args.verify_tls)
    local_agent_id = args.agent_id.split(":")[-1]

    打印分隔("步骤 1：读取公开 Agent Link manifest")
    manifest = 读取公开清单(client)
    print(json.dumps({
        "public_connect_url": manifest["public_connect_url"],
        "agent_prompt_url": manifest["agent_prompt_url"],
        "openclaw_install_script_url": manifest["openclaw_install_script_url"],
        "plugin_download_url": manifest["plugin_download_url"],
        "required_plugin": manifest["required_plugin"],
        "mqtt_public_broker_url": manifest["mqtt_public_broker_url"],
    }, ensure_ascii=False, indent=2))

    打印分隔("步骤 2：推荐直接转发给 Agent 的入口")
    print(manifest["agent_prompt_url"])

    打印分隔("步骤 3：推荐安装命令")
    print(
        "# 推荐：agent 先调用 session_status，从 sessionKey（如 agent:mia:main）解析出本机短 id。\n"
        "AGENT_ID=\"{agent}\" CONNECT_URL=\"{connect}\" \\\n"
        "curl -fsSL \"{script}\" | bash\n\n"
        "# 如果无法取得 session_status，再使用安装脚本自动识别兜底：\n"
        "CONNECT_URL=\"{connect}\" \\\n"
        "curl -fsSL \"{script}\" | bash\n\n"
        "# 如自动识别失败，再补：\n"
        "AGENT_ID=\"{agent}\" CONNECT_URL=\"{connect}\" curl -fsSL \"{script}\" | bash".format(
            agent=local_agent_id,
            connect=manifest["public_connect_url"],
            script=manifest["openclaw_install_script_url"],
        )
    )

    if args.save_prompt_file:
        打印分隔("步骤 4：保存 prompt 文本")
        prompt_text = _读取原始文本(client, manifest["agent_prompt_url"])
        target = Path(args.save_prompt_file).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(prompt_text, encoding="utf-8")
        打印成功(f"已写入：{target}")
    else:
        打印分隔("步骤 4：提示")
        打印提示("如需把完整任务文本保存到本机文件，可追加 --save-prompt-file <路径>")

    if args.wait_result_file:
        打印分隔("步骤 5：等待 install-result.json")
        result = 等待安装结果(args.wait_result_file, args.wait_seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        打印成功("安装结果已显示成功或在线")
    else:
        默认结果文件 = f"~/.openclaw/workspace/{local_agent_id}/.agent-link/install-result.json"
        打印分隔("步骤 5：结果检查提示")
        打印提示("安装完成后优先检查 workspace 里的结果镜像：")
        print(f"cat {默认结果文件}")

    打印分隔("完成")
    打印成功("当前推荐接入信息已准备好")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise
