"""CostLogger: учёт токенов/латентности/статусов -> stats (jsonl + агрегаты).

- non-stream: usage берётся из тела ответа;
- stream: usage парсится из SSE-чанков на лету (без буферизации тела!).
  Чтобы usage вообще пришёл, для chat/completions добавляем
  stream_options.include_usage=true, если клиент сам не задал stream_options.
- Ровно одна запись stats.record на транзакцию:
  non-stream -> on_response_body, stream -> on_stream_end.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

from proxy.config import get_config
from proxy.stats import get_stats_manager
from proxy.transformers.base import RequestContext, Transformer

logger = logging.getLogger(__name__)

PROVIDER_HEADERS = ("x-provider", "x-routerai-provider", "x-upstream-provider")


def _extract_usage(payload: Any) -> Optional[Dict[str, int]]:
    if isinstance(payload, dict):
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


class CostLogger(Transformer):
    name = "cost_logger"

    async def transform_request(self, ctx: RequestContext) -> RequestContext:
        # Просим usage в стриме, если клиент не задал stream_options сам
        if (
            ctx.is_stream
            and ctx.upstream_path.startswith("chat/completions")
            and ctx.body is not None
            and "stream_options" not in ctx.body
        ):
            ctx.body["stream_options"] = {"include_usage": True}
        return ctx

    async def on_response_start(self, ctx: RequestContext, status: int, headers) -> None:
        ctx.meta["response_status"] = status
        for key in PROVIDER_HEADERS:
            if key in headers:
                ctx.meta["provider"] = headers[key]
                break

    async def on_stream_chunk(self, ctx: RequestContext, chunk: bytes) -> None:
        # Инкрементальный SSE-парсер: хвост незавершённой строки живёт в meta
        buf: bytes = ctx.meta.get("sse_tail", b"") + chunk
        *lines, tail = buf.split(b"\n")
        ctx.meta["sse_tail"] = tail
        for line in lines:
            line = line.strip()
            if not line.startswith(b"data:") or b"usage" not in line:
                continue
            payload = line[5:].strip()
            if payload in (b"[DONE]", b""):
                continue
            try:
                usage = _extract_usage(json.loads(payload))
            except Exception:  # noqa: BLE001 — мусор в стриме не роняет прокси
                continue
            if usage:
                ctx.meta["usage"] = usage

    async def on_response_body(self, ctx: RequestContext, body: bytes) -> None:
        if ctx.is_stream:
            return
        try:
            usage = _extract_usage(json.loads(body))
        except Exception:  # noqa: BLE001
            usage = None
        if usage:
            ctx.meta["usage"] = usage
        self._record(ctx)

    async def on_stream_end(self, ctx: RequestContext) -> None:
        if ctx.is_stream:
            self._record(ctx)

    # ------------------------------------------------------------------ inner

    def _record(self, ctx: RequestContext) -> None:
        usage = ctx.meta.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (prompt + completion))
        latency_ms = (time.monotonic() - ctx.started_at) * 1000

        model = ctx.meta.get("resolved_model") or (ctx.body or {}).get("model") or "unknown"
        get_stats_manager().record(
            model=model,
            profile=ctx.profile_name,
            status=int(ctx.meta.get("response_status") or 0),
            provider=ctx.meta.get("provider"),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            latency_ms=latency_ms,
        )
        logger.info(
            "tx: model=%s profile=%s status=%s tokens=%d/%d latency=%.0fms",
            model, ctx.profile_name, ctx.meta.get("response_status"),
            prompt, completion, latency_ms,
        )
        # Конфиг мог поменять flush-путь — не кэшируем ничего между вызовами
        _ = get_config()