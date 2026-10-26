"""Upstream-слой: httpx-пул + собственный circuit breaker.

Правила breaker'а:
- failure = 5xx от RouterAI или сетевая ошибка/таймаут.
- success = любой ответ < 500 (4xx — сервер жив, ошибка семантическая).
- CLOSED -> OPEN после failure_threshold подряд; OPEN -> HALF_OPEN через
  recovery_timeout; HALF_OPEN -> CLOSED после success_threshold успехов.
"""

import logging
import threading
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, Dict, Optional

import httpx

from proxy.config import CircuitBreakerConfig, get_config

logger = logging.getLogger(__name__)


class CircuitBreakerOpenError(Exception):
    """Breaker открыт: запрос отклонён без обращения к upstream."""


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, cfg: CircuitBreakerConfig) -> None:
        self._cfg = cfg
        self._state = _State.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> _State:
        with self._lock:
            if (
                self._state is _State.OPEN
                and time.monotonic() - self._opened_at >= self._cfg.recovery_timeout
            ):
                self._state = _State.HALF_OPEN
                self._successes = 0
                logger.info("Circuit breaker: OPEN -> HALF_OPEN")
            return self._state

    def allow_request(self) -> bool:
        return self.state is not _State.OPEN

    def record_success(self) -> None:
        with self._lock:
            if self._state is _State.HALF_OPEN:
                self._successes += 1
                if self._successes >= self._cfg.success_threshold:
                    self._state = _State.CLOSED
                    self._failures = 0
                    logger.info("Circuit breaker: HALF_OPEN -> CLOSED")
            else:
                self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state is _State.HALF_OPEN:
                self._state = _State.OPEN
                self._opened_at = time.monotonic()
                logger.warning("Circuit breaker: HALF_OPEN -> OPEN")
                return
            self._failures += 1
            if self._failures >= self._cfg.failure_threshold:
                self._state = _State.OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "Circuit breaker: CLOSED -> OPEN (failures=%d)", self._failures
                )

    def info(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "failures": self._failures,
        }


class UpstreamClient:
    """Один httpx.AsyncClient на процесс + подмена Authorization."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self.breaker = CircuitBreaker(get_config().circuit_breaker)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                    keepalive_expiry=30.0,
                ),
            )
        return self._client

    def _url(self, path: str) -> str:
        cfg = get_config()
        return f"{cfg.routerai.api_url}/{path.lstrip('/')}"

    def _auth_headers(self) -> Dict[str, str]:
        cfg = get_config()
        return {"Authorization": f"Bearer {cfg.routerai.api_key}"}

    async def request(
        self,
        method: str,
        path: str,
        json: Any = None,
        content: Optional[bytes] = None,
    ) -> httpx.Response:
        """Обычный (не stream) запрос. Тело ответа уже прочитано."""
        if not self.breaker.allow_request():
            raise CircuitBreakerOpenError("RouterAI недоступен: circuit breaker OPEN")
        client = await self._get_client()
        try:
            resp = await client.request(
                method,
                self._url(path),
                json=json,
                content=content,
                headers=self._auth_headers(),
            )
        except httpx.HTTPError as e:
            self.breaker.record_failure()
            logger.error("upstream network error: %s", e)
            raise
        if resp.status_code >= 500:
            self.breaker.record_failure()
        else:
            self.breaker.record_success()
        return resp

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        path: str,
        json: Any = None,
        content: Optional[bytes] = None,
    ):
        """Стриминг: отдаёт httpx.Response, тело читает вызывающий по чанкам.

        Retry НЕ возможен после начала стрима — это контракт транспортного слоя.
        """
        if not self.breaker.allow_request():
            raise CircuitBreakerOpenError("RouterAI недоступен: circuit breaker OPEN")
        client = await self._get_client()
        try:
            async with client.stream(
                method,
                self._url(path),
                json=json,
                content=content,
                headers=self._auth_headers(),
            ) as resp:
                if resp.status_code >= 500:
                    self.breaker.record_failure()
                else:
                    self.breaker.record_success()
                yield resp
        except httpx.HTTPError as e:
            self.breaker.record_failure()
            logger.error("upstream stream network error: %s", e)
            raise

_upstream: Optional[UpstreamClient] = None


def get_upstream() -> UpstreamClient:
    global _upstream
    if _upstream is None:
        _upstream = UpstreamClient()
    return _upstream


async def close_upstream() -> None:
    global _upstream
    if _upstream is not None and _upstream._client and not _upstream._client.is_closed:
        await _upstream._client.aclose()
    _upstream = None