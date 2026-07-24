"""Tests for the STT seam (stt.py). faster_whisper.WhisperModel is faked here the same way
tests/test_speech.py already fakes it: a real model load is slow and its actual transcription
behavior isn't something worth asserting exact output from - only SpeechRecognizer's own logic
(model-size resolution, unwrapping Segment objects to plain strings) is under test.
"""

import pytest

from dm_mixer import stt


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeWhisperModel:
    def __init__(self, model_size, **kwargs):
        self.model_size = model_size
        self.init_kwargs = kwargs

    def transcribe(self, audio_buffer, **kwargs):
        self.transcribe_call = (audio_buffer, kwargs)
        return ([FakeSegment("hello"), FakeSegment("world")], None)


@pytest.fixture(autouse=True)
def fake_whisper_model(monkeypatch):
    monkeypatch.setattr(stt, "WhisperModel", FakeWhisperModel)


def test_resolve_model_size_prefers_explicit_override(monkeypatch):
    monkeypatch.setenv("DM_MIXER_WHISPER_MODEL", "tiny")
    assert stt.resolve_model_size("small") == "small"


def test_resolve_model_size_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("DM_MIXER_WHISPER_MODEL", "tiny")
    assert stt.resolve_model_size() == "tiny"


def test_resolve_model_size_falls_back_to_default_when_nothing_set(monkeypatch):
    monkeypatch.delenv("DM_MIXER_WHISPER_MODEL", raising=False)
    assert stt.resolve_model_size() == stt.DEFAULT_MODEL_SIZE


def test_speech_recognizer_uses_an_explicit_model_size(monkeypatch):
    monkeypatch.setenv("DM_MIXER_WHISPER_MODEL", "small")  # must be overridden, not just ignored
    recognizer = stt.SpeechRecognizer(model_size="tiny")

    assert recognizer.model_size == "tiny"
    assert recognizer.model.model_size == "tiny"


def test_speech_recognizer_falls_back_to_the_env_var(monkeypatch):
    monkeypatch.setenv("DM_MIXER_WHISPER_MODEL", "small")
    recognizer = stt.SpeechRecognizer()

    assert recognizer.model_size == "small"


def test_transcribe_returns_plain_text_not_segment_objects():
    recognizer = stt.SpeechRecognizer(model_size="tiny")

    result = recognizer.transcribe("fake-audio-buffer")

    assert result == ["hello", "world"]
    # Locks in the actual parameters sent to the backend, not just the unwrapped return shape -
    # a change to beam_size/vad_filter/language here would otherwise pass silently.
    audio_buffer, kwargs = recognizer.model.transcribe_call
    assert audio_buffer == "fake-audio-buffer"
    assert kwargs == {"beam_size": 3, "vad_filter": True, "language": "en"}


def test_speech_recognizer_raises_a_clear_error_on_invalid_model_size(monkeypatch):
    original_error = ValueError("unknown model size")

    def raising_whisper_model(model_size, **kwargs):
        raise original_error

    monkeypatch.setattr(stt, "WhisperModel", raising_whisper_model)

    with pytest.raises(RuntimeError, match="Failed to load Whisper model 'bogus'") as exc_info:
        stt.SpeechRecognizer(model_size="bogus")

    # The original error is preserved as the cause, not swallowed - still visible for
    # debugging even though the top-level message is now actionable.
    assert exc_info.value.__cause__ is original_error
