"""
OpenClaw Agent Link 基础插件 / Sidecar。

用途：
- 读取平台生成的 connect_url
- 自动 bootstrap
- 建立 MQTT 订阅
- 定时上报 presence
- 收到 task.dispatch 后自动回 task.ack
- 调用本地命令处理任务
- 处理完成后回 task.update

运行示例：
python -m app.sdk.openclaw_plugin \
  --connect-url 'https://hub.example.com/openclaw/agents/connect?token=xxx' \
  --handler-command 'python /opt/openclaw/handlers/ava_handler.py'
"""
import argparse
import asyncio
import json
import shlex
import subprocess
import threading
from typing import Any

from app.sdk.agent_link import AgentLinkClient, OpenClawAgentAdapter


class LocalCommandHandler:
    def __init__(self, command: str | None = None):
        self.command = command

    def handle(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """执行本地处理器，返回 (成功, 输出文本)。"""
        if not self.command:
            input_text = payload.get("input_text") or ""
            task_id = payload.get("task_id") or "unknown"
            return True, f"本地插件已收到任务 {task_id}，输入内容：{input_text}"

        env = {
            **subprocess.os.environ,
            "A2A_TASK_ID": str(payload.get("task_id") or ""),
            "A2A_TENANT_ID": str(payload.get("tenant_id") or ""),
            "A2A_CONTEXT_ID": str(payload.get("context_id") or ""),
            "A2A_TASK_TYPE": str(payload.get("task_type") or ""),
            "A2A_TRACE_ID": str(payload.get("trace_id") or ""),
        }
        result = subprocess.run(
            shlex.split(self.command),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            env=env,
        )
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0:
            return True, output or "处理成功"
        return False, output or f"处理器退出码: {result.returncode}"


class OpenClawPluginRunner:
    def __init__(
        self,
        connect_url: str,
        handler_command: str | None = None,
        presence_interval: int = 30,
        metadata: dict[str, Any] | None = None,
    ):
        self.connect_url = connect_url
        self.handler = LocalCommandHandler(handler_command)
        self.presence_interval = presence_interval
        self.metadata = metadata or {}
        self.client = AgentLinkClient(connect_url=connect_url, on_task=self.on_task)
        self.adapter = OpenClawAgentAdapter(self.client, base_url=self._base_url(connect_url))
        self._loop = asyncio.new_event_loop()

    @staticmethod
    def _base_url(connect_url: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(connect_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def initialize(self) -> None:
        try:
            bootstrap = await self.client.load_bootstrap()
            print("bootstrap 完成")
            print(f"agent_id={bootstrap.agent_id}")
            print(f"tenant_id={bootstrap.tenant_id}")
            print(f"mqtt_broker_url={bootstrap.mqtt_broker_url}")
            print(f"mqtt_command_topic={bootstrap.mqtt_command_topic}")
            print(f"presence_url={bootstrap.presence_url}")
            await self.client.send_presence(self.metadata)
            print("首次 presence 上报完成")
        except Exception as exc:
            await self.client.report_error(stage="bootstrap", summary="初始化失败", detail=str(exc), category="runtime")
            raise

    async def presence_loop(self) -> None:
        while True:
            try:
                await self.client.send_presence(self.metadata)
            except Exception as exc:
                print(f"presence 上报失败: {exc}")
            await asyncio.sleep(self.presence_interval)

    async def on_task(self, payload: dict[str, Any]) -> None:
        msg_type = payload.get("type")
        if msg_type != "task.dispatch":
            print(f"忽略非 task.dispatch 消息: {payload}")
            return

        task_id = payload.get("task_id")
        print(f"收到任务: task_id={task_id}")

        await self.client.send_agent_message(
            {
                "type": "task.ack",
                "task_id": task_id,
            }
        )
        print(f"已发送 task.ack: task_id={task_id}")

        try:
            success, output = self.handler.handle(payload)
        except Exception as exc:
            success = False
            output = f"处理异常: {exc}"
            await self.client.report_error(
                stage="local_handler",
                summary="本地任务处理异常",
                detail=str(exc),
                category="runtime",
                metadata={"task_id": str(task_id or "")},
            )

        if success:
            await self.adapter.update_task(
                task_id=task_id,
                state="COMPLETED",
                output_text=output,
                message_text=output,
            )
            print(f"任务完成: task_id={task_id}")
        else:
            await self.client.report_error(
                stage="task_update",
                summary="任务执行失败",
                detail=output,
                category="runtime",
                metadata={"task_id": str(task_id or "")},
            )
            await self.adapter.update_task(
                task_id=task_id,
                state="FAILED",
                output_text=output,
                message_text=output,
            )
            print(f"任务失败: task_id={task_id}")

    def _run_background_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self.presence_loop())
        self._loop.run_forever()

    def start(self) -> None:
        asyncio.run(self.initialize())
        thread = threading.Thread(target=self._run_background_loop, daemon=True)
        thread.start()
        print("开始监听 MQTT...")
        self.client.run_mqtt()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw Agent Link 基础插件 / Sidecar")
    parser.add_argument("--connect-url", required=True, help="平台生成的一次性 connect_url")
    parser.add_argument(
        "--handler-command",
        default=None,
        help="收到任务后执行的本地命令。平台 task.dispatch JSON 会通过 stdin 传给该命令。",
    )
    parser.add_argument(
        "--presence-interval",
        type=int,
        default=30,
        help="presence 上报间隔，单位秒，默认 30",
    )
    parser.add_argument(
        "--metadata-json",
        default="{}",
        help="presence 附带的 metadata，JSON 字符串格式",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        metadata = json.loads(args.metadata_json or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"metadata-json 不是合法 JSON: {exc}") from exc

    runner = OpenClawPluginRunner(
        connect_url=args.connect_url,
        handler_command=args.handler_command,
        presence_interval=args.presence_interval,
        metadata=metadata,
    )
    runner.start()


if __name__ == "__main__":
    main()
