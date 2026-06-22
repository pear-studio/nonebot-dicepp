# -*- mode: python ; coding: utf-8 -*-
"""Build DicePPDashboard.exe as a transitional standalone onefile binary."""

import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), "..", ".."))

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("websockets")

datas = [
    (
        os.path.join(PROJECT_ROOT, "dashboard", "src", "static"),
        os.path.join("dashboard", "src", "static"),
    ),
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
    excludes=["matplotlib", "numpy", "pandas", "scipy", "PIL", "cv2", "torch"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DicePPDashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
