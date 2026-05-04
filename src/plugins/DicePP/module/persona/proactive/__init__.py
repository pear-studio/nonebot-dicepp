"""
Persona AI Proactive 模块

包含主动消息调度、角色生活模拟等功能
"""
import warnings

warnings.warn(
    "persona.proactive 模块已迁移到 life/llm，请改用具体子包",
    DeprecationWarning,
    stacklevel=2,
)
from ..life.character_life import CharacterLife, CharacterLifeConfig
from ..life.proactive import ProactiveScheduler, ProactiveConfig, EventShareTaskQueue
from ..llm.coordinator import LLMCallCoordinator

__all__ = [
    "CharacterLife",
    "CharacterLifeConfig",
    "ProactiveScheduler",
    "ProactiveConfig",
    "EventShareTaskQueue",
    "LLMCallCoordinator",
]
