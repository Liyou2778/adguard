"""Concurrent benchmark client for the M5 inference comparison (vLLM vs SGLang).

Measures per-request TTFT / TPOT / end-to-end latency and throughput at a fixed
concurrency level, then reports P50/P95/P99 plus errors. Client-side timing is taken
from HTTP streaming so TTFT is real, not estimated.

Usage:
    python -m bench.bench_client --base-url http://127.0.0.1:8100 --concurrency 8 \
        --requests 96 --output /tmp/bench-vllm-c8.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

SYSTEM_PROMPT = """你是 AdGuard 营销素材合规初审引擎。判断营销素材是否违反广告法，输出严格 JSON。"""

PROMPTS = [
    "全网最低价，错过再等一年！本店承诺国家级品质。",
    "本店商品支持七天无理由退换，物流售后有保障。",
    "销量第一（有据可查的历史数据，可提供证明）",
    "本店最\u200b😀佳，醉好品质",
    "商品详情：虚假宣传，引人误解，限时秒杀。",
    "新品上市，欢迎到店体验，客服在线 9:00-21:00。",
]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[index]


async def one_request(client: httpx.AsyncClient, base_url: str, prompt: str,
                      max_tokens: int) -> dict:
    payload = {
        "model": "adguard-judge",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": True,
    }
    started = time.perf_counter()
    ttft = None
    chunks = 0
    text_parts: list[str] = []
    async with client.stream("POST", f"{base_url}/v1/chat/completions", json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = event.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content") or ""
            if content and ttft is None:
                ttft = time.perf_counter() - started
            if content:
                chunks += 1
                text_parts.append(content)
    finished = time.perf_counter()
    return {
        "ttft_s": ttft if ttft is not None else (finished - started),
        "e2e_s": finished - started,
        "output_chars": len("".join(text_parts)),
        "events": chunks,
    }


async def run(args) -> dict:
    prompts = (PROMPTS * ((args.requests // len(PROMPTS)) + 1))[: args.requests]
    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        async def worker(prompt: str) -> None:
            async with semaphore:
                try:
                    results.append(await one_request(client, args.base_url, prompt, args.max_tokens))
                except Exception as exc:  # noqa: BLE001 - benchmark must record all failures
                    errors.append(f"{type(exc).__name__}: {exc}")

        wall_start = time.perf_counter()
        await asyncio.gather(*(worker(prompt) for prompt in prompts))
        wall_end = time.perf_counter()

    elapsed = wall_end - wall_start
    ttfts = [r["ttft_s"] for r in results]
    e2es = [r["e2e_s"] for r in results]
    total_chars = sum(r["output_chars"] for r in results)
    metrics = {
        "engine_label": args.label,
        "concurrency": args.concurrency,
        "requests": args.requests,
        "errors": len(errors),
        "error_samples": errors[:5],
        "wall_seconds": round(elapsed, 3),
        "throughput_req_per_s": round(len(results) / elapsed, 3) if elapsed else 0.0,
        "output_chars_per_s": round(total_chars / elapsed, 1) if elapsed else 0.0,
        "ttft_p50": round(percentile(ttfts, 50), 4),
        "ttft_p95": round(percentile(ttfts, 95), 4),
        "ttft_p99": round(percentile(ttfts, 99), 4),
        "e2e_p50": round(percentile(e2es, 50), 4),
        "e2e_p95": round(percentile(e2es, 95), 4),
        "e2e_p99": round(percentile(e2es, 99), 4),
        "tpot_p50_ms": round(
            statistics.median([r["e2e_s"] - r["ttft_s"] for r in results]) * 1000, 1
        ) if results else 0.0,
    }
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="M5 inference benchmark client")
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--label", default="vllm")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--requests", type=int, default=96)
    parser.add_argument("--max-tokens", type=int, default=192)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
