#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$ROOT_DIR"

mkdir -p deploy/mosquitto
: > deploy/mosquitto/passwordfile
cat > deploy/mosquitto/aclfile <<'EOF'
# 租户级 ACL：Mosquitto 用户名即 tenant_id。
pattern readwrite a2a-hub/%u/#
EOF
: > deploy/mosquitto/reload.stamp

docker compose down -v --remove-orphans
docker compose up -d postgres redis
docker compose up db-init

if [ -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
  env PYTHONPATH="$ROOT_DIR/backend" \
    "$ROOT_DIR/backend/.venv/bin/python" \
    "$ROOT_DIR/backend/scripts/render_mosquitto_auth.py" \
    --passwordfile "$ROOT_DIR/deploy/mosquitto/passwordfile" \
    --aclfile "$ROOT_DIR/deploy/mosquitto/aclfile"
fi

docker compose up -d mosquitto api

echo "服务端测试状态已清空并重建。"
echo "已重置 PostgreSQL、Redis、Mosquitto 数据，并重新启动 api/mosquitto。"
