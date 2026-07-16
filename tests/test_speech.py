import threading
import time

import numpy as np
import pytest

from dm_mixer import speech as speech_module


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel - no real model load, no real audio
    analysis. Tests pre-program what "transcript" comes back from each call."""

    def __init__(self):
        self.responses = []  # list of list[str], consumed FIFO, one list per transcribe() call
        self.calls = 0

    def queue_response(self, texts):
        self.responses.append(list(texts))

    def transcribe(self, _audio_buffer, **_kwargs):
        self.calls += 1
        texts = self.responses.pop(0) if self.responses else []
        return ([FakeSegment(t) for t in texts], None)


class FakeAudioManager:
    """Records every play()/stop_all_sounds_with_fade() call instead of touching real audio."""

    def __init__(self):
        self.play_calls = []
        self.active_sounds = {}
        self.stop_all_calls = []

    def play(self, keyword, file_info, on_ui_refresh_callback, root_window_widget, periodic_interval=None):
        self.play_calls.append({
            "keyword": keyword, "file_info": file_info, "periodic_interval": periodic_interval,
        })
        self.active_sounds[keyword] = True
        on_ui_refresh_callback()
        return True

    def stop_all_sounds_with_fade(self, save_history_callback):
        self.stop_all_calls.append(save_history_callback)


class FakeRootStub:
    def after(self, _ms, _callback):
        return 1

    def after_cancel(self, _task_id):
        pass


class FakeTimeModule:
    """Replaces the whole `time` reference inside dm_mixer.speech so cooldown windows can
    be advanced deterministically, and burst dispatch sleeps become instant."""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, _seconds):
        pass

    def advance(self, delta):
        self.now += delta


def _wait_until(condition, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def engine_factory(monkeypatch):
    def _make(keyword_mapping):
        model = FakeWhisperModel()
        monkeypatch.setattr(speech_module, "WhisperModel", lambda *a, **kw: model)

        audio_manager = FakeAudioManager()
        engine = speech_module.TranscriptionEngine(audio_manager, on_keyword_triggered_callback=lambda: None)
        engine.keyword_mapping = keyword_mapping
        engine.hardware_sample_rate = engine.whisper_target_rate  # skip the resampling branch
        engine.root_window_widget = FakeRootStub()
        engine.is_running = True
        return engine, audio_manager, model

    return _make


def _run_and_stop(engine, timeout=2.0):
    thread = threading.Thread(target=engine.run_loop, daemon=True)
    thread.start()
    return thread


def _push_chunk(engine, num_samples=32000):
    engine.audio_queue.put(np.zeros(num_samples, dtype=np.float32))


# ---------------------------------------------------------------------------
# Keyword substring matching
# ---------------------------------------------------------------------------

def test_matched_keyword_triggers_audio_manager_play(engine_factory):
    engine, audio_manager, model = engine_factory({
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })
    model.queue_response(["i hear rain outside the window"])
    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        assert audio_manager.play_calls[0]["keyword"] == "rain"
        assert audio_manager.play_calls[0]["periodic_interval"] is None
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_run_loop_survives_transcribe_crash_and_keeps_running(engine_factory):
    engine, audio_manager, model = engine_factory({
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })

    call_count = [0]
    original_transcribe = model.transcribe

    def flaky_transcribe(audio_buffer, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("model crashed on this chunk")
        return original_transcribe(audio_buffer, **kwargs)

    model.transcribe = flaky_transcribe
    model.queue_response(["i hear rain outside"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)  # first chunk: transcribe raises, loop must not die
        assert _wait_until(lambda: call_count[0] >= 1)
        _push_chunk(engine)  # second chunk: transcribe succeeds normally
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_unmatched_text_does_not_trigger_play(engine_factory):
    engine, audio_manager, model = engine_factory({
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })
    model.queue_response(["the sun is shining brightly today"])
    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: model.calls >= 1)
        time.sleep(0.05)
        assert audio_manager.play_calls == []
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_known_limitation_substring_match_has_false_positives(engine_factory):
    """Documents current behavior rather than desired behavior: keyword matching is a plain
    substring check, not word-boundary aware, so a short keyword can misfire inside an
    unrelated larger word. If this test starts failing, matching has been improved to be
    word-boundary aware and this test should be updated to reflect that."""
    engine, audio_manager, model = engine_factory({
        "rat": {"file_path": "sounds/rat.wav", "one_shot": True},
    })
    model.queue_response(["the bard began to narrate the tale"])
    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        assert audio_manager.play_calls[0]["keyword"] == "rat"
    finally:
        engine.is_running = False
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Phrase cooldown
# ---------------------------------------------------------------------------

def test_cooldown_blocks_rapid_retrigger_but_allows_after_expiry(engine_factory, monkeypatch):
    engine, audio_manager, model = engine_factory({
        "dragon": {"file_path": "sounds/dragon.wav", "one_shot": True},
    })
    fake_time = FakeTimeModule()
    monkeypatch.setattr(speech_module, "time", fake_time)

    model.queue_response(["a dragon roars"])
    model.queue_response(["a dragon roars"])
    model.queue_response(["a dragon roars"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)

        # Still within the 4-second cooldown window -> must not refire
        fake_time.advance(1.0)
        _push_chunk(engine)
        assert _wait_until(lambda: model.calls >= 2)
        time.sleep(0.05)
        assert len(audio_manager.play_calls) == 1

        # Past the cooldown window -> must refire
        fake_time.advance(10.0)
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 2)
    finally:
        engine.is_running = False
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Quantity-word burst parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase, expected_count",
    [
        ("three arrows fly through the air", 3),
        ("many arrows fly through the air", 5),
        ("5 arrows fly through the air", 5),
        ("20 arrows fly through the air", 15),  # capped at 15
    ],
)
def test_quantity_phrase_fires_the_expected_number_of_bursts(engine_factory, monkeypatch, phrase, expected_count):
    engine, audio_manager, model = engine_factory({
        "arrow": {"file_path": "sounds/arrow.wav", "one_shot": True},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())  # no-op sleep -> fast bursts
    model.queue_response([phrase])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == expected_count, timeout=3.0)
        assert audio_manager.play_calls[0]["keyword"] == "arrow"
        for call in audio_manager.play_calls[1:]:
            assert call["keyword"].startswith("arrow #")
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_known_limitation_leading_article_shadows_a_real_quantity_word(engine_factory, monkeypatch):
    """Documents current behavior rather than desired behavior: the quantity scan reads the
    last-3-words window left-to-right and stops at the FIRST match. "a dozen arrows" should
    arguably fire 12 times, but "a" itself is also a mapped quantity word (=1) and appears
    before "dozen" in the window, so it wins and the phrase fires only once. If this test
    starts failing, the scan order has been fixed (e.g. to prefer the word closest to the
    keyword) and this test should be updated to reflect that."""
    engine, audio_manager, model = engine_factory({
        "arrow": {"file_path": "sounds/arrow.wav", "one_shot": True},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())
    model.queue_response(["a dozen arrows fly through the air"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        time.sleep(0.1)
        assert len(audio_manager.play_calls) == 1  # not 12, per the limitation above
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_quantity_word_is_ignored_for_looping_keywords(engine_factory, monkeypatch):
    """Burst-firing only applies to one-shot effects; a loop keyword should fire exactly once
    even if a quantity word happens to precede it in the sentence."""
    engine, audio_manager, model = engine_factory({
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())
    model.queue_response(["three rain clouds gather overhead"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        time.sleep(0.05)
        assert len(audio_manager.play_calls) == 1
        assert audio_manager.play_calls[0]["keyword"] == "rain"
    finally:
        engine.is_running = False
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Periodic "every N seconds" parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase, expected_interval",
    [
        ("thunder rumbles every fifteen seconds", 15.0),
        ("thunder rumbles every 20 seconds", 20.0),
        ("thunder rumbles every minute", 60.0),
        ("thunder strikes every now and then", 8.0),  # no recognizable unit -> default
    ],
)
def test_every_phrase_triggers_periodic_play_with_expected_interval(engine_factory, phrase, expected_interval):
    engine, audio_manager, model = engine_factory({
        "thunder": {"file_path": "sounds/thunder.wav", "one_shot": True},
    })
    model.queue_response([phrase])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        call = audio_manager.play_calls[0]
        assert call["periodic_interval"] == expected_interval
        assert call["keyword"] == f"thunder @ every {int(expected_interval)}s"
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_every_as_part_of_another_word_is_not_treated_as_periodic(engine_factory):
    """'everywhere' contains the substring 'every' but is not the standalone word 'every',
    so it must not be misparsed as a periodic re-fire request."""
    engine, audio_manager, model = engine_factory({
        "monster": {"file_path": "sounds/monster.wav", "one_shot": True},
    })
    model.queue_response(["monsters lurk everywhere in this dungeon"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        assert audio_manager.play_calls[0]["periodic_interval"] is None
        assert audio_manager.play_calls[0]["keyword"] == "monster"
    finally:
        engine.is_running = False
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# audio_callback / stop / start
# ---------------------------------------------------------------------------

def test_audio_callback_queues_a_copy_of_the_incoming_block(engine_factory):
    engine, _audio_manager, _model = engine_factory({})
    block = np.ones((4, 1), dtype=np.float32)

    engine.audio_callback(block, 4, None, status=None)

    queued = engine.audio_queue.get_nowait()
    assert np.array_equal(queued, block)
    assert queued is not block  # must be a copy, not the same buffer object


def test_audio_callback_logs_status_but_still_queues_data(engine_factory, capsys):
    engine, _audio_manager, _model = engine_factory({})
    block = np.zeros((4, 1), dtype=np.float32)

    engine.audio_callback(block, 4, None, status="input overflow")

    assert engine.audio_queue.get_nowait() is not None
    captured = capsys.readouterr()
    assert "input overflow" in captured.err


def test_stop_closes_stream_and_fades_out_audio(engine_factory):
    engine, audio_manager, _model = engine_factory({})

    class FakeStream:
        def __init__(self):
            self.stopped = False
            self.closed = False

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    stream = FakeStream()
    engine.stream = stream
    engine.is_running = True

    engine.stop(save_history_callback="cb-marker")

    assert engine.is_running is False
    assert stream.stopped is True
    assert stream.closed is True
    assert audio_manager.stop_all_calls == ["cb-marker"]


def test_stop_tolerates_stream_close_failure(engine_factory):
    engine, audio_manager, _model = engine_factory({})

    class ExplodingStream:
        def stop(self):
            raise RuntimeError("device already gone")

        def close(self):
            pass

    engine.stream = ExplodingStream()

    engine.stop(save_history_callback=None)  # must not raise

    assert audio_manager.stop_all_calls == [None]


class FakeInputStream:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        FakeInputStream.instances.append(self)

    def start(self):
        self.started = True


def test_start_derives_sample_rate_from_input_device(engine_factory, monkeypatch):
    engine, _audio_manager, _model = engine_factory({})
    FakeInputStream.instances = []
    monkeypatch.setattr(speech_module.sd, "query_devices", lambda kind: {"default_sample_rate": 48000.0})
    monkeypatch.setattr(speech_module.sd, "InputStream", FakeInputStream)

    engine.start(active_keyword_map={}, root_window_widget=FakeRootStub())
    try:
        assert engine.hardware_sample_rate == 48000
        assert len(FakeInputStream.instances) == 1
        assert FakeInputStream.instances[0].started is True
    finally:
        engine.is_running = False


def test_start_falls_back_to_16k_when_device_query_fails(engine_factory, monkeypatch):
    engine, _audio_manager, _model = engine_factory({})
    monkeypatch.setattr(speech_module.sd, "query_devices", lambda kind: (_ for _ in ()).throw(RuntimeError("no device")))
    monkeypatch.setattr(speech_module.sd, "InputStream", FakeInputStream)

    engine.start(active_keyword_map={}, root_window_widget=FakeRootStub())
    try:
        assert engine.hardware_sample_rate == 16000
    finally:
        engine.is_running = False


# ---------------------------------------------------------------------------
# Sample-rate resampling path
# ---------------------------------------------------------------------------

def test_run_loop_resamples_when_hardware_rate_differs(engine_factory):
    engine, audio_manager, model = engine_factory({
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })
    engine.hardware_sample_rate = 48000  # differs from whisper_target_rate (16000)
    model.queue_response(["i hear rain outside"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine, num_samples=48000 * 2)  # 2 seconds of mic audio at 48kHz
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        assert audio_manager.play_calls[0]["keyword"] == "rain"
    finally:
        engine.is_running = False
        thread.join(timeout=2)
