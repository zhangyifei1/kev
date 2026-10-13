# Kev 推理服务镜像（V100 / Volta 专用:fp32 eager 路径,无 bf16、无 fused 内核）
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    # uv / pip 走腾讯云 PyPI 镜像(实测该内网机器各源均慢,腾讯云相对最快)
    UV_DEFAULT_INDEX=https://mirrors.cloud.tencent.com/pypi/simple \
    PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
    # 慢网下 30s 默认超时必挂,torch 全家桶单包近 700MB
    UV_HTTP_TIMEOUT=600 \
    # 降低并发,避免慢链路上并发流互相挤爆超时
    UV_CONCURRENT_DOWNLOADS=2 \
    # HuggingFace 走 hf-mirror
    HF_ENDPOINT=https://hf-mirror.com \
    # HF 缓存落在挂载卷,重建容器不用重新下载模型
    HF_HOME=/data/hf

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

# 先装依赖(锁在 uv.lock),再装 kev 本身,利用 Docker 层缓存
# uv 缓存用 BuildKit cache mount 持久化:网络中断导致层失败时,已下载的 wheel 在下次重试时直接复用
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra serve --no-dev --no-install-project

COPY kev ./kev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra serve --no-dev

COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8009
ENTRYPOINT ["/app/entrypoint.sh"]
