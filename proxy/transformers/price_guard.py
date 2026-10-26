"""PriceGuard: автоматический ignore провайдеров дороже ценового потолка.

Цены берутся из кэша endpoint'ов RouterAI (руб/1M prompt токенов).
Принцип informed refusal: денежные решения принимает человек, поэтому
при политике block прокси громко отказывает (409) с таблицей цен и списком
путей обхода, а не молча угадывает.

Override: заголовок X-Proxy-Price-Override: true отключает guard для запроса.
"""

import logging

from fastapi import HTTPException

from proxy.catalog import get_catalog
from proxy.config import get_config
from proxy.transformers.base import RequestContext, Transformer

logger = logging.getLogger(__name__)

ROUTING_PATHS = ("chat/completions", "responses", "messages")
OVERRIDE_HEADER = "x-proxy-price-override"
TRUTHY = ("1", "true", "yes", "on")


class PriceGuard(Transformer):
    name = "price_guard"

    async def transform_request(self, ctx: RequestContext) -> RequestContext:
        if ctx.method not in ("POST", "PUT", "PATCH") or ctx.body is None:
            return ctx
        if not ctx.upstream_path.startswith(ROUTING_PATHS):
            return ctx

        cfg = get_config()
        profile = cfg.profiles.get(ctx.profile_name)
        if profile is None:
            return ctx

        model = ctx.meta.get("resolved_model") or ctx.body.get("model")
        if not isinstance(model, str):
            return ctx

        # Используем resolver для получения эффективных настроек
        from proxy.profile_resolver import resolve_price_ceiling
        price_ceiling_config = resolve_price_ceiling(profile, model)
        ceiling = price_ceiling_config.prompt_per_mtoken
        
        if ceiling is None:
            return ctx

        # Осознанный override на запрос
        if ctx.headers.get(OVERRIDE_HEADER, "").lower() in TRUTHY:
            logger.warning("PriceGuard: override для %s (заголовок %s)", model, OVERRIDE_HEADER)
            ctx.meta["price_override"] = True
            return ctx

        prices = await get_catalog().prompt_prices(model)
        if not prices:
            logger.debug("PriceGuard: нет токен-цен для %s, пропускаю", model)
            return ctx

        expensive = [(tag, p) for tag, p in prices if p > ceiling]
        affordable = [(tag, p) for tag, p in prices if p <= ceiling]
        policy = price_ceiling_config.on_all_expensive

        if not affordable and expensive:
            if policy == "block":
                table = "; ".join(f"{tag}={p:.1f}" for tag, p in prices[:5])
                detail = (
                    f"PriceGuard: ВСЕ провайдеры модели {model} дороже потолка "
                    f"профиля '{ctx.profile_name}'.\n"
                    f"Потолок: {ceiling:g} руб/1M prompt токенов.\n"
                    f"Цены провайдеров (руб/1M): {table}\n"
                    "Варианты: 1) заголовок X-Proxy-Price-Override: true; "
                    "2) переключить профиль (X-Proxy-Profile) на профиль без жёсткого потолка; "
                    "3) поднять price_ceiling в config.yaml (применится hot reload)."
                )
                logger.warning("PriceGuard: BLOCK %s (%s)", model, table)
                raise HTTPException(status_code=409, detail=detail)

            if policy == "allow_cheapest":
                cheapest_tag = prices[0][0]
                ctx.meta["price_ignore_tags"] = [t for t, _ in expensive if t != cheapest_tag]
                logger.warning(
                    "PriceGuard: все дороже потолка, разрешён cheapest=%s для %s",
                    cheapest_tag, model,
                )
                return ctx

            # warn
            logger.warning(
                "PriceGuard: WARN все провайдеры %s дороже потолка (cheapest=%s=%.1f)",
                model, prices[0][0], prices[0][1],
            )
            return ctx

        if expensive:
            ctx.meta["price_ignore_tags"] = [t for t, _ in expensive]
            logger.info(
                "PriceGuard: %s — в ignore %d провайдер(ов) дороже %g руб/1M: %s",
                model, len(expensive), ceiling,
                ", ".join(t for t, _ in expensive),
            )

        return ctx