"""Контракт трансформера и контекст запроса.

Трансформер — компонент пайплайна с четырьмя точками вмешательства:
- transform_request: правка тела/метаданных ДО отправки upstream;
- on_response_start: статус и заголовки ответа (до тела);
- on_stream_chunk: каждый SSE-чанк стрима (тело НЕ буферизуется!);
- on_response_body: полное тело НЕ-stream ответа.

Все методы no-op по умолчанию: наследник переопределяет только нужное.
Трансформеры обязаны быть async и не блокировать событие-луп.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


@dataclass
class RequestContext:
    """Контекст одной транзакции, проходит весь пайплайн."""

    method: str
    client_path: str                      # как пришло от клиента: "/v1/chat/completions"
    upstream_path: str                    # как уйдёт в RouterAI: "chat/completions"
    headers: Dict[str, str]               # заголовки клиента (ключи в нижнем регистре)
    body: Optional[Dict[str, Any]] = None
    profile_name: str = "default"
    meta: Dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)

    @property
    def is_stream(self) -> bool:
        return bool(self.body and self.body.get("stream"))


class Transformer:
    """Базовый трансформер. Порядок в пайплайне задаёт app.py."""

    name: str = "transformer"

    async def transform_request(self, ctx: RequestContext) -> RequestContext:
        return ctx

    async def on_response_start(self, ctx: RequestContext, status: int, headers: Mapping[str, str]) -> None:
        return None

    async def on_stream_chunk(self, ctx: RequestContext, chunk: bytes) -> None:
        return None

    async def on_response_body(self, ctx: RequestContext, body: bytes) -> None:
        return None

    async def on_stream_end(self, ctx: RequestContext) -> None:
        return None