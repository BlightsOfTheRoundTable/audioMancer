# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for DM Mixer.

Build from the repo root (requires the `build` dependency group):

    uv sync --group build
    uv run pyinstaller packaging/pyinstaller.spec --noconfirm

See packaging/README.md for why each collected package below is needed - several of this
app's dependencies (spaCy's model package, faster-whisper's bundled VAD asset, ctranslate2's
compiled libraries, certifi's CA bundle) load resources at runtime in ways PyInstaller's
static import analysis can't see on its own, and are missing entirely without this.
"""

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).parent  # this file lives in packaging/, ROOT is the repo root
ENTRY_POINT = str(ROOT / "src" / "dm_mixer" / "__main__.py")
APP_NAME = "DM Mixer"

with open(ROOT / "pyproject.toml", "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]

datas = []
binaries = []
hiddenimports = ["spacy.lang.en"]

# Each of these packages does runtime resource lookups (model data, compiled shared libs, a
# bundled ONNX asset) that PyInstaller's static analysis misses without an explicit collect.
for package in ("en_core_web_sm", "ctranslate2", "faster_whisper", "av"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

# Data files only (lookup tables / CA bundle) - no need to pull in every submodule for these.
for package in ("spacy", "certifi"):
    datas += collect_data_files(package)

a = Analysis(
    [ENTRY_POINT],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

icon = str(ROOT / "packaging" / ("windows/icon.ico" if sys.platform == "win32" else "macos/icon.icns"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dm-mixer",
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
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="dm-mixer",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ROOT / "packaging" / "macos" / "icon.icns"),
        bundle_identifier="com.blightsoftheroundtable.dmmixer",
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # Without this key, macOS denies sounddevice/PortAudio microphone access outright
            # with no error dialog at all - the app would just never hear anything.
            "NSMicrophoneUsageDescription": "DM Mixer listens to your voice to trigger ambient sound effects.",
        },
    )
