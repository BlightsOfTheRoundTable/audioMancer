# Building the installers

This directory holds everything needed to package DM Mixer into a standalone Windows installer
and a macOS `.app`/`.dmg`. End users should just download a release from GitHub Releases - this
is for building those releases yourself.

## Prerequisites

```
uv sync --group build
```

Installs PyInstaller, `pyinstaller-hooks-contrib`, and Pillow. These are deliberately kept out of
the app's normal runtime dependencies and the `dev` group - a shipped installer has no reason to
carry test/build tooling.

Windows also needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install
JRSoftware.InnoSetup` or `choco install innosetup`) to build the `.exe` installer, not just the
frozen app itself.

## Building

```
uv run pyinstaller packaging/pyinstaller.spec --noconfirm
```

Produces `dist/dm-mixer/` (Windows onedir build) or `dist/DM Mixer.app` (macOS).

Windows only, to wrap that into a proper installer:

```
iscc packaging\windows\installer.iss /DMyAppVersion=1.0.0
```

Produces `packaging/windows/Output/DM-Mixer-Setup-<version>.exe`. The version defaults to `1.0.0`
if you omit `/DMyAppVersion=`, so a bare `iscc installer.iss` still works for local testing - CI
passes the real version parsed from `pyproject.toml` so the two never drift.

macOS only, to wrap the `.app` into a `.dmg`:

```
hdiutil create -volname "DM Mixer" -srcfolder "dist/DM Mixer.app" -ov -format UDZO DM-Mixer.dmg
```

## Why the PyInstaller spec collects what it collects

Several dependencies do runtime resource lookups PyInstaller's static import analysis can't see
on its own - miss any of these and the packaged app builds successfully but breaks (often
silently, inside `speech.py`'s broad exception handling) the first time that code path runs:

- **`en_core_web_sm`, `spacy`**: spaCy's model package ships data files (`meta.json`, `config.cfg`,
  binary model weights) that the generic spaCy hook doesn't know about, plus lookup tables loaded
  via package-data access. `spacy.lang.en` is also listed as a hidden import since spaCy resolves
  the language class through a registry lookup, not a plain `import`.
- **`ctranslate2`**: faster-whisper's backend ships compiled shared libraries.
- **`faster_whisper`**: bundles a Silero VAD ONNX asset, loaded via `get_assets_path()` at runtime
  because `speech.py` calls `transcribe(..., vad_filter=True)`.
- **`av`** (PyAV): a faster-whisper import-time dependency, needed even though this app's
  numpy-array transcription path never exercises PyAV's own file-decode logic.
- **`certifi`**: needed for the first-run Hugging Face Hub model download's TLS verification -
  without it, that download fails with an SSL error instead of a network error.

`sounddevice`, `pygame`, and `onnxruntime` are left to their existing PyInstaller/
`pyinstaller-hooks-contrib` hooks; only add explicit collects for those if a build surfaces them
missing.

## Icons

`packaging/generate_icons.py` (run with `uv run python packaging/generate_icons.py`) converts
`packaging/icon_source.png` into `packaging/windows/icon.ico` and `packaging/macos/icon.icns`
using Pillow (which can write both formats cross-platform, no macOS `iconutil` needed). If
`icon_source.png` doesn't exist yet, the script draws a simple placeholder and saves it there
first.

**The committed icons are placeholders.** Swap `packaging/icon_source.png` for real branding art
before a public release, then re-run the script to regenerate the `.ico`/`.icns` from it.

## What's deliberately not here

- **Code signing / notarization.** Installers are unsigned for now - Windows shows a SmartScreen
  warning, macOS Gatekeeper requires a right-click → Open on first launch. Revisit with a paid
  Apple Developer Program membership + a Windows code-signing cert if that friction becomes worth
  removing.
- **The faster-whisper model.** Not bundled - the app downloads it (~145MB) from the Hugging Face
  Hub on first launch, same as it always has.
