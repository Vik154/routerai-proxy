"""ProviderRoutingInjector: правила профиля -> объект provider в теле запроса.

Семантика мержа:
- order/only/country/allow_fallbacks: значение клиента побеждает (его явное намерение);
- ignore: UNION(клиент + профиль + PriceGuard) — запрет есть запрет, списки объединяются;
- provider инжектится только в routing-эндпоинты (chat/completions, responses, messages),
  чтобы не сломать embeddings/audio лишним полем.
"""

import logging
from typing import Any, Dict, List

from proxy.config import get_config
from proxy.transformers.base import RequestContext, Transformer

logger = logging.getLogger(__name__)

ROUTING_PATHS = ("chat/completions", "responses", "messages")


class ProviderRoutingInjector(Transformer):
    name = "provider_routing"

    async def transform_request(self, ctx: RequestContext) -> RequestContext:
        if ctx.method not in ("POST", "PUT", "PATCH") or ctx.body is None:
            return ctx
        if not ctx.upstream_path.startswith(ROUTING_PATHS):
            return ctx

        cfg = get_config()
        profile = cfg.profiles.get(ctx.profile_name)
        if profile is None:
            return ctx
        
        # Получаем модель (уже разрешённую ModelRewriter'ом)
        model_id = ctx.meta.get("resolved_model") or ctx.body.get("model")
        if not isinstance(model_id, str):
            return ctx
        
        # Используем resolver для получения эффективных настроек
        from proxy.profile_resolver import resolve_provider_config
        pc = resolve_provider_config(profile, model_id)

        client_provider = ctx.body.get("provider")
        if client_provider is not None and not isinstance(client_provider, dict):
            return ctx

        final: Dict[str, Any] = dict(client_provider or {})

        if pc.order and "order" not in final:
            final["order"] = list(pc.order)
        if pc.only and "only" not in final:
            final["only"] = list(pc.only)
        if pc.country and "country" not in final:
            final["country"] = pc.country
        if "allow_fallbacks" not in final:
            final["allow_fallbacks"] = pc.allow_fallbacks

        # ignore: union клиента, профиля и PriceGuard
        ignore: List[str] = list(final.get("ignore") or [])
        ignore.extend(pc.ignore)
        ignore.extend(ctx.meta.get("price_ignore_tags") or [])
        if ignore:
            final["ignore"] = sorted(set(ignore))

        ctx.body["provider"] = final
        logger.debug("ProviderRouting: provider=%s", final)
        return ctx
    