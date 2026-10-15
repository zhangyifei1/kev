#!/bin/sh
# 启动前先从 hf-mirror 预下载模型(适配器 + 底座),再拉起 serve
# 依赖已由镜像离线装好(系统 site-packages),无 venv、无网络安装
set -e

MODEL="${KEV_MODEL:-jaredpalmer/kev-4b}"
BASE="${KEV_BASE:-Qwen/Qwen3.5-4B-Base}"

if command -v hf >/dev/null 2>&1; then
    HF=hf
else
    HF=huggingface-cli
fi

cd /app
echo ">>> 预下载适配器: $MODEL"
"$HF" download "$MODEL"
echo ">>> 预下载底座: $BASE"
"$HF" download "$BASE"

echo ">>> 启动 kev.serve (fp32 eager, V100, 结构化日志落盘 /var/log/kev)"
# 启动器读 KEV_PORT 环境变量(compose 已设),不传命令行参数
exec python /app/serve_with_logs.py
