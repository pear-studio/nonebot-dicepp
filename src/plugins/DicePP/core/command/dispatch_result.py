from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.command.bot_cmd import BotCommandBase
from core.communication import MessagePort


class FileDeliveryOutcome(str, Enum):
    FOLDER_SUCCESS = "folder_success"
    ROOT_SUCCESS = "root_success"
    ROOT_FALLBACK_SUCCESS = "root_fallback_success"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class FileDeliveryResult:
    target: MessagePort
    outcome: FileDeliveryOutcome
    requested_folder: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome in {
            FileDeliveryOutcome.FOLDER_SUCCESS,
            FileDeliveryOutcome.ROOT_SUCCESS,
            FileDeliveryOutcome.ROOT_FALLBACK_SUCCESS,
        }


@dataclass(frozen=True, slots=True)
class BotCommandDispatchResult:
    command: BotCommandBase
    file_deliveries: tuple[FileDeliveryResult, ...] = ()
