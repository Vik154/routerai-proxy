"""Конфигурация: загрузка YAML, валидация (pydantic), hot reload.

Структура:
- config.yaml: общие настройки (server, routerai, circuit_breaker, profile_selection)
- profiles/*.yaml: профили (default.yaml, experiments.yaml, ...)

Принципы:
- Невалидный конфиг при hot reload НЕ роняет прокси: остаётся последний валидный.
- get_config() ленив: первый вызов загружает конфиг.
- profile_selection.default_profile обязан существовать (fail fast при старте).
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")
PROFILES_DIR = Path("profiles")

_lock = threading.RLock()
_config: Optional["AppConfig"] = None


# ---------------------------------------------------------------------------
# Секции конфига
# ---------------------------------------------------------------------------

class RouterAIConfig(BaseModel):
    api_url: str = "https://routerai.ru/api/v1"
    api_key_env: str = "ROUTERAI_API_KEY"

    @property
    def api_key(self) -> Optional[str]:
        """Ключ читается из env каждый раз: ротация ключа не требует рестарта."""
        return os.getenv(self.api_key_env)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: Literal["debug", "info", "warning", "error"] = "info"


class CatalogConfig(BaseModel):
    models_ttl: int = 300
    price_ttl: int = 3600
    unknown_model: Literal["reject", "passthrough"] = "reject"


class ProviderConfig(BaseModel):
    order: List[str] = Field(default_factory=list)
    ignore: List[str] = Field(default_factory=list)
    only: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    allow_fallbacks: bool = True


class PriceCeilingConfig(BaseModel):
    prompt_per_mtoken: Optional[float] = None
    on_all_expensive: Literal["block", "allow_cheapest", "warn"] = "block"


class ModelOverrideConfig(BaseModel):
    """Model-specific overrides внутри профиля."""
    model_config = {"protected_namespaces": ()}
    
    provider: Optional[ProviderConfig] = None
    price_ceiling: Optional[PriceCeilingConfig] = None


class ProfileConfig(BaseModel):
    model_config = {"protected_namespaces": ()}
    
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    price_ceiling: PriceCeilingConfig = Field(default_factory=PriceCeilingConfig)
    model_map: Dict[str, str] = Field(default_factory=dict)
    hide_over_ceiling: bool = False
    models: Dict[str, ModelOverrideConfig] = Field(default_factory=dict)


class ProfileSelectionConfig(BaseModel):
    method: Literal["header", "default_only"] = "header"
    header_name: str = "X-Proxy-Profile"
    default_profile: str = "default"
    unknown_profile: Literal["reject", "fallback_default"] = "reject"


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = 5
    recovery_timeout: int = 60
    success_threshold: int = 1


class StatsConfig(BaseModel):
    requests_log: str = "data/requests.jsonl"
    stats_snapshot: str = "data/stats.json"
    flush_interval: int = 60


class AppConfig(BaseModel):
    routerai: RouterAIConfig = Field(default_factory=RouterAIConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    profiles: Dict[str, ProfileConfig] = Field(default_factory=dict)
    profile_selection: ProfileSelectionConfig = Field(default_factory=ProfileSelectionConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    stats: StatsConfig = Field(default_factory=StatsConfig)

    @model_validator(mode="after")
    def _validate_profiles(self) -> "AppConfig":
        if not self.profiles:
            raise ValueError("profiles: должен быть объявлен хотя бы один профиль")
        if self.profile_selection.default_profile not in self.profiles:
            raise ValueError(
                f"profile_selection.default_profile="
                f"'{self.profile_selection.default_profile}' не найден среди profiles: "
                f"{sorted(self.profiles.keys())}"
            )
        return self


# ---------------------------------------------------------------------------
# Загрузка профилей из директории
# ---------------------------------------------------------------------------

def _load_profiles() -> Dict[str, ProfileConfig]:
    """Загрузить все профили из profiles/*.yaml"""
    profiles: Dict[str, ProfileConfig] = {}
    
    if not PROFILES_DIR.exists():
        logger.warning("Директория %s не существует, создаю", PROFILES_DIR)
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        return profiles
    
    for yaml_file in PROFILES_DIR.glob("*.yaml"):
        profile_name = yaml_file.stem
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            profile = ProfileConfig(**raw)
            profiles[profile_name] = profile
            logger.info("Загружен профиль: %s из %s", profile_name, yaml_file)
        except Exception as e:
            logger.error("Ошибка загрузки профиля %s: %s", yaml_file, e)
            raise
    
    return profiles


# ---------------------------------------------------------------------------
# Загрузка / доступ / hot reload
# ---------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    """Загрузить и провалидировать конфиг. Обновляет глобальный инстанс только при успехе."""
    global _config

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Загружаем профили из директории
    profiles = _load_profiles()
    
    # Мержим профили в основной конфиг
    raw["profiles"] = profiles

    new_config = AppConfig(**raw)

    with _lock:
        _config = new_config

    logger.info(
        "Конфиг загружен: профилей=%s, default=%s",
        sorted(new_config.profiles.keys()),
        new_config.profile_selection.default_profile,
    )
    return new_config


def get_config() -> AppConfig:
    """Текущий валидный конфиг (ленивая загрузка при первом вызове)."""
    with _lock:
        if _config is None:
            return load_config()
        return _config


def _reload_safe() -> None:
    """Hot reload: ошибка валидации НЕ роняет прокси — остаётся старый конфиг."""
    try:
        load_config()
        logger.info("Hot reload: конфиг применён")
    except Exception as e:  # noqa: BLE001 — намеренно широко: откат на старый конфиг
        logger.error("Hot reload: конфиг НЕВАЛИДЕН, оставлен предыдущий: %s", e)


def start_config_watcher(poll_interval: float = 5.0) -> threading.Thread:
    """Фоновый тред: следит за mtime config.yaml и profiles/*.yaml."""

    def _loop() -> None:
        last_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0.0
        last_profile_mtimes = {}
        if PROFILES_DIR.exists():
            for f in PROFILES_DIR.glob("*.yaml"):
                last_profile_mtimes[f] = f.stat().st_mtime

        while True:
            time.sleep(poll_interval)
            try:
                # Проверяем config.yaml
                if CONFIG_PATH.exists():
                    mtime = CONFIG_PATH.stat().st_mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        logger.info("config.yaml изменён, перечитываю...")
                        _reload_safe()
                
                # Проверяем profiles/*.yaml
                if PROFILES_DIR.exists():
                    current_files = set(PROFILES_DIR.glob("*.yaml"))
                    current_mtimes = {f: f.stat().st_mtime for f in current_files}
                    
                    # Проверяем изменения
                    changed = False
                    for f in current_files:
                        if f not in last_profile_mtimes or current_mtimes[f] != last_profile_mtimes[f]:
                            changed = True
                            break
                    
                    # Проверяем удаления
                    if not changed:
                        for f in last_profile_mtimes:
                            if f not in current_files:
                                changed = True
                                break
                    
                    if changed:
                        logger.info("profiles/*.yaml изменены, перечитываю...")
                        _reload_safe()
                        last_profile_mtimes = current_mtimes
                        
            except Exception as e:  # noqa: BLE001 — watcher не должен умирать
                logger.error("config watcher: %s", e)

    thread = threading.Thread(target=_loop, name="config-watcher", daemon=True)
    thread.start()
    logger.info("Config watcher запущен (poll=%.1fs)", poll_interval)
    return thread