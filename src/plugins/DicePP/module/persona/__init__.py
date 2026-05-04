"""
Persona 模块初始化
"""
from . import command
from .factory import PersonaApp, create_persona

__all__ = ["PersonaApp", "create_persona"]
