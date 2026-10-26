"""Profile resolver: мержит глобальные настройки профиля с model-specific overrides.

Приоритет (от высокого к низкому):
1. Model-specific override из profile.models[model_id]
2. Глобальные настройки профиля
3. Дефолты pydantic

Пример:
    profile.provider.ignore = ["deepseek"]
    profile.models["anthropic/claude-sonnet-4.5"].provider.ignore = ["google"]
    
    -> Для claude-sonnet-4.5: ignore = ["google"] (override побеждает)
    -> Для других моделей: ignore = ["deepseek"] (глобальная настройка)
"""

import copy
from typing import Optional

from proxy.config import ProfileConfig, ModelOverrideConfig


def resolve_provider_config(profile: ProfileConfig, model_id: str):
    """Получить ProviderConfig для конкретной модели с учётом overrides."""
    base = copy.deepcopy(profile.provider)
    
    override = profile.models.get(model_id)
    if override and override.provider:
        # Override полностью заменяет глобальные настройки
        return copy.deepcopy(override.provider)
    
    return base


def resolve_price_ceiling(profile: ProfileConfig, model_id: str):
    """Получить PriceCeilingConfig для конкретной модели с учётом overrides."""
    base = copy.deepcopy(profile.price_ceiling)
    
    override = profile.models.get(model_id)
    if override and override.price_ceiling:
        # Override полностью заменяет глобальные настройки
        return copy.deepcopy(override.price_ceiling)
    
    return base


def get_effective_settings(profile: ProfileConfig, model_id: str) -> dict:
    """Получить все эффективные настройки для модели (для логирования)."""
    return {
        "provider": resolve_provider_config(profile, model_id),
        "price_ceiling": resolve_price_ceiling(profile, model_id),
    }