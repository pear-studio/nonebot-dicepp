# -*- mode: python ; coding: utf-8 -*-
"""Build DicePP.exe as the Windows single-entry launcher."""

import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), "..", ".."))

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("websockets")
hiddenimports += collect_submodules("pystray")
hiddenimports += collect_submodules("PIL")
hiddenimports += collect_submodules("dicepp_manager")
hiddenimports += collect_submodules("dicepp_control")
hiddenimports += collect_submodules("dicepp_security")
hiddenimports += collect_submodules("pydantic")

datas = [
    (
        os.path.join(PROJECT_ROOT, "dashboard", "src", "static"),
        os.path.join("dashboard", "src", "static"),
    ),
    (
        os.path.join(
            PROJECT_ROOT,
            "src",
            "plugins",
            "DicePP",
            "core",
            "config",
            "pydantic_models.py",
        ),
        os.path.join("dashboard_config_schema"),
    ),
    (os.path.join(PROJECT_ROOT, "templates"), "templates"),
]
datas += copy_metadata("dicepp")

a = Analysis(
    [os.path.join(PROJECT_ROOT, "scripts", "build", "dashboard_entry.py")],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "scipy", "cv2", "torch"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DicePP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
