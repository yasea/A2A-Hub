#!/usr/bin/env bash

# 上传指定部署内容到 hub，并在远端执行 Docker 重启。
# 默认同步：
#   - backend/
#   - database/
#   - deploy/
#   - docker-compose.yml
#
# 可选：
#   - 通过 --only <path> 仅上传一个文件或目录
#   - <path> 必须是 backend、database、deploy 或 docker-compose.yml 之一
#
# 默认目标：
#   - 主机别名: hub
#   - 路径: /data/wwwroot/ai-hub
#
# 依赖：
#   - 本机已配置 sshpass，并通过 SSHPASS 环境变量提供密码
#   - 本机 ssh config 中存在 host=hub

set -euo pipefail

TARGET_HOST="${TARGET_HOST:-hub}"
TARGET_PATH="${TARGET_PATH:-/data/wwwroot/ai-hub}"
ONLY_PATH=""

usage() {
  cat <<'EOF'
用法:
  bash tests/upload_to_hub.sh
  bash tests/upload_to_hub.sh --only backend
  bash tests/upload_to_hub.sh --only backend/app/api/routes_integrations.py
  TARGET_HOST=hub TARGET_PATH=/data/wwwroot/ai-hub bash tests/upload_to_hub.sh --only docker-compose.yml

说明:
  - 默认上传 backend/、database/、deploy/、docker-compose.yml
  - --only 仅上传一个文件或目录
  - --only 的路径必须位于 backend/、database/、deploy/ 下，或等于 docker-compose.yml
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --only)
      ONLY_PATH="${2:-}"
      if [ -z "$ONLY_PATH" ]; then
        echo "--only 需要指定路径。" >&2
        usage >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v sshpass >/dev/null 2>&1; then
  echo "缺少 sshpass，请先安装。" >&2
  exit 2
fi

if [ -z "${SSHPASS:-}" ]; then
  echo "缺少 SSHPASS 环境变量，无法登录 ${TARGET_HOST}。" >&2
  exit 2
fi

is_allowed_path() {
  case "$1" in
    backend|backend/*|database|database/*|deploy|deploy/*|docker-compose.yml)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [ -n "$ONLY_PATH" ]; then
  if ! is_allowed_path "$ONLY_PATH"; then
    echo "不允许的 --only 路径: $ONLY_PATH" >&2
    usage >&2
    exit 2
  fi
  if [ ! -e "$ONLY_PATH" ]; then
    echo "缺少待上传内容: $ONLY_PATH" >&2
    exit 2
  fi
  SYNC_ITEMS=("$ONLY_PATH")
else
  for path in backend database deploy docker-compose.yml; do
    if [ ! -e "$path" ]; then
      echo "缺少待上传内容: $path" >&2
      exit 2
    fi
  done
  SYNC_ITEMS=(backend database deploy docker-compose.yml)
fi

echo "开始上传到 ${TARGET_HOST}:${TARGET_PATH}"
echo "同步内容: ${SYNC_ITEMS[*]}"

sshpass -e ssh "${TARGET_HOST}" "mkdir -p '${TARGET_PATH}'"

if [ -n "$ONLY_PATH" ]; then
  sshpass -e ssh "${TARGET_HOST}" "mkdir -p '${TARGET_PATH}/$(dirname "$ONLY_PATH")'"
  sshpass -e ssh "${TARGET_HOST}" "rm -rf '${TARGET_PATH}/${ONLY_PATH}'"
else
  sshpass -e ssh "${TARGET_HOST}" "cd '${TARGET_PATH}' && rm -rf backend database deploy docker-compose.yml"
fi

tar \
  --exclude='backend/.venv' \
  --exclude='backend/__pycache__' \
  --exclude='backend/dbim-mqtt-plugin/node_modules' \
  --exclude='backend/app/__pycache__' \
  --exclude='database/__pycache__' \
  --exclude='deploy/__pycache__' \
  -czf - \
  "${SYNC_ITEMS[@]}" \
| sshpass -e ssh "${TARGET_HOST}" "cd '${TARGET_PATH}' && tar -xzf -"

echo "上传完成，开始重启远端 Docker 服务..."

sshpass -e ssh "${TARGET_HOST}" "cd '${TARGET_PATH}' && docker compose up -d --build"

echo "远端服务状态："
sshpass -e ssh "${TARGET_HOST}" "cd '${TARGET_PATH}' && docker compose ps"

echo "完成。"
