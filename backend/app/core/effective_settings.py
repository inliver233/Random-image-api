from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, load_settings
from app.core.runtime_settings import RuntimeConfig, load_runtime_config


@dataclass(frozen=True, slots=True)
class EffectiveSettings:
    env: Settings
    runtime: RuntimeConfig


async def load_effective_settings(engine: AsyncEngine, *, env: Mapping[str, str] | None = None) -> EffectiveSettings:
    settings = load_settings(env)
    runtime = await load_runtime_config(engine)
    return EffectiveSettings(env=settings, runtime=runtime)

