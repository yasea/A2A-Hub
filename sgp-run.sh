#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "run.sh 已切换为 Docker 启动模式 (host 网络)"
cd "$SCRIPT_DIR"

if [ ! -f "$SCRIPT_DIR/backend/Dockerfile" ]; then
  echo "缺少 backend/Dockerfile，当前仓库无法执行 docker compose -f docker-compose-sgp.yml build。" >&2
  exit 1
fi

# 加载 .env 配置
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

# 显示配置
API_URL="${A2A_HUB_PUBLIC_BASE_URL:-http://localhost:${API_HOST_PORT:-1880}}"
MQTT_URL="${MQTT_PUBLIC_BROKER_URL:-mqtt://localhost:${MQTT_HOST_PORT:-1883}}"
echo "平台地址: $API_URL"
echo "MQTT 地址: $MQTT_URL"

mkdir -p "$SCRIPT_DIR/deploy/mosquitto"
touch "$SCRIPT_DIR/deploy/mosquitto/passwordfile" "$SCRIPT_DIR/deploy/mosquitto/aclfile"

# 停止旧容器
docker compose -f docker-compose-sgp.yml down 2>/dev/null || true

# 启动 Redis 和 Mosquitto
echo "启动 Redis 和 Mosquitto..."
docker compose -f docker-compose-sgp.yml up -d redis mosquitto

# 等待 Redis 就绪
echo "等待 Redis 就绪..."
for i in $(seq 1 30); do
  if nc -z 127.0.0.1 "${REDIS_HOST_PORT:-1882}" 2>/dev/null; then
    echo "Redis 已就绪"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Redis 启动超时" >&2
    exit 1
  fi
  sleep 1
done

# 等待 Mosquitto 就绪
echo "等待 Mosquitto 就绪..."
for i in $(seq 1 30); do
  if nc -z 127.0.0.1 "${MQTT_HOST_PORT:-1883}" 2>/dev/null; then
    echo "Mosquitto 已就绪"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "Mosquitto 启动超时" >&2
    exit 1
  fi
  sleep 1
done

# 执行数据库初始化
echo "执行数据库初始化..."
docker compose -f docker-compose-sgp.yml up db-init
if [ "$(docker inspect -f {{.State.ExitCode}} a2a-hub-db-init)" != "0" ]; then
  echo "数据库初始化失败" >&2
  docker logs a2a-hub-db-init
  exit 1
fi
echo "数据库初始化完成"

# 生成 Mosquitto auth 文件
echo "生成 Mosquitto auth 文件..."
docker compose -f docker-compose-sgp.yml run --rm -e PYTHONPATH=/app api python scripts/render_mosquitto_auth.py \
  --passwordfile /app/runtime/mosquitto-auth/passwordfile \
  --aclfile /app/runtime/mosquitto-auth/aclfile
echo "Mosquitto auth 生成成功"

# 启动 API
echo "启动 API 服务..."
docker compose -f docker-compose-sgp.yml up -d api

echo "已启动 redis、mosquitto、api，并执行数据库初始化与 MQTT auth 渲染。"
echo "默认访问地址: $API_URL"
