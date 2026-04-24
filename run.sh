#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "run.sh 已切换为 Docker 启动模式"
cd "$SCRIPT_DIR"

if [ ! -f "$SCRIPT_DIR/backend/Dockerfile" ]; then
  echo "缺少 backend/Dockerfile，当前仓库无法执行 docker compose build。" >&2
  exit 1
fi

# 加载 .env 配置（处理特殊字符）
if [ -f "$SCRIPT_DIR/.env" ]; then
  while IFS='=' read -r key value; do
    # 跳过注释和空行
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    # 跳过包含特殊 shell 字符的值
    if [[ "$value" =~ [\$\#\`\\] ]]; then
      continue
    fi
    export "$key=$value"
  done < "$SCRIPT_DIR/.env"
fi

# 显示配置
API_URL="${A2A_HUB_PUBLIC_BASE_URL:-http://localhost:${API_HOST_PORT:-1880}}"
MQTT_URL="${MQTT_PUBLIC_BROKER_URL:-mqtt://localhost:${MQTT_HOST_PORT:-1883}}"
echo "平台地址: $API_URL"
echo "MQTT 地址: $MQTT_URL"

mkdir -p "$SCRIPT_DIR/deploy/mosquitto"
touch "$SCRIPT_DIR/deploy/mosquitto/passwordfile" "$SCRIPT_DIR/deploy/mosquitto/aclfile"

docker compose down
docker compose up -d postgres redis

# 等待 postgres 就绪
echo "等待 PostgreSQL 就绪..."
i=0
max_attempts=30
while [ $i -lt $max_attempts ]; do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-a2a_hub}" >/dev/null 2>&1; then
    echo "PostgreSQL 已就绪"
    break
  fi
  i=$((i+1))
  sleep 1
done
if [ $i -ge $max_attempts ]; then
  echo "PostgreSQL 启动超时" >&2
  exit 1
fi

docker compose up -d db-init

# 等待 db-init 完成
echo "等待数据库初始化..."
container_name="a2a-hub-db-init"
if docker wait "$container_name" >/dev/null 2>&1; then
  echo "数据库初始化完成"
else
  echo "数据库初始化失败" >&2
  docker compose logs db-init
  exit 1
fi

echo "执行数据库迁移..."
attempt=0
max_attempts=3
while [ $attempt -lt $max_attempts ]; do
  if docker compose run --rm --no-deps -e PYTHONPATH=/app api alembic upgrade head 2>&1; then
    echo "数据库迁移完成"
    break
  fi
  attempt=$((attempt+1))
  echo "数据库迁移失败，重试 ($attempt/$max_attempts)..."
  sleep 2
done
if [ $attempt -ge $max_attempts ]; then
  echo "数据库迁移失败" >&2
  exit 1
fi

echo "正在容器内生成 Mosquitto auth 文件..."
attempt=0
max_attempts=3
while [ $attempt -lt $max_attempts ]; do
  if docker compose run --rm --no-deps -e PYTHONPATH=/app api python scripts/render_mosquitto_auth.py \
    --passwordfile /app/runtime/mosquitto-auth/passwordfile \
    --aclfile /app/runtime/mosquitto-auth/aclfile 2>&1; then
    echo "Mosquitto auth 生成成功"
    break
  fi
  attempt=$((attempt+1))
  echo "Mosquitto auth 生成失败，重试 ($attempt/$max_attempts)..."
  sleep 2
done
if [ $attempt -ge $max_attempts ]; then
  echo "Mosquitto auth 生成失败" >&2
  exit 1
fi

docker compose up -d mosquitto api
echo "已启动 postgres、redis、mosquitto、api，并执行数据库初始化与 MQTT auth 渲染。"
echo "默认访问地址: $API_URL"
