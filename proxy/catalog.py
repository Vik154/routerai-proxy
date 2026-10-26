"""Каталог RouterAI: список моделей + цены endpoint'ов.

Два уровня кэша:
- Список моделей: 1 запрос GET /models, TTL = catalog.models_ttl.
- Endpoint'ы модели: lazy, по 1 запросу на модель, TTL = catalog.price_ttl.
  (Не тянем цены всех моделей при старте — их сотни.)

При ошибке сети отдаём СТАРЫЙ кэш (stale-while-error), кроме случая
"кэша ещё нет" — тогда пустой результат + error-лог.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

import httpx
from pydantic import BaseModel

from proxy.config import get_config

logger = logging.getLogger(__name__)


class ModelEndpoint(BaseModel):
    """Endpoint модели у конкретного провайдера."""

    tag: str
    provider_name: str = ""
    countries: List[str] = []
    pricing: Dict[str, float] = {}
    pricing_units: Dict[str, str] = {}
    status: int = 0
    context_length: Optional[int] = None

    @property
    def prompt_price_per_mtok(self) -> Optional[float]:
        """Цена prompt в рублях за 1M токенов, или None если единицы не токены."""
        price = self.pricing.get("prompt")
        if price is None:
            return None
        if self.pricing_units.get("prompt", "token") != "token":
            # Цена за request/second/... — с токенами не сравниваемо
            return None
        return price * 1_000_000


class ModelInfo(BaseModel):
    id: str
    name: str = ""


class Catalog:
    """Кэш каталога с asyncio-локом от параллельных fetch."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._models: Dict[str, ModelInfo] = {}
        self._models_fetched_at: float = 0.0
        self._endpoints: Dict[str, Tuple[float, List[ModelEndpoint]]] = {}
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    # ------------------------------------------------------------------ models

    async def get_models(self, force: bool = False) -> Dict[str, ModelInfo]:
        """Список моделей {id: ModelInfo} из кэша или сети."""
        cfg = get_config()
        async with self._lock:
            fresh = (time.monotonic() - self._models_fetched_at) < cfg.catalog.models_ttl
            if self._models and (fresh or force is False and fresh):
                return self._models
            if force or not self._models or fresh:
                if fresh and not force and self._models:
                    return self._models
            try:
                models = await self._fetch_models()
                self._models = models
                self._models_fetched_at = time.monotonic()
                logger.info("Каталог моделей обновлён: %d шт.", len(models))
                return self._models
            except Exception as e:  # noqa: BLE001 — stale-while-error
                logger.error("Не удалось обновить каталог моделей: %s", e)
                return self._models

    async def _fetch_models(self) -> Dict[str, ModelInfo]:
        cfg = get_config()
        client = await self._get_client()
        resp = await client.get(f"{cfg.routerai.api_url}/models")
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            items = []
        models: Dict[str, ModelInfo] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id:
                models[model_id] = ModelInfo(id=model_id, name=item.get("name", model_id))
        return models

    # --------------------------------------------------------------- endpoints

    async def get_endpoints(self, model_id: str) -> List[ModelEndpoint]:
        """Endpoint'ы модели (lazy-кэш по price_ttl)."""
        cfg = get_config()
        async with self._lock:
            cached = self._endpoints.get(model_id)
            if cached and (time.monotonic() - cached[0]) < cfg.catalog.price_ttl:
                return cached[1]

        endpoints = await self._fetch_endpoints(model_id)
        if endpoints:
            async with self._lock:
                self._endpoints[model_id] = (time.monotonic(), endpoints)
        return endpoints

    async def _fetch_endpoints(self, model_id: str) -> List[ModelEndpoint]:
        cfg = get_config()
        parts = model_id.split("/")
        if len(parts) != 2:
            logger.warning("Некорректный model id для endpoints: %s", model_id)
            return []
        author, model = parts
        client = await self._get_client()
        url = f"{cfg.routerai.api_url}/models/{author}/{model}/endpoints"
        try:
            resp = await client.get(url)  # авторизация не требуется
            if resp.status_code != 200:
                logger.warning("endpoints для %s: HTTP %d", model_id, resp.status_code)
                return []
            payload = resp.json()
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            raw_eps = data.get("endpoints", []) if isinstance(data, dict) else []
        except Exception as e:  # noqa: BLE001
            logger.error("endpoints для %s: %s", model_id, e)
            return []

        endpoints: List[ModelEndpoint] = []
        for ep in raw_eps:
            if not isinstance(ep, dict):
                continue
            tag = ep.get("tag")
            if not isinstance(tag, str) or not tag:
                continue
            endpoints.append(
                ModelEndpoint(
                    tag=tag,
                    provider_name=ep.get("provider_name", ""),
                    countries=ep.get("countries") or [],
                    pricing=ep.get("pricing") or {},
                    pricing_units=ep.get("pricing_units") or {},
                    status=ep.get("status", 0),
                    context_length=ep.get("context_length"),
                )
            )
        return endpoints

    # ------------------------------------------------------------------ prices

    async def prompt_prices(self, model_id: str) -> List[Tuple[str, float]]:
        """[(tag, руб/1M prompt)] по всем endpoint'ам, отсортировано по цене."""
        endpoints = await self.get_endpoints(model_id)
        prices = [
            (ep.tag, p)
            for ep in endpoints
            if (p := ep.prompt_price_per_mtok) is not None
        ]
        return sorted(prices, key=lambda x: x[1])

    def cached_model_ids(self) -> List[str]:
        """ID моделей из текущего кэша (без сети)."""
        return list(self._models.keys())

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_catalog: Optional[Catalog] = None


def get_catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog()
    return _catalog


async def close_catalog() -> None:
    global _catalog
    if _catalog is not None:
        await _catalog.close()
        _catalog = None