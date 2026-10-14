# Kev 推理服务镜像(V100 / Volta 专用:fp32 eager 路径,无 bf16、无 fused 内核)
#
# 依赖以 wheel 形式预下载在 deploy/wheels/(download-wheels.sh 用 pip 生成),
# 构建完全离线——这台机器的链路对 uv(rustls)的下载限速到 KB/s,容器内
# 联网安装不可靠;pip/urllib(OpenSSL)不受影响,wheel 预下载走同一条快路径。
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    # HuggingFace 走 hf-mirror(仅运行时下载模型用)
    HF_ENDPOINT=https://hf-mirror.com \
    # HF 缓存落在挂载卷,重建容器不用重新下载模型
    HF_HOME=/data/hf

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 源码与依赖清单(kev 的 editable 安装需要 pyproject + 包源码)
COPY pyproject.toml uv.lock ./
COPY kev ./kev

# 离线安装:全部依赖来自预下载的 wheel(含 setuptools/wheel,供 kev editable 构建用)
COPY deploy/requirements.txt /wheels/requirements.txt
COPY deploy/wheels/ /wheels/
RUN pip install --no-cache-dir --no-index --find-links /wheels \
        -r /wheels/requirements.txt \
    && pip install --no-cache-dir --no-index --no-deps -e . \
    && rm -rf /wheels

COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8009
ENTRYPOINT ["/app/entrypoint.sh"]
