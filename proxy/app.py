"""Транспортный слой: catch-all прокси + SSE pass-through + служебные эндпоинты.

Правила:
- Точные маршруты (/health, /stats, /v1/models) регистрируются ДО catch-all.
- Всё остальное прозрачно уходит в RouterAI; вмешиваемся только через пайплайн.
- Тело, которое не распарсилось как JSON, пробрасывается байтами как есть.
- Стрим не буферизуется: чанки уходят клиенту по мере поступления.
- Ошибка upstream (не-200) в stream-запросе отдаётся обычным Response с телом ошибки.
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from proxy.catalog import close_catalog, get_catalog
from proxy.config import get_config
from proxy.stats import get_stats_manager, shutdown_stats
from proxy.transformers.base import RequestContext, Transformer
from proxy.transformers.cost_logger import CostLogger
from proxy.transformers.model_rewriter import ModelRewriter
from proxy.transformers.price_guard import PriceGuard
from proxy.transformers.provider_routing import ProviderRoutingInjector
from proxy.upstream import CircuitBreakerOpenError, close_upstream, get_upstream

logger = logging.getLogger(__name__)

START_MONOTONIC = time.monotonic()

HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "content-encoding",
    "content-length", "date", "server", "set-cookie", "upgrade",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("RouterAI Proxy: startup")
    yield
    logger.info("RouterAI Proxy: shutdown, flush статистики...")
    shutdown_stats()
    await close_catalog()
    await close_upstream()
    logger.info("RouterAI Proxy: остановлен корректно")


app = FastAPI(title="RouterAI Proxy", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _lower_headers(request: Request) -> Dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}


def _resolve_profile(headers: Dict[str, str]) -> str:
    cfg = get_config()
    ps = cfg.profile_selection
    if ps.method != "header":
        return ps.default_profile
    name = headers.get(ps.header_name.lower())
    if not name:
        return ps.default_profile
    if name in cfg.profiles:
        return name
    if ps.unknown_profile == "fallback_default":
        logger.warning("Неизвестный профиль '%s', fallback на '%s'", name, ps.default_profile)
        return ps.default_profile
    raise HTTPException(
        status_code=400,
        detail=(
            f"Неизвестный профиль '{name}'. "
            f"Доступные: {', '.join(sorted(cfg.profiles))}"
        ),
    )


def _normalize_path(path: str) -> str:
    """'/v1/chat/completions' -> 'chat/completions' (api_url уже содержит /v1)."""
    p = path.lstrip("/")
    if p.startswith("v1/"):
        p = p[len("v1/"):]
    return p


def _safe_headers(headers: httpx.Headers) -> Dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}


def _build_pipeline() -> List[Transformer]:
    """Порядок важен: алиасы -> цены -> роутинг -> учёт."""
    return [ModelRewriter(), PriceGuard(), ProviderRoutingInjector(), CostLogger()]


def _proxy_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": "proxy_error", "code": code}},
    )


# ---------------------------------------------------------------------------
# Служебные эндпоинты
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "uptime_s": round(time.monotonic() - START_MONOTONIC, 1),
        "circuit_breaker": get_upstream().breaker.info(),
        "profiles": sorted(get_config().profiles.keys()),
    }


@app.get("/stats")
async def stats():
    return get_stats_manager().get_stats()


@app.get("/v1/models")
@app.get("/models")
async def list_models(request: Request):
    """Каталог для dropdown Cline: модели RouterAI + алиасы профиля."""
    profile_name = _resolve_profile(_lower_headers(request))
    cfg = get_config()
    profile = cfg.profiles[profile_name]
    catalog = get_catalog()

    models = await catalog.get_models()
    keep = set(models.keys())

    ceiling = profile.price_ceiling.prompt_per_mtoken
    if profile.hide_over_ceiling and ceiling is not None and keep:
        sem = asyncio.Semaphore(8)  # не бомбим RouterAI параллельными fetch цен

        async def _visible(model_id: str) -> bool:
            async with sem:
                prices = await catalog.prompt_prices(model_id)
                if not prices:
                    return True  # нет токен-цен (image/audio) — не прячем
                return prices[0][1] <= ceiling

        flags = await asyncio.gather(*(_visible(mid) for mid in sorted(keep)))
        keep = {mid for mid, ok in zip(sorted(keep), flags) if ok}
        logger.info("hide_over_ceiling: оставлено %d из %d моделей", len(keep), len(models))

    now = int(time.time())
    data: List[Dict[str, Any]] = [
        {
            "id": mid,
            "object": "model",
            "created": now,
            "owned_by": mid.split("/")[0],
        }
        for mid in sorted(keep)
    ]
    for alias, target in sorted(profile.model_map.items()):
        if target in keep:
            data.append({"id": alias, "object": "model", "created": now, "owned_by": "alias"})

    return {"object": "list", "data": data}


@app.get("/providers")
async def list_providers(request: Request, model: Optional[str] = None, country: Optional[str] = None):
    """Список провайдеров с ценами.

    Примеры:
        GET /providers?model=anthropic/claude-sonnet-4.5
        GET /providers?country=ru
        GET /providers  (все провайдеры по всем моделям)
    """
    catalog = get_catalog()

    if model:
        # Провайдеры для конкретной модели
        models_dict = await catalog.get_models()
        if model not in models_dict:
            raise HTTPException(status_code=404, detail=f"Модель '{model}' не найдена")

        prices = await catalog.prompt_prices(model)
        if not prices:
            return {"model": model, "providers": [], "note": "Нет данных о ценах (модель без токен-тарифа)"}

        endpoints = await catalog.get_endpoints(model)
        providers = []
        for ep in endpoints:
            prompt_price = ep.prompt_price_per_mtok
            completion_price = None
            if "completion" in ep.pricing and ep.pricing_units.get("completion") == "token":
                completion_price = ep.pricing["completion"] * 1_000_000

            # Фильтр по стране
            if country and ep.countries and country not in ep.countries:
                continue

            providers.append({
                "tag": ep.tag,
                "name": ep.provider_name,
                "countries": ep.countries,
                "prompt_per_mtok_rub": round(prompt_price, 2) if prompt_price else None,
                "completion_per_mtok_rub": round(completion_price, 2) if completion_price else None,
                "context_length": ep.context_length,
                "status": ep.status,
            })

        # Сортировка по цене prompt
        providers.sort(key=lambda x: x.get("prompt_per_mtok_rub") or 999999)

        return {
            "model": model,
            "count": len(providers),
            "providers": providers,
        }

    else:
        # Все провайдеры по всем моделям (агрегированный список)
        models_dict = await catalog.get_models()
        all_providers: Dict[str, Dict] = {}

        for model_id in sorted(models_dict.keys()):
            endpoints = await catalog.get_endpoints(model_id)
            for ep in endpoints:
                if ep.tag not in all_providers:
                    prompt_price = ep.prompt_price_per_mtok
                    all_providers[ep.tag] = {
                        "tag": ep.tag,
                        "name": ep.provider_name,
                        "countries": ep.countries,
                        "models": [],
                        "sample_prompt_per_mtok_rub": round(prompt_price, 2) if prompt_price else None,
                    }
                all_providers[ep.tag]["models"].append(model_id)

                # Фильтр по стране
                if country and ep.countries and country not in ep.countries:
                    continue

        providers_list = list(all_providers.values())
        providers_list.sort(key=lambda x: x.get("sample_prompt_per_mtok_rub") or 999999)

        return {
            "count": len(providers_list),
            "providers": providers_list,
        }

# ---------------------------------------------------------------------------
# Catch-all прокси
# ---------------------------------------------------------------------------

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def catch_all(request: Request, path: str):
    headers = _lower_headers(request)
    profile_name = _resolve_profile(headers)

    raw = await request.body()
    body: Optional[Dict[str, Any]] = None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                body = parsed
        except Exception:  # noqa: BLE001 — не JSON: пробросим байтами (прозрачность)
            body = None

    ctx = RequestContext(
        method=request.method,
        client_path=request.url.path,
        upstream_path=_normalize_path(path),
        headers=headers,
        body=body,
        profile_name=profile_name,
    )
    if raw and body is None:
        ctx.meta["raw_body"] = raw

    model_preview = (body or {}).get("model") if body else None
    logger.info(
        "req: %s %s profile=%s model=%s stream=%s",
        request.method, ctx.client_path, profile_name, model_preview, ctx.is_stream,
    )

    pipeline = _build_pipeline()
    try:
        for t in pipeline:
            ctx = await t.transform_request(ctx)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("transformer %s упал", t.name)
        return _proxy_error(500, "transformer_error", f"Ошибка обработки запроса: {e}")

    send_json = ctx.body if ctx.body is not None else None
    send_content = ctx.meta.get("raw_body")

    upstream = get_upstream()

    try:
        if not ctx.is_stream:
            resp = await upstream.request(
                ctx.method, ctx.upstream_path, json=send_json, content=send_content
            )
            await _fire_response_start(pipeline, ctx, resp)
            for t in pipeline:
                await t.on_response_body(ctx, resp.content)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=_safe_headers(resp.headers),
            )

        # ---------------- stream: решение по статусу ДО создания StreamingResponse
        cm = upstream.stream(ctx.method, ctx.upstream_path, json=send_json, content=send_content)
        resp = await cm.__aenter__()
        try:
            await _fire_response_start(pipeline, ctx, resp)
            if resp.status_code != 200:
                err_body = await resp.aread()
                await cm.__aexit__(None, None, None)
                for t in pipeline:
                    await t.on_response_body(ctx, err_body)
                return Response(
                    content=err_body,
                    status_code=resp.status_code,
                    headers=_safe_headers(resp.headers),
                )

            async def _gen():
                try:
                    async for chunk in resp.aiter_bytes():
                        for t in pipeline:
                            await t.on_stream_chunk(ctx, chunk)
                        yield chunk  # сразу клиенту, без буферизации
                finally:
                    for t in pipeline:
                        await t.on_stream_end(ctx)
                    await cm.__aexit__(None, None, None)

            return StreamingResponse(
                _gen(),
                status_code=200,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        except Exception:
            await cm.__aexit__(None, None, None)
            raise

    except CircuitBreakerOpenError:
        return _proxy_error(
            503,
            "circuit_breaker_open",
            "RouterAI временно недоступен (circuit breaker OPEN). Подождите и повторите.",
        )
    except httpx.HTTPError as e:
        logger.error("upstream error: %s", e)
        return _proxy_error(502, "upstream_error", f"Ошибка соединения с RouterAI: {e}")


async def _fire_response_start(pipeline: List[Transformer], ctx: RequestContext, resp: httpx.Response) -> None:
    for t in pipeline:
        await t.on_response_start(ctx, resp.status_code, resp.headers)


