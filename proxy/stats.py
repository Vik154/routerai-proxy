"""Статистика без БД: агрегаты в памяти + append-only jsonl + атомарные снапшоты.

- requests.jsonl: одна строка на запрос, пишется СРАЗУ (переживает SIGKILL).
- stats.json: агрегаты, снапшот раз в flush_interval атомарно (tmp + rename).
- При жёстком убийстве теряется не более flush_interval секунд агрегатов.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from proxy.config import get_config

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StatsManager:
    def __init__(self) -> None:
        cfg = get_config().stats
        self._log_path = Path(cfg.requests_log)
        self._snap_path = Path(cfg.stats_snapshot)
        self._flush_interval = max(5, cfg.flush_interval)
        self._lock = threading.Lock()
        self._stats: Dict[str, Any] = {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "by_model": {},
            "by_provider": {},
            "started_at": _now_iso(),
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._snap_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_snapshot()
        threading.Thread(target=self._loop, name="stats-flush", daemon=True).start()

    # ------------------------------------------------------------------ load

    def _load_snapshot(self) -> None:
        if not self._snap_path.exists():
            return
        try:
            data = json.loads(self._snap_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._stats.update(data)
                logger.info("stats: снапшот загружен (%s)", self._snap_path)
        except Exception as e:  # noqa: BLE001
            logger.error("stats: снапшот повреждён, начинаю с нуля: %s", e)

    # ---------------------------------------------------------------- record

    def record(
        self,
        *,
        model: str,
        profile: str,
        status: int,
        provider: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        """Записать запрос: строка в jsonl сразу + агрегаты под локом."""
        entry = {
            "ts": _now_iso(),
            "model": model,
            "profile": profile,
            "provider": provider,
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 1),
        }
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.error("stats: запись jsonl не удалась: %s", e)

        with self._lock:
            s = self._stats
            s["total_requests"] += 1
            s["total_prompt_tokens"] += prompt_tokens
            s["total_completion_tokens"] += completion_tokens
            s["total_tokens"] += total_tokens

            m = s["by_model"].setdefault(
                model,
                {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            m["requests"] += 1
            m["prompt_tokens"] += prompt_tokens
            m["completion_tokens"] += completion_tokens
            m["total_tokens"] += total_tokens

            p = s["by_provider"].setdefault(provider or "unknown", {"requests": 0, "total_tokens": 0})
            p["requests"] += 1
            p["total_tokens"] += total_tokens

    # ----------------------------------------------------------------- flush

    def _loop(self) -> None:
        while True:
            time.sleep(self._flush_interval)
            self.flush_snapshot()

    def flush_snapshot(self) -> None:
        with self._lock:
            payload = json.dumps(self._stats, ensure_ascii=False, indent=2)
        tmp = self._snap_path.with_suffix(".tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._snap_path)  # атомарно: читатель не увидит половину
        except Exception as e:  # noqa: BLE001
            logger.error("stats: flush снапшота не удался: %s", e)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._stats))  # глубокая копия


_manager: Optional[StatsManager] = None
_manager_lock = threading.Lock()


def get_stats_manager() -> StatsManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = StatsManager()
        return _manager


def shutdown_stats() -> None:
    """Flush при остановке (вызывается из app shutdown и atexit)."""
    with _manager_lock:
        if _manager is not None:
            _manager.flush_snapshot()