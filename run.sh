#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "run.sh 已切换为 Docker 启动模式"
cd "$SCRIPT_DIR/backend"
docker compose down
docker compose up -d postgres redis mosquitto db-init api
echo "已启动 postgres、redis、mosquitto、api，并执行数据库初始化。"
echo "默认访问地址请查看 backend/.env 中的 A2A_HUB_PUBLIC_BASE_URL 配置。"
