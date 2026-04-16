#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "run.sh 已切换为 Docker 启动模式"
# docker-compose.yml 已移到项目根目录，与 .env 同级
cd "$SCRIPT_DIR"

if [ ! -f "$SCRIPT_DIR/backend/Dockerfile" ]; then
  echo "缺少 backend/Dockerfile，当前仓库无法执行 docker compose build。" >&2
  exit 1
fi

mkdir -p "$SCRIPT_DIR/deploy/mosquitto"
touch "$SCRIPT_DIR/deploy/mosquitto/passwordfile" "$SCRIPT_DIR/deploy/mosquitto/aclfile"

docker compose down
docker compose up -d postgres redis
docker compose up -d db-init

echo "正在容器内生成 Mosquitto auth 文件..."
docker compose run --rm --no-deps -e PYTHONPATH=/app api python scripts/render_mosquitto_auth.py \
  --passwordfile /app/runtime/mosquitto-auth/passwordfile \
  --aclfile /app/runtime/mosquitto-auth/aclfile


docker compose up -d mosquitto api
echo "已启动 postgres、redis、mosquitto、api，并执行数据库初始化与 MQTT auth 渲染。"
echo "默认访问地址请查看 .env 中的 A2A_HUB_PUBLIC_BASE_URL 配置。"
