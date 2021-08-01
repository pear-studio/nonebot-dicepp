import abc
from command import BotCommandBase


class ClientProxy(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def process_bot_command(self, command: BotCommandBase):
        pass
