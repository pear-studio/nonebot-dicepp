from dashboard.src.windows_autostart import RUN_KEY, VALUE_NAME, WindowsAutostart, autostart_command


class _Key:
    def __init__(self, registry): self.registry = registry
    def __enter__(self): return self
    def __exit__(self, *args): return None


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self): self.values = {}
    def OpenKey(self, root, path, reserved, access):
        if not self.values and access == self.KEY_READ: raise FileNotFoundError
        return _Key(self)
    def CreateKeyEx(self, root, path, reserved, access): return _Key(self)
    def QueryValueEx(self, key, name): return self.values[name], self.REG_SZ
    def SetValueEx(self, key, name, reserved, kind, value): self.values[name] = value
    def DeleteValue(self, key, name):
        if name not in self.values: raise FileNotFoundError
        del self.values[name]


def test_hkcu_run_toggle_uses_stable_root_entry(tmp_path) -> None:
    registry = FakeRegistry()
    executable = tmp_path / "DicePP.exe"
    adapter = WindowsAutostart(executable, registry=registry)
    assert adapter.enabled() is False
    adapter.set_enabled(True)
    assert adapter.enabled() is True
    assert registry.values[VALUE_NAME] == autostart_command(executable)
    assert "--background" in registry.values[VALUE_NAME]
    assert RUN_KEY.endswith("CurrentVersion\\Run")
    adapter.set_enabled(False)
    assert adapter.enabled() is False
