"""Persona 模块异常契约

`create_persona` 在 Phase 2 之前用 ``return None`` 表示初始化失败，调用方
（``command.delay_init``）只能记录一行 ``[Persona] 模块初始化失败``，无从
判断到底是哪一步出错。Phase 2 改为抛出此处定义的具名异常，调用方在
``try/except`` 中捕获并保留 ``init_error``，``_admin_debug`` 据此输出诊断。

具名异常的好处：
- IDE 可定位所有抛点；
- 调用方可按异常类型区分处理（例如配置错误可以提示用户改 ``config.json``，
  数据库错误可以提示重启）；
- 测试可断言"配置缺失抛 ConfigError 而非通用 Exception"。
"""
from __future__ import annotations


class PersonaInitError(Exception):
    """Persona 模块初始化失败的基类异常。"""


class PersonaConfigError(PersonaInitError):
    """配置错误（缺 API Key、配置项非法等）。"""


class PersonaCharacterLoadError(PersonaInitError):
    """角色卡加载失败（文件缺失、JSON 解析错误等）。"""


class PersonaStorageError(PersonaInitError):
    """数据库 / 存储相关初始化错误。"""
