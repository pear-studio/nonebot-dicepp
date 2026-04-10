"""
Persona AI Proactive 模块

包含主动消息调度、角色生活模拟等功能
"""
from .character_life import CharacterLife, CharacterLifeConfig
from .scheduler import ProactiveScheduler, ProactiveConfig

__all__ = ["CharacterLife", "CharacterLifeConfig", "ProactiveScheduler", "ProactiveConfig"]
