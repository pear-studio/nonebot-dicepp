import abc
from typing import List

from core.command import BotCommandBase
from core.communication import GroupInfo, GroupMemberInfo


class ClientProxy(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def process_bot_command(self, command: BotCommandBase):
        pass

    @abc.abstractmethod
    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        pass

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
