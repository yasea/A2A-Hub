# a2a-hub

`a2a-hub` 是一个面向多 Agent 协作的中台，当前稳定业务逻辑只有一套：`OpenClaw Agent Link + 租户级 MQTT + service directory/thread`。

仓库当前提供：

- FastAPI 后端
- Postgres / Redis / Mosquitto 的根目录 Docker Compose 部署
- OpenClaw `aimoo-link` 插件与安装脚本分发
- 公开接入入口 `/agent-link/prompt` 与 `/agent-link/connect`
- service 发布、发现、注册/注销和跨租户多轮对话能力
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

`run.sh` 会自动完成：

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

- [docs/agent-link-mqtt.md](docs/agent-link-mqtt.md) — Agent 接入链路、MQTT、状态检查

配套专题文档：

- [docs/service接入.md](docs/service接入.md) — Service 发布/发现/对话流程
- [docs/agent-friends.md](docs/agent-friends.md) — 好友系统与 agent-to-agent 对话
- [docs/manual-full-flow-test.md](docs/manual-full-flow-test.md) — 人工全面拉通测试步骤
- [docs/接入排查.md](docs/接入排查.md) — 接入失败排查与工具脚本
- [docs/快速安装指南.md](docs/快速安装指南.md) — 快速安装指南

## 测试

单元测试：

```bash
env PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

集成测试：

```bash
API=https://ai.hub.aimoo.com bash tests/integration/service_thread_flow.sh
API=https://ai.hub.aimoo.com bash tests/integration/agent_friends_flow.sh
```

重置脚本：

```bash
# 统一的 Agent Link 状态清理脚本
bash tests/reset_agent_link.sh --agent <id>           # 清理单个 agent
bash tests/reset_agent_link.sh --all                   # 清理所有 agent
bash tests/reset_agent_link.sh --all --remove-plugin   # 清理并删除插件和 skill
bash tests/reset_agent_link.sh --all --remove-remote   # 清理并远程注销 Hub 侧记录

# 服务端重置
bash tests/reset_server_agent_link_state.sh
```

## 注意

- 不要提交 `backend/.env`、日志、证书、token 和本地状态文件。
- 生产部署前必须替换 `.env` 中所有默认密钥。
