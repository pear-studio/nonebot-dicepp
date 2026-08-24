"""Current-user Windows login autostart for the stable DicePP root entry."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DicePP"


def autostart_command(executable: str | os.PathLike[str]) -> str:
    return subprocess.list2cmdline([str(Path(executable).resolve()), "--background"])


class WindowsAutostart:
    def __init__(self, executable: str | os.PathLike[str], *, registry: Any | None = None) -> None:
        if registry is None:
            if os.name != "nt":
                raise RuntimeError("Windows login autostart is only available on Windows")
            import winreg as registry_module
            registry = registry_module
        self._registry = registry
        self._command = autostart_command(executable)

    def enabled(self) -> bool:
        try:
            with self._registry.OpenKey(self._registry.HKEY_CURRENT_USER, RUN_KEY, 0, self._registry.KEY_READ) as key:
                value, _kind = self._registry.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return False
        return value == self._command

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            with self._registry.CreateKeyEx(self._registry.HKEY_CURRENT_USER, RUN_KEY, 0, self._registry.KEY_SET_VALUE) as key:
                self._registry.SetValueEx(key, VALUE_NAME, 0, self._registry.REG_SZ, self._command)
            return
        try:
            with self._registry.OpenKey(self._registry.HKEY_CURRENT_USER, RUN_KEY, 0, self._registry.KEY_SET_VALUE) as key:
                self._registry.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
