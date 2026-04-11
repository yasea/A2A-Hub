#!/bin/bash

# 上传 backend 目录到目标服务器。
# 需要显式提供 TARGET_HOST，避免仓库内绑定某一台固定主机。
# 示例：
#   TARGET_HOST=deploy@example.internal
#   TARGET_PATH=/data/ai-hub/backend
#   REMOTE_RUN_SH=/data/ai-hub/run.sh

set -euo pipefail

TARGET_HOST="${TARGET_HOST:-}"
TARGET_PATH="${TARGET_PATH:-/data/ai-hub/backend}"
REMOTE_RUN_SH="${REMOTE_RUN_SH:-/data/ai-hub/run.sh}"

if [ -z "$TARGET_HOST" ]; then
  echo "缺少 TARGET_HOST。请先设置目标主机，例如：" >&2
  echo "  TARGET_HOST=deploy@example.internal $0" >&2
  exit 2
fi

echo "开始上传 backend 目录到目标服务器..."
echo "目标主机: ${TARGET_HOST}"
echo "目标路径: ${TARGET_PATH}"

# 使用 rsync 同步 backend 目录到服务器
# --delete 删除服务器上存在但本地不存在的文件
# -z 压缩传输
# -a 归档模式，保留权限、时间等
# -v 显示详细信息
# --exclude 排除 .env 文件和 .venv 目录，不覆盖服务器上的配置
rsync -avz --delete --exclude '.env' --exclude '.venv/' --exclude '__pycache__/' \
    ./backend/ \
    "${TARGET_HOST}:${TARGET_PATH}/"

echo "上传成功！"
echo "正在执行服务器启动脚本..."

ssh "${TARGET_HOST}" "${REMOTE_RUN_SH}"

echo "启动脚本执行成功！"

echo "完成！"
