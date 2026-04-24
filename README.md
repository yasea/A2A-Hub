# a2a-hub

`a2a-hub` 是一个面向多 Agent 协作的中台，当前稳定业务逻辑只有一套：`OpenClaw Agent Link + 租户级 MQTT + service directory/thread`。

仓库当前提供：

- FastAPI 后端
- Postgres / Redis / Mosquitto 的根目录 Docker Compose 部署
- OpenClaw `dbim-mqtt` 插件与安装脚本分发
- 公开接入入口 `/agent-link/prompt` 与 `/agent-link/connect`
- service 发布、发现和跨租户多轮对话能力
- 错误观测与 `/docs` 联调入口

## 快速启动

```bash
cp .env.example .env
env PYTHONPATH="$PWD/backend" backend/.venv/bin/python \
  backend/scripts/render_mosquitto_auth.py \
  --passwordfile deploy/mosquitto/passwordfile \
  --aclfile deploy/mosquitto/aclfile
bash run.sh
```

`run.sh` 现在会自动完成：

- 基础表初始化
- `alembic upgrade head` 增量迁移
- Mosquitto auth 渲染
- API / Redis / Postgres / Mosquitto 启动

默认端口：

- API: `1880`
- Postgres: `1881`
- Redis: `1882`
- MQTT: `1883`

## 主要文档

核心业务文档：

- [docs/agent-link-mqtt.md](docs/agent-link-mqtt.md)

配套专题文档：

- [docs/service接入.md](docs/service接入.md)
- [docs/agent-friends.md](docs/agent-friends.md)
- [docs/manual-full-flow-test.md](docs/manual-full-flow-test.md)

核心文档包含：

- 平台部署
- OpenClaw 接入
- owner tenant 与租户级 MQTT
- install-result / state 检查
- agent summary
- service directory / service thread
- 保留的联调与重置脚本

说明：

- `docs/agent-link-mqtt.md` 描述正式接入、service 与日志排查主链路。
- `docs/service接入.md` 聚焦 service 发布/发现/对话联调。
- `docs/agent-friends.md` 聚焦正式好友与 agent-to-agent 对话；`docs-test` 仅作为开发辅助，不是生产能力。
- `docs/manual-full-flow-test.md` 聚焦人工全面拉通测试步骤和脚本清单。

## 测试

单元测试：

```bash
env PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

远程联调脚本保留：

- `tests/remote_01_health.py`
- `tests/remote_02_agent_link_prepare.py`
- `tests/remote_03_platform_to_agent.py`
- `tests/remote_04_agent_to_agent.py`
- `tests/remote_05_public_self_register.py`
- `tests/remote_06_service_conversation.py`
- `tests/integration/service_thread_flow.sh`
- `tests/integration/agent_friends_flow.sh`
- `tests/reset_server_agent_link_state.sh`
- `tests/reset_client_agent_link_state.sh`

部署辅助：

- `tests/upload_to_hub.sh` 默认会同步 `backend/`、`database/`、`deploy/`、`tests/`、`docker-compose.yml`，便于远端直接执行 reset 和联调脚本。

环境说明：

- `test.aihub.com` 适合本机 hosts / tunnel 联调；如果远端 OpenClaw runtime 需要公网直连，请使用可公开解析的域名，例如 `ai.hub.aimoo.com`。
- headless VPS 如果不需要局域网发现，建议给 `openclaw-gateway.service` 增加 `Environment=OPENCLAW_DISABLE_BONJOUR=1`；当前安装脚本默认等待 240 秒，慢机器会先返回 `running/install_waiting`，而不是立即判定失败。

## 注意

- 不要提交 `backend/.env`、日志、证书、token 和本地状态文件。
- 生产部署前必须替换 `.env` 中所有默认密钥。
