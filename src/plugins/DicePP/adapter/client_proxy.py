import abc
from typing import List

from core.command import BotCommandBase


class ClientProxy(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def process_bot_command(self, command: BotCommandBase):
        pass

    @abc.abstractmethod
    async def process_bot_command_list(self, command_list: List[BotCommandBase]):
        pass
