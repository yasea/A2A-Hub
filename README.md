# a2a-hub

`a2a-hub` 是一个面向多 Agent 协作的中台，当前重点支持 OpenClaw Agent 通过 `Agent Link + MQTT` 接入平台。

当前仓库提供：

- FastAPI 后端
- Postgres / Redis / Mosquitto 的 Docker Compose 部署
- OpenClaw `dbim-mqtt` 插件分发骨架
- 公开 Agent 接入入口 `/agent-link/connect` 与 `/agent-link/prompt`
- `/docs` 内置联调窗口与错误记录入口

## 目录

- `backend/`: API、服务、模型、Docker Compose、插件分发
- `docs/`: 当前文档
- `tests/`: 回归测试与远程联调脚本
- `database/`: 初始化 SQL 参考

## 快速启动

```bash
cd backend
cp .env.example .env
docker compose up -d postgres redis mosquitto db-init api
```

默认端口：

- API: `1880`
- Postgres: `1881`
- Redis: `1882`
- MQTT: `1883`

## OpenClaw 接入

推荐直接把下面这个地址发给 agent：

```text
http://<host>:1880/agent-link/prompt
```

如果 agent 已经理解安装流程，也可以读取：

```text
http://<host>:1880/agent-link/connect
```

更多说明见 [docs/agent-link-mqtt.md](docs/agent-link-mqtt.md)。

## 测试

单元测试：

```bash
env PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 上传 GitHub 前

- 不要提交 `backend/.env`
- 不要提交测试证书、密钥、日志和本地状态文件
- 生产部署前请替换 `.env` 里的所有默认密钥
