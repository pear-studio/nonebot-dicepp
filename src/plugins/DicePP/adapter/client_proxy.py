import abc
from typing import Callable, Dict, List

from core.command import BotCommandBase
from core.communication import GroupInfo, GroupMemberInfo


class ClientProxy(metaclass=abc.ABCMeta):
    def __init__(self) -> None:
        # 使用 type() 精确匹配，无 MRO 遍历。子类必须显式注册自己的 handler。
        self._command_handlers: Dict[type, Callable] = {}

    async def process_bot_command(self, command: BotCommandBase) -> None:
        handler = self._command_handlers.get(type(command))
        if handler is not None:
            await handler(command)
        else:
            await self._handle_unknown(command)

    async def _handle_unknown(self, command: BotCommandBase) -> None:
        raise NotImplementedError(f"未定义的BotCommand类型: {type(command).__name__}")

    async def process_bot_command_list(self, command_list: List[BotCommandBase]) -> None:
        for command in command_list:
            await self.process_bot_command(command)

    @abc.abstractmethod
    async def get_group_list(self) -> List[GroupInfo]:
        pass

    @abc.abstractmethod
    async def get_group_info(self, group_id: str) -> GroupInfo:
        pass

    @abc.abstractmethod
    async def get_group_member_list(self, group_id: str) -> List[GroupMemberInfo]:
        pass

    @abc.abstractmethod
    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMemberInfo:
        pass
