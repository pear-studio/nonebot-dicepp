# -*- mode: python ; coding: utf-8 -*-
"""Build the version-independent DicePP-UpdateGuard.exe."""

import os

from PyInstaller.utils.hooks import copy_metadata


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), "..", ".."))

datas = copy_metadata("dicepp")

a = Analysis(
    [os.path.join(PROJECT_ROOT, "scripts", "build", "update_guard_entry.py")],
    pathex=[PROJECT_ROOT, os.path.join(PROJECT_ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    name="DicePP-UpdateGuard",
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
