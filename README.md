# DM Sound Mixer Suite

A voice-activated, automated ambient/sound-effect mixer for tabletop Dungeon Masters. Speak a room
description out loud and matching background loops or one-shot effects fire automatically, mixed
live in a desktop dashboard.

## Installing

Download the latest installer for your platform from the
[Releases page](https://github.com/BlightsOfTheRoundTable/audioMancer/releases):

- **Windows**: run `DM-Mixer-Setup-<version>.exe` and follow the wizard. The installer is
  unsigned, so Windows SmartScreen will warn that it's from an unrecognized publisher - click
  **More info → Run anyway** to proceed.
- **macOS**: open the `.dmg` and drag **DM Mixer** into Applications. The app is unsigned/
  unnotarized, so Gatekeeper will block the first launch - right-click the app → **Open** → confirm
  to proceed. (Only needed once.)

## Requirements

- A working microphone input device
- Windows or macOS

### Performance on lower-spec hardware

Speech recognition runs locally on CPU. If keyword triggers feel slow to fire on older
hardware, try a smaller Whisper model by setting an environment variable before launching:

```bash
DM_MIXER_WHISPER_MODEL=tiny dm-mixer
```

`tiny` is faster and lighter than the default `base` model, at some cost to transcription
accuracy. No rebuild or reinstall needed - just set the variable before each launch.

## Setup (from source)

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. Requires
Python 3.11.

```bash
uv sync
uv run dm-mixer
```

Alternatively, with a plain virtualenv:

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS
pip install -e .
dm-mixer
```

The first launch downloads the Whisper `base` speech model, which can take a moment.

## Usage

1. Open the **Soundbank Studio** tab and import audio tracks (`.mp3`, `.wav`, `.ogg`), tagging each
   with one or more voice-trigger keywords.
   - Leave **Loop Background Audio** checked for ambient loops (e.g. "rain", "tavern").
   - Uncheck it for instant one-shot effects (e.g. "explosion", "sword clash").
2. Switch to the **Live Scene Mixer** tab and click **Set the Scene** to start listening.
3. Describe the scene out loud. Matching keywords trigger their mapped audio automatically:
   - Loops fade in and mix together; adjust relative and master volume with the sliders.
   - One-shots play once and clear themselves from the mixer when finished.
   - Saying a quantity before a one-shot keyword (e.g. "three arrows") fires it multiple times in a
     staggered volley.
   - Saying "every &lt;duration&gt;" before a keyword (e.g. "thunder every 15 seconds") arms it to
     re-fire on that interval until stopped.
4. Click **End Scene** to stop listening and fade out all active audio. Per-track volume levels are
   remembered across sessions.

User configuration (imported sounds and keyword mappings) and saved volume levels live in
`~/.dm_sound_mixer/`, separate from the repo.

## Development

```bash
uv run pytest
```

## Building the installers

See [packaging/README.md](packaging/README.md) for building the Windows/macOS installers
yourself. `.github/workflows/build-installers.yml` builds both automatically on a version tag
push.
