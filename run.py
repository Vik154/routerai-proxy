"""
Точка входа: python run.py
Запускает uvicorn с graceful shutdown (10c на доживание стримов).
Flush статистики при остановке делается в proxy.app shutdown-event
и через atexit как страховка.
"""

import atexit
import logging
import sys

import uvicorn

from proxy.config import CONFIG_PATH, get_config, start_config_watcher

logger = logging.getLogger("run")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _flush_on_exit() -> None:
    """Страховка: если StatsManager уже создан — сохранить агрегаты."""
    try:
        from proxy.stats import shutdown_stats
        shutdown_stats()
    except Exception:  # noqa: BLE001 при выходе не падаем из-за статистики
        pass

def main() -> None:
    if not CONFIG_PATH.exists():
        print("ОШИБКА: config.yaml не найден.")
        print("Скопируйте пример:  cp config.example.yaml config.yaml")
        sys.exit(1)

    try:
        config = get_config()
    except Exception as e:
        print(f"ОШИБКА: config.yaml невалиден: {e}")
        sys.exit(1)

    _setup_logging(config.server.log_level)

    if not config.routerai.api_key:
        print(f"ОШИБКА: переменная окружения {config.routerai.api_key_env} не установлена.")
        print("PowerShell:  $env:ROUTERAI_API_KEY = 'ваш_ключ'")
        sys.exit(1)

    if config.server.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "ВНИМАНИЕ: прокси слушает %s — он подставляет ВАШ ключ RouterAI, "
            "доступ из сети = ваши деньги!",
            config.server.host,
        )

    start_config_watcher()
    atexit.register(_flush_on_exit)

    logger.info(
        "RouterAI Proxy стартует: http://%s:%d (профиль по умолчанию: %s)",
        config.server.host,
        config.server.port,
        config.profile_selection.default_profile,
    )

    uvicorn.run(
        "proxy.app:app",
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level,
        timeout_graceful_shutdown=10,  # даём стримам дожить до 10 секунд
    )


if __name__ == "__main__":
    main()