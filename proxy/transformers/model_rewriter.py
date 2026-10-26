"""ModelRewriter: алиасы -> полные ID + валидация по каталогу.

Политика при неизвестной модели (catalog.unknown_model):
- reject (default): 404 с did-you-mean — опечатка не должна уходить в деньги;
- passthrough: пропустить как есть (для power-user режима).
"""

import logging
from difflib import get_close_matches

from fastapi import HTTPException

from proxy.catalog import get_catalog
from proxy.config import get_config
from proxy.transformers.base import RequestContext, Transformer

logger = logging.getLogger(__name__)


class ModelRewriter(Transformer):
    name = "model_rewriter"

    async def transform_request(self, ctx: RequestContext) -> RequestContext:
        if ctx.method not in ("POST", "PUT", "PATCH") or not ctx.body:
            return ctx

        model = ctx.body.get("model")
        if not isinstance(model, str) or not model:
            return ctx

        cfg = get_config()
        profile = cfg.profiles.get(ctx.profile_name)

        # 1) алиас -> полный ID
        resolved = model
        if profile and model in profile.model_map:
            resolved = profile.model_map[model]
            ctx.meta["alias"] = model
            logger.info("ModelRewriter: алиас %s -> %s", model, resolved)

        # 2) валидация по каталогу RouterAI
        catalog = get_catalog()
        models = await catalog.get_models()
        if resolved not in models:
            if cfg.catalog.unknown_model == "passthrough":
                logger.warning("ModelRewriter: модель %s вне каталога, passthrough", resolved)
            else:
                candidates = list(models.keys())
                if profile:
                    candidates.extend(profile.model_map.keys())
                suggestions = get_close_matches(model, candidates, n=3, cutoff=0.5)
                detail = f"Модель '{model}' не найдена в каталоге RouterAI."
                if suggestions:
                    detail += f" Возможно, вы имели в виду: {', '.join(suggestions)}"
                logger.warning("ModelRewriter: %s", detail)
                raise HTTPException(status_code=404, detail=detail)

        ctx.body["model"] = resolved
        ctx.meta["resolved_model"] = resolved
        return ctx