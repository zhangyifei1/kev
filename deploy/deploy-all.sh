#!/bin/sh
# 构建(走 host 网络,绕开 buildkit 构建容器网络的 CDN 劣质节点问题) + 启动
cd /home/ubuntu/zhangyifei/kev-docker
docker build --network=host -f deploy/Dockerfile -t kev-4b:latest . > deploy/build.log 2>&1
if docker images kev-4b --format '{{.Repository}}' | grep -q kev-4b; then
    echo "BUILD-OK $(date)" >> deploy/deploy.log
    cd deploy && docker compose up --no-build -d >> deploy.log 2>&1 \
        && echo "UP-OK $(date)" >> deploy.log || echo "UP-FAILED $(date)" >> deploy.log
else
    echo "BUILD-FAILED $(date)" >> deploy.log
    tail -30 build.log >> deploy.log
fi
