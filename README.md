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

默认端口：

- API: `1880`
- Postgres: `1881`
- Redis: `1882`
- MQTT: `1883`

## 主要文档

只保留一份业务文档：

- [docs/agent-link-mqtt.md](docs/agent-link-mqtt.md)

这份文档包含：

- 平台部署
- OpenClaw 接入
- owner tenant 与租户级 MQTT
- install-result / state 检查
- agent summary
- service directory / service thread
- 保留的联调与重置脚本

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
- `tests/reset_server_agent_link_state.sh`
- `tests/reset_client_agent_link_state.sh`

## 注意

- 不要提交 `backend/.env`、日志、证书、token 和本地状态文件。
- 生产部署前必须替换 `.env` 中所有默认密钥。
