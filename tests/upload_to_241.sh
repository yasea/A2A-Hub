#!/bin/bash

# 上传 backend 目录到服务器 241
# 服务器地址: 172.16.110.241
# 目标路径: /data/ai-hub/backend

echo "开始上传 backend 目录到服务器 241..."

# 使用 rsync 同步 backend 目录到服务器
# --delete 删除服务器上存在但本地不存在的文件
# -z 压缩传输
# -a 归档模式，保留权限、时间等
# -v 显示详细信息
# --exclude 排除 .env 文件和 .venv 目录，不覆盖服务器上的配置
rsync -avz --delete --exclude '.env' --exclude '.venv/' --exclude '__pycache__/' \
    ./backend/ \
    root@172.16.110.241:/data/ai-hub/backend/

if [ $? -eq 0 ]; then
    echo "上传成功！"   
       
    echo "正在执行服务器启动脚本..."
    
    # 在服务器上执行启动脚本
    ssh root@172.16.110.241 "/data/ai-hub/run.sh"
    
    if [ $? -eq 0 ]; then
        echo "启动脚本执行成功！"
    else
        echo "启动脚本执行失败！"
        exit 1
    fi
else
    echo "上传失败！"
    exit 1
fi

echo "完成！"
