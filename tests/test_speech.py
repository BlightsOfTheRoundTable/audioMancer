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
        self.rejected_calls = []
        self.active_sounds = {}
        self.stop_all_calls = []
        self.reject_keywords = set()  # keywords that should behave as "already active" (play() -> False)

    def play(self, keyword, file_info, on_ui_refresh_callback, root_window_widget, periodic_interval=None, context_volume_multiplier=1.0, context_modifier_word=None):
        if keyword in self.reject_keywords:
            self.rejected_calls.append(keyword)
            return False
        self.play_calls.append({
            "keyword": keyword, "file_info": file_info, "periodic_interval": periodic_interval,
            "context_volume_multiplier": context_volume_multiplier, "context_modifier_word": context_modifier_word,
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


def _push_chunk(engine, num_samples=56000):
    engine.audio_queue.put(np.zeros(num_samples, dtype=np.float32))


# ---------------------------------------------------------------------------
# Keyword substring matching
# ---------------------------------------------------------------------------

def test_skipped_play_logs_instead_of_claiming_success(engine_factory, capsys):
    """Regression test: the console used to print "Triggering ..." unconditionally, even when
    AudioManager.play() silently rejected the call (e.g. the keyword was already active) -
    misleadingly implying a sound played when nothing actually happened. It must now check
    the return value and log a "Skipped" message instead."""
    engine, audio_manager, model = engine_factory({
        "explosion": {"file_path": "sounds/explosion.wav", "one_shot": True},
    })
    audio_manager.reject_keywords.add("explosion")
    model.queue_response(["you hear an explosion"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: audio_manager.rejected_calls == ["explosion"])
        assert audio_manager.play_calls == []
        captured = capsys.readouterr()
        assert "Skipped 'explosion'" in captured.out
        assert "Triggering 'explosion'" not in captured.out
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_skipped_periodic_play_logs_instead_of_claiming_success(engine_factory, capsys):
    engine, audio_manager, model = engine_factory({
        "thunder": {"file_path": "sounds/thunder.wav", "one_shot": True},
    })
    audio_manager.reject_keywords.add("thunder @ every 8s")
    model.queue_response(["thunder rumbles every 8 seconds"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: audio_manager.rejected_calls == ["thunder @ every 8s"])
        assert audio_manager.play_calls == []
        captured = capsys.readouterr()
        assert "Skipped 'thunder' (every 8s)" in captured.out
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_skipped_burst_shot_logs_instead_of_silently_dropping(engine_factory, monkeypatch, capsys):
    engine, audio_manager, model = engine_factory({
        "arrow": {"file_path": "sounds/arrow.wav", "one_shot": True},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())
    audio_manager.reject_keywords.add("arrow")  # only the bare (shot 0) key collides here
    model.queue_response(["three arrows fly through the air"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: "arrow" in audio_manager.rejected_calls, timeout=3.0)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 2, timeout=3.0)  # shots 2 and 3 still land
        captured = capsys.readouterr()
        assert "Skipped burst shot 1/3 for 'arrow'" in captured.out
    finally:
        engine.is_running = False
        thread.join(timeout=2)


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


def test_run_loop_survives_a_single_keyword_analysis_crash(engine_factory, monkeypatch):
    """Regression test: a crash analyzing ONE keyword's context cues used to be caught only by
    the outer per-chunk try/except, which silently killed the entire listening session (no log,
    loop just stopped) - the DM would have no idea why nothing was firing anymore. A crash
    analyzing one keyword must not even prevent OTHER keywords in the same chunk from firing."""
    engine, audio_manager, model = engine_factory({
        "explosion": {"file_path": "sounds/explosion.wav", "one_shot": True},
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })
    model.queue_response(["an explosion booms while rain falls"])

    original_analyze = speech_module.context_analysis.analyze_occurrence

    def flaky_analyze(doc, start, end):
        text = doc.text[start:end]
        if text.startswith("explosion"):
            raise RuntimeError("simulated analysis crash")
        return original_analyze(doc, start, end)

    monkeypatch.setattr(speech_module.context_analysis, "analyze_occurrence", flaky_analyze)

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        assert audio_manager.play_calls[0]["keyword"] == "rain"  # explosion crashed, rain still fired
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_run_loop_survives_an_unexpected_chunk_level_error(engine_factory, monkeypatch):
    """Regression test: any unexpected exception processing one chunk used to silently break
    out of run_loop entirely - the background thread would just vanish. It must log the error
    and keep listening for the next chunk instead."""
    engine, audio_manager, model = engine_factory({
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })
    model.queue_response(["i hear rain outside"])
    model.queue_response(["i hear rain outside"])

    call_count = [0]
    original_parse_chunk = speech_module.context_analysis.parse_chunk

    def flaky_parse_chunk(clean_text):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("simulated chunk-level crash")
        return original_parse_chunk(clean_text)

    monkeypatch.setattr(speech_module.context_analysis, "parse_chunk", flaky_parse_chunk)

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)  # first chunk: parse_chunk raises, loop must not die
        assert _wait_until(lambda: call_count[0] >= 1)
        assert thread.is_alive()
        _push_chunk(engine)  # second chunk: succeeds normally
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


def test_keyword_does_not_misfire_inside_an_unrelated_word(engine_factory):
    """Regression test: keyword matching used to be a plain substring check, so "rat" fired
    inside the unrelated word "narrate". Matching is now left-word-boundary aware, so this
    must no longer trigger."""
    engine, audio_manager, model = engine_factory({
        "rat": {"file_path": "sounds/rat.wav", "one_shot": True},
    })
    model.queue_response(["the bard began to narrate the tale"])
    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: model.calls >= 1)
        time.sleep(0.05)
        assert audio_manager.play_calls == []
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_keyword_still_matches_a_spoken_plural_or_suffix(engine_factory):
    """The left-boundary-only fix must not regress the common case of matching a keyword
    spoken as a plural/suffixed form, e.g. "arrow" inside "arrows"."""
    engine, audio_manager, model = engine_factory({
        "arrow": {"file_path": "sounds/arrow.wav", "one_shot": True},
    })
    model.queue_response(["arrows rain down from above"])
    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        assert audio_manager.play_calls[0]["keyword"] == "arrow"
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


def test_leading_article_no_longer_shadows_a_real_quantity_word(engine_factory, monkeypatch):
    """Regression test: the quantity scan used to read the last-3-words window left-to-right
    and stop at the FIRST match, so "a dozen arrows" fired only once - "a" (itself mapped to
    quantity 1) was hit before "dozen" was ever reached. The scan now reads right-to-left
    (closest word to the keyword wins), so this must now fire the full 12 times."""
    engine, audio_manager, model = engine_factory({
        "arrow": {"file_path": "sounds/arrow.wav", "one_shot": True},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())
    model.queue_response(["a dozen arrows fly through the air"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 12, timeout=3.0)
        assert audio_manager.play_calls[0]["keyword"] == "arrow"
        for call in audio_manager.play_calls[1:]:
            assert call["keyword"].startswith("arrow #")
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


def test_repeated_mention_in_one_chunk_sums_to_a_single_combined_burst(engine_factory, monkeypatch):
    """Regression test: a single transcribed chunk used to only look at the FIRST mention of
    a keyword (re.search), so "two explosions ... and then two more explosions" fired only 2
    total, silently dropping the second mention entirely. Scanning now finds every distinct
    mention (re.finditer) and sums their quantities into one combined burst."""
    engine, audio_manager, model = engine_factory({
        "explosion": {"file_path": "sounds/explosion.wav", "one_shot": True},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())
    model.queue_response(["you hear two explosions and then two more explosions"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 4, timeout=3.0)
        keywords = [call["keyword"] for call in audio_manager.play_calls]
        assert keywords[0] == "explosion"
        assert len(set(keywords)) == 4  # every shot got a unique key, no collisions
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_repeated_mention_with_different_quantities_sums_each_occurrence(engine_factory, monkeypatch):
    """Each mention can carry its own quantity: "two" the first time, "three" the second."""
    engine, audio_manager, model = engine_factory({
        "explosion": {"file_path": "sounds/explosion.wav", "one_shot": True},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())
    model.queue_response(["two explosions rock the tower and then three more explosions follow"])

    thread = _run_and_stop(engine)
    try:
        _push_chunk(engine)
        assert _wait_until(lambda: len(audio_manager.play_calls) == 5, timeout=3.0)
    finally:
        engine.is_running = False
        thread.join(timeout=2)


def test_repeated_mention_of_a_looping_keyword_still_starts_once(engine_factory, monkeypatch):
    """Multiple mentions of a LOOPING keyword in one chunk shouldn't multiply anything -
    a loop just needs to start once."""
    engine, audio_manager, model = engine_factory({
        "rain": {"file_path": "sounds/rain.wav", "one_shot": False},
    })
    monkeypatch.setattr(speech_module, "time", FakeTimeModule())
    model.queue_response(["rain falls, and more rain follows, then even more rain"])

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


class ExplodingInputStream:
    """A mic that can't be opened at all: no device, already in use, permission denied, etc."""

    def __init__(self, **_kwargs):
        raise OSError("Error opening InputStream: Device unavailable")


def test_start_returns_false_and_leaves_engine_stopped_when_mic_open_fails(engine_factory, monkeypatch):
    """Regression test: start() used to only guard sd.query_devices(), leaving the actual
    sd.InputStream(...) construction unguarded. A missing/busy/permission-denied microphone
    would raise uncaught from a Tkinter button handler, while is_running was already True -
    leaving the UI stuck claiming it was listening. start() must now catch this, leave
    is_running False, and report failure back to the caller."""
    engine, _audio_manager, _model = engine_factory({})
    monkeypatch.setattr(speech_module.sd, "query_devices", lambda kind: {"default_sample_rate": 16000.0})
    monkeypatch.setattr(speech_module.sd, "InputStream", ExplodingInputStream)

    result = engine.start(active_keyword_map={}, root_window_widget=FakeRootStub())

    assert result is False
    assert engine.is_running is False
    assert engine.stream is None


def test_start_returns_true_on_success(engine_factory, monkeypatch):
    engine, _audio_manager, _model = engine_factory({})
    monkeypatch.setattr(speech_module.sd, "query_devices", lambda kind: {"default_sample_rate": 16000.0})
    monkeypatch.setattr(speech_module.sd, "InputStream", FakeInputStream)

    result = engine.start(active_keyword_map={}, root_window_widget=FakeRootStub())
    try:
        assert result is True
        assert engine.is_running is True
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
        _push_chunk(engine, num_samples=int(48000 * 3.5))  # 3.5 seconds of mic audio at 48kHz
        assert _wait_until(lambda: len(audio_manager.play_calls) == 1)
        assert audio_manager.play_calls[0]["keyword"] == "rain"
    finally:
        engine.is_running = False
        thread.join(timeout=2)
