"""Thin wrapper around the actual speech-to-text backend.

Isolating this here - rather than having TranscriptionEngine construct and call
faster_whisper.WhisperModel directly - means a future hardware-accelerated backend (CoreML on
Mac, ONNX+DirectML on Windows; see the perf backlog) is a change to this module alone, not
surgery on speech.py's listening loop. Callers only ever see transcribe() -> list[str]; they
never touch a faster-whisper Segment object, so the backend can change shape without rippling
outward.
"""

import os

from faster_whisper import WhisperModel

# Overridable via env var so trying a different model size (e.g. "tiny" on lower-spec
# hardware) is a settings change, not a rebuild: set DM_MIXER_WHISPER_MODEL before launching.
DEFAULT_MODEL_SIZE = "base"


def resolve_model_size(explicit=None):
    """An explicit override wins, then the DM_MIXER_WHISPER_MODEL env var, then the default.
    Exposed separately from SpeechRecognizer so callers can log the resolved size *before*
    paying the slow model-load cost, not only after."""
    return explicit or os.environ.get("DM_MIXER_WHISPER_MODEL", DEFAULT_MODEL_SIZE)


class SpeechRecognizer:
    def __init__(self, model_size=None):
        self.model_size = resolve_model_size(model_size)
        try:
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        except Exception as e:
            # A typo'd DM_MIXER_WHISPER_MODEL would otherwise surface as whatever internal
            # error ctranslate2/faster-whisper happens to raise for an unrecognized model
            # name - not obviously connected to the env var that caused it, especially since
            # this fails during startup before any UI exists to explain it.
            raise RuntimeError(
                f"Failed to load Whisper model '{self.model_size}'. "
                "Check DM_MIXER_WHISPER_MODEL (unset it to use the default) or provide a valid model size/path."
            ) from e

    def transcribe(self, audio_buffer):
        """Returns the recognized text of each detected speech segment, in order."""
        segments, _ = self.model.transcribe(audio_buffer, beam_size=3, vad_filter=True, language="en")
        return [segment.text for segment in segments]
