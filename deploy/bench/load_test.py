"""Kev-4B 服务压测脚本(本机 Windows 直接打 10.94.112.8:8009)。

stdlib 实现,无第三方依赖:threading + http.client(keep-alive 连接复用)。
场景矩阵:状态长度 x 题数,并发梯度 1/4/8/16/32。
输出 deploy/bench/results/results.json,报告由 make_report.py 汇总。
"""
import http.client
import json
import statistics
import sys
import threading
import time
from pathlib import Path

HOST, PORT = "10.94.112.8", 8009
OUT = Path(__file__).parent / "results" / "results.json"
TIMEOUT = 180

# 压测语料:中文句子循环填充,Qwen 分词器下 1 汉字 ≈ 1 token
_SENT = "第{}条规定,供应商应在合同签订后7个工作日内提交发票,逾期按日扣款合同金额的千分之一,重大变更需经法务部审核。"

def make_state(tokens: int) -> str:
    # 1 汉字 ≈ 1 token:按字符数生成(整句循环 + 截断),真实 token 数以响应 input_tokens 为准
    chars = tokens
    sents = chars // 31 + 1
    text = "公司采购付款政策全文如下:\n" + "\n".join(_SENT.format(i % 64) for i in range(sents))
    return text[:chars]

def make_payload(state_tokens: int, questions: int, seq: int = 0) -> dict:
    qs = {}
    if questions >= 1:
        qs["q1"] = {"type": "noul", "text": "供应商在第5个工作日提交发票,是否合规?"}
    if questions >= 2:
        qs["q2"] = {"type": "choice", "text": "这笔付款的分类", "criteria": {"normal": "正常按期", "late": "逾期提交", "exception": "例外审批"}}
    if questions >= 3:
        qs["q3"] = {"type": "score", "text": "违规严重程度", "criteria": ["无违规", "轻微", "一般", "严重"]}
    for i in range(3, questions):
        qs[f"q{i+1}"] = {"type": "noul", "text": f"第{i}号供应商的情况是否需要人工复核?"}
    state = make_state(state_tokens)
    if seq:  # 冷启动场景:每请求唯一尾缀,绕开前缀缓存
        state += f"\n补充条款{seq}:本附件仅适用于请求编号{seq},其余请求不适用本条。"
    return {"state": state, "questions": qs}

def worker(conn_args, payloads, lat, toks, errs, lock, stop_flag):
    conn = None
    while True:
        with lock:
            if stop_flag[0] >= len(payloads):
                return
            i = stop_flag[0]; stop_flag[0] += 1
        body = json.dumps(payloads[i]).encode()
        for attempt in (1, 2):  # 连接级重试一次(keep-alive 断开属正常)
            try:
                if conn is None:
                    conn = http.client.HTTPConnection(*conn_args, timeout=TIMEOUT)
                t0 = time.perf_counter()
                conn.request("POST", "/v1/systemone", body, {"Content-Type": "application/json"})
                resp = conn.getresponse()
                data = resp.read()
                dt = (time.perf_counter() - t0) * 1000
                if resp.status == 200:
                    with lock:
                        lat.append(dt)
                        try: toks.append(json.loads(data)["usage"]["input_tokens"])
                        except Exception: pass
                else:
                    with lock: errs.append(f"HTTP {resp.status}")
                break
            except Exception as e:
                conn = None
                if attempt == 2:
                    with lock: errs.append(type(e).__name__ + ": " + str(e)[:80])

def run_scenario(name, state_tokens, n_questions, n_requests, concurrency, unique_state=False):
    payloads = [make_payload(state_tokens, n_questions, seq=(i + 1) if unique_state else 0) for i in range(n_requests)]
    lat, toks, errs = [], [], []
    lock = threading.Lock(); stop_flag = [0]
    threads = [threading.Thread(target=worker, args=((HOST, PORT), payloads, lat, toks, errs, lock, stop_flag)) for _ in range(concurrency)]
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    wall = time.perf_counter() - t0
    lat.sort()
    def pct(p): return lat[min(len(lat) - 1, int(len(lat) * p))] if lat else None
    def rd(x): return round(x, 1) if x is not None else None
    r = {
        "scenario": name, "state_tokens": state_tokens, "questions": n_questions,
        "concurrency": concurrency, "requests": n_requests,
        "ok": len(lat), "errors": len(errs),
        "error_kinds": sorted({e.split(":")[0] for e in errs}),
        "wall_s": round(wall, 1),
        "qps": round(len(lat) / wall, 2),
        "latency_ms": {
            "mean": rd(statistics.mean(lat)) if lat else None,
            "p50": rd(pct(0.50)), "p95": rd(pct(0.95)), "p99": rd(pct(0.99)),
            "min": rd(lat[0]) if lat else None, "max": rd(lat[-1]) if lat else None,
        },
        "input_tokens_mean": round(statistics.mean(toks)) if toks else None,
    }
    print(f"[{name}] c={concurrency}  {r['ok']}/{n_requests} ok  qps={r['qps']}  p50={r['latency_ms']['p50']}  p95={r['latency_ms']['p95']}  p99={r['latency_ms']['p99']}  errs={r['error_kinds']}", flush=True)
    return r

SCENARIOS = [  # (名称, 状态token目标, 题数, 每并发档请求数, 每请求唯一状态)
    ("S1-短文本-单题",   50,    1, 100, False),
    ("S2-短文本-5题",    50,    5, 100, False),
    ("S3-中文本-5题",    1000,  5, 60,  False),
    ("S4-长文本-单题",   8000,  1, 24,  False),
    ("S5-超长文本-单题", 16000, 1, 12,  False),
    ("S4C-长文本冷启动",   8000,  1, 24, True),
    ("S5C-超长文本冷启动", 16000, 1, 12, True),
]
CONCURRENCY = [1, 4, 8, 16, 32]

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 续跑模式:读入已有结果,只跑缺失的 (场景, 并发) 组合——支持分段补跑而不丢数据
    results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    done = {(r["scenario"], r["concurrency"]) for r in results}
    todo = [(name, st, nq, nr, uq) for name, st, nq, nr, uq in SCENARIOS if not only or only in name]
    todo = [(name, st, nq, nr, uq) for name, st, nq, nr, uq in todo for c in CONCURRENCY if (name, c) not in done]
    # 预热:连接建立 + 模型已驻留显存确认
    print("warmup...", flush=True)
    run_scenario("warmup", 50, 1, 4, 4)
    for name, st, nq, nr, uq in todo:
        for c in CONCURRENCY:
            if (name, c) in done: continue
            results.append(run_scenario(name, st, nq, nr, c, unique_state=uq))
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
