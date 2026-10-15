"""部署侧启动器:复刻 kev.serve.main() 的模型初始化,外加结构化访问日志落盘。

不复用 uvicorn 默认 access log(格式简单、无耗时),改为:
- 中间件打一行结构化日志:ISO 时间 | 级别 | 调用 IP | 方法 路径 | 状态码 | 耗时 ms
- 同时输出到 stdout(docker logs 可看)和 /var/log/kev/kev.log(compose 挂载到宿主机磁盘)
- 文件按 20MB x 10 个自动轮转

配置全部来自 KEV_* 环境变量(compose 设置):KEV_MODEL / KEV_DTYPE / KEV_FUSED / KEV_CUDA_GRAPHS。
注意 kev.serve 的 app.state.server 在其 main() 里构建,不能 `python -m kev.serve` 与本文件混用。
"""
import json
import logging
import logging.handlers
import os
import time

import torch
import uvicorn
from dataclasses import replace

from kev.checkpoint import Checkpoint, LoadOptions
from kev.device import select
from kev.serve import SERVE_MAX_STATE, Server, app, fused_available

LOG_DIR = os.environ.get("KEV_LOG_DIR", "/var/log/kev")
os.makedirs(LOG_DIR, exist_ok=True)

# 模型加载、启动信息等走这个格式;访问日志行由中间件产出
LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "kev.log"), maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
logging.getLogger().addHandler(_file_handler)

access_logger = logging.getLogger("kev.access")

# KEV_LOG_CONTEXT=1 时,每个 /v1/systemone 请求的日志行附带 state 摘要(截断到 300 字符);
# 默认 0(不打 context,日志里只有 IP/方法/路径/状态/耗时)
LOG_CONTEXT = os.environ.get("KEV_LOG_CONTEXT", "0") == "1"
CTX_MAX_CHARS = 300


def _request_context(request) -> str:
    """请求体的 context 摘要(state 字段截断)。request.body() 读一次后缓存,下游解析不受影响。"""
    try:
        body = json.loads(request.scope.setdefault("_cached_body", b"") or b"{}")
    except Exception:
        return " context=<unparseable>"
    state = body.get("state", "")
    state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    if len(state_text) > CTX_MAX_CHARS:
        state_text = state_text[:CTX_MAX_CHARS] + f"...(共{len(state_text)}字符)"
    return f" context={state_text}"


@app.middleware("http")
async def _access_log(request, call_next):
    """每个请求一行:调用 IP、方法、路径、状态码、服务端耗时;KEV_LOG_CONTEXT=1 时附 state 摘要。"""
    ctx = ""
    if LOG_CONTEXT and request.method == "POST" and request.url.path.startswith("/v1/systemone"):
        raw = await request.body()
        request.scope["_cached_body"] = raw
        ctx = _request_context(request)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        ms = (time.perf_counter() - t0) * 1000
        ip = request.client.host if request.client else "-"
        access_logger.exception('%s "%s %s" 500 %.1fms%s', ip, request.method, request.url.path, ms, ctx)
        raise
    ms = (time.perf_counter() - t0) * 1000
    ip = request.client.host if request.client else "-"
    access_logger.info('%s "%s %s" %d %.1fms%s', ip, request.method, request.url.path, response.status_code, ms, ctx)
    return response


def load_server() -> None:
    """与 kev.serve.main() 相同的加载路径(V100 部署固定 CUDA),模型加载失败则进程直接退出。"""
    run = os.environ.get("KEV_MODEL", "jaredpalmer/kev-4b")
    opts = LoadOptions.from_env()
    if opts.backend is None:
        opts = replace(opts, backend="auto")
    dev, ck = select("auto", dtype=opts.dtype or torch.bfloat16), Checkpoint(run)
    if dev != "cpu" and opts.dtype is None:
        opts = replace(opts, dtype=torch.bfloat16)      # serving 默认 bf16;本部署 KEV_DTYPE=fp32 显式覆盖
    cuda_optimizations = dev == "cuda" and torch.version.hip is None
    if cuda_optimizations and opts.cuda_graphs is None:
        opts = replace(opts, cuda_graphs=True)           # KEV_CUDA_GRAPHS=0 时 from_env 已读入,不会走到这
    if cuda_optimizations and opts.fused is None:
        opts = replace(opts, fused=fused_available())   # KEV_FUSED=0 时 from_env 已读入,不会走到这
    tok, model = ck.load(dev, opts)
    app.state.server = Server(ck, tok, model, dev)
    print(f"serving {ck.requested} ({ck.path}) on {dev} via {model.backend} ({model.dtype}); "
          f"states over {SERVE_MAX_STATE:,} tokens {'truncated' if app.state.server.truncate_states else 'refused (422)'}",
          flush=True)


if __name__ == "__main__":
    load_server()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("KEV_PORT", "8009")),
                log_config=None, access_log=False)
