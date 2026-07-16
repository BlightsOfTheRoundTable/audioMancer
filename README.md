# DM Sound Mixer Suite

A voice-activated, automated ambient/sound-effect mixer for tabletop Dungeon Masters. Speak a room
description out loud and matching background loops or one-shot effects fire automatically, mixed
live in a desktop dashboard.

## Requirements

- Python 3.11
- A working microphone input device
- Windows or macOS

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

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
