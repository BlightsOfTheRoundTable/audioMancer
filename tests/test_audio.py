import json
import os
import queue
import struct
import threading
import time

import pygame
import pytest

from dm_mixer.audio import AudioManager, _base_keyword, get_audio_file_duration, pygame_worker_process


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _write_wav(path, num_frames, sample_rate=8000, channels=1, bits_per_sample=16, extra_chunk=b""):
    """Writes a minimal, valid silent WAV file, optionally preceded by a filler chunk
    (e.g. LIST/JUNK) to exercise the RIFF chunk-walking logic in get_audio_file_duration."""
    bytes_per_sample = bits_per_sample // 8
    block_align = channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    data = b"\x00" * (num_frames * block_align)

    fmt_body = struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    body = extra_chunk
    body += b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
    body += b"data" + struct.pack("<I", len(data)) + data

    riff_size = 4 + len(body)
    path.write_bytes(b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + body)


def _junk_chunk(content=b"abc"):
    """A filler chunk with an odd byte count, to verify word-alignment padding is honored."""
    padding = b"\x00" * (len(content) % 2)
    return b"JUNK" + struct.pack("<I", len(content)) + content + padding


class FakeRoot:
    """Stands in for the Tkinter root widget's .after()/.after_cancel(), letting tests
    fire scheduled callbacks deterministically instead of waiting on a real Tk event loop."""

    def __init__(self):
        self._next_id = 0
        self._scheduled = {}

    def after(self, _ms, callback):
        self._next_id += 1
        task_id = self._next_id
        self._scheduled[task_id] = callback
        return task_id

    def after_cancel(self, task_id):
        self._scheduled.pop(task_id, None)

    def fire(self, task_id):
        """Simulates the scheduled delay elapsing."""
        callback = self._scheduled.pop(task_id)
        callback()


class FakeWidget:
    """Stands in for a ttk.Progressbar - just records the last value it was configured with."""

    def __init__(self):
        self.value = None

    def config(self, value=None, **_kwargs):
        self.value = value


class FakeProcess:
    """Stands in for multiprocessing.Process so tests don't spawn a real OS process
    or touch real audio hardware just to construct an AudioManager."""

    def __init__(self, target=None, args=(), daemon=None, hangs=False):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.hangs = hangs
        self._alive = False
        self.terminated = False
        self.killed = False

    def start(self):
        self._alive = True

    def join(self, timeout=None):
        if not self.hangs:
            self._alive = False

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True
        if not self.hangs:
            self._alive = False

    def kill(self):
        self.killed = True
        if not self.hangs:
            self._alive = False


@pytest.fixture
def audio_manager(monkeypatch, tmp_path):
    """A real AudioManager, but with the multiprocessing worker and history file swapped
    for in-process test doubles so no real subprocess or audio hardware is touched."""
    monkeypatch.setattr("dm_mixer.audio.Queue", queue.Queue)
    monkeypatch.setattr("dm_mixer.audio.Process", FakeProcess)
    monkeypatch.setattr("dm_mixer.audio.HISTORY_FILE", str(tmp_path / "volume_history.json"))
    return AudioManager()


def _recorder():
    calls = []

    def callback():
        calls.append(True)

    callback.calls = calls
    return callback


# ---------------------------------------------------------------------------
# get_audio_file_duration
# ---------------------------------------------------------------------------

def test_wav_duration_matches_frame_count(tmp_path):
    wav_path = tmp_path / "tone.wav"
    _write_wav(wav_path, num_frames=8000 * 2, sample_rate=8000)  # exactly 2 seconds

    assert get_audio_file_duration(str(wav_path)) == pytest.approx(2.0, rel=1e-3)


def test_wav_duration_survives_extra_chunk_before_data(tmp_path):
    """Regression test: earlier code trusted fixed byte offsets and broke on WAVs with a
    LIST/JUNK chunk before the data chunk. The chunk-walking parser must skip over it."""
    wav_path = tmp_path / "with_junk.wav"
    _write_wav(wav_path, num_frames=8000 * 3, sample_rate=8000, extra_chunk=_junk_chunk())

    assert get_audio_file_duration(str(wav_path)) == pytest.approx(3.0, rel=1e-3)


def test_wav_duration_real_repo_sound_files():
    import wave

    for name in ("sounds/rain.wav", "sounds/tavern_background.wav"):
        with wave.open(name, "rb") as f:
            expected = f.getnframes() / f.getframerate()
        assert get_audio_file_duration(name) == pytest.approx(expected, rel=1e-3)


def test_wav_duration_falls_back_on_corrupt_header(tmp_path):
    bad_path = tmp_path / "corrupt.wav"
    bad_path.write_bytes(b"NOT A REAL WAV HEADER AT ALL")

    assert get_audio_file_duration(str(bad_path)) == 3.0


def test_wav_duration_falls_back_when_data_chunk_missing(tmp_path):
    truncated_path = tmp_path / "truncated.wav"
    truncated_path.write_bytes(b"RIFF" + struct.pack("<I", 4) + b"WAVE")  # no chunks at all

    assert get_audio_file_duration(str(truncated_path)) == 3.0


def test_duration_falls_back_for_missing_file():
    assert get_audio_file_duration("does/not/exist.wav") == 3.0


def test_mp3_duration_uses_bitrate_estimate(tmp_path):
    mp3_path = tmp_path / "clip.mp3"
    mp3_path.write_bytes(b"\x00" * 16000)  # 16000 bytes -> 1.0s at the assumed 128kbps

    assert get_audio_file_duration(str(mp3_path)) == pytest.approx(1.0, rel=1e-3)


def test_ogg_duration_uses_bitrate_estimate(tmp_path):
    ogg_path = tmp_path / "clip.ogg"
    ogg_path.write_bytes(b"\x00" * 32000)  # 32000 bytes -> 2.0s at the assumed 128kbps

    assert get_audio_file_duration(str(ogg_path)) == pytest.approx(2.0, rel=1e-3)


# ---------------------------------------------------------------------------
# _base_keyword
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "keyword, expected",
    [
        ("fireball", "fireball"),
        ("fireball @ every 8s", "fireball"),
        ("fireball #2-482", "fireball"),
        ("fireball #14-007", "fireball"),
    ],
)
def test_base_keyword_strips_periodic_and_burst_suffixes(keyword, expected):
    assert _base_keyword(keyword) == expected


# ---------------------------------------------------------------------------
# pygame_worker_process - synchronous command batches (no real-time waits needed)
# ---------------------------------------------------------------------------

class _ChannelVolumeSpy:
    """pygame.mixer.Channel is an immutable C-extension type, so its methods can't be
    monkeypatched directly. This wraps a real channel instead, recording set_volume calls
    while transparently delegating everything (play/get_busy/fadeout/etc) to the real thing."""

    def __init__(self, channel, volume_calls):
        self._channel = channel
        self._volume_calls = volume_calls

    def set_volume(self, vol):
        self._volume_calls.append(round(vol, 3))
        return self._channel.set_volume(vol)

    def __getattr__(self, name):
        return getattr(self._channel, name)


def test_worker_processes_full_command_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    wav_path = tmp_path / "loop.wav"
    _write_wav(wav_path, num_frames=8000 * 1, sample_rate=8000)  # long enough to stay busy

    volume_calls = []
    original_find_channel = pygame.mixer.find_channel

    def spy_find_channel(*args, **kwargs):
        return _ChannelVolumeSpy(original_find_channel(*args, **kwargs), volume_calls)

    monkeypatch.setattr(pygame.mixer, "find_channel", spy_find_channel)

    q = queue.Queue()
    q.put({"action": "play", "keyword": "rain", "file_path": str(wav_path), "one_shot": False, "base_volume": 0.4})
    q.put({"action": "update_master", "value": 0.5})
    q.put({"action": "update_individual", "keyword": "rain", "value": 0.9})
    q.put({"action": "stop_track", "keyword": "rain", "fade_ms": 10})
    q.put({"action": "play", "keyword": "storm", "file_path": str(wav_path), "one_shot": False, "base_volume": 0.3})
    q.put({"action": "stop_all", "fade_ms": 10})
    q.put(None)

    pygame_worker_process(q)  # processes the whole queue synchronously, then returns

    assert volume_calls == [0.4, 0.2, 0.45, 0.15]


def test_worker_skips_play_for_nonexistent_file(monkeypatch):
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    calls = []
    monkeypatch.setattr(pygame.mixer, "find_channel", lambda: calls.append(True))

    q = queue.Queue()
    q.put({"action": "play", "keyword": "ghost", "file_path": "nope.wav", "one_shot": True, "base_volume": 0.5})
    q.put(None)

    pygame_worker_process(q)

    assert calls == []


def test_worker_survives_malformed_command_and_keeps_processing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    wav_path = tmp_path / "blip.wav"
    _write_wav(wav_path, num_frames=8000 * 1, sample_rate=8000)

    calls = []
    original_find_channel = pygame.mixer.find_channel

    def spy(*args, **kwargs):
        result = original_find_channel(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(pygame.mixer, "find_channel", spy)

    q = queue.Queue()
    q.put({"action": "update_individual"})  # missing required keys -> raises internally
    q.put({"action": "play", "keyword": "blip", "file_path": str(wav_path), "one_shot": True, "base_volume": 0.5})
    q.put(None)

    pygame_worker_process(q)  # must not crash on the malformed command above

    assert len(calls) == 1
    # Regression: this used to be a bare `except: pass` with zero diagnostic trail - a
    # malformed command would go silent forever with the main process none the wiser.
    assert "[ERROR-AUDIO-WORKER]" in capsys.readouterr().err


def test_worker_allows_replay_after_previous_oneshot_finishes(tmp_path, monkeypatch, capsys):
    """Regression test for the periodic re-fire bug: the worker used to permanently mark a
    keyword as 'active' the moment it was first played, silently dropping every later replay
    of that same keyword even after the sound had long finished. It must now only block a
    replay while the previous channel is still actually busy."""
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, num_frames=int(8000 * 0.15), sample_rate=8000)  # ~150ms one-shot

    calls = []
    original_find_channel = pygame.mixer.find_channel

    def spy(*args, **kwargs):
        result = original_find_channel(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(pygame.mixer, "find_channel", spy)

    q = queue.Queue()
    worker_thread = threading.Thread(target=pygame_worker_process, args=(q,), daemon=True)
    worker_thread.start()

    play_cmd = {"action": "play", "keyword": "boom", "file_path": str(wav_path), "one_shot": True, "base_volume": 0.5}

    q.put(dict(play_cmd))
    time.sleep(0.03)  # let the channel actually start

    q.put(dict(play_cmd))  # still busy -> must be dropped
    time.sleep(0.05)
    assert len(calls) == 1
    assert "still playing on its channel - skipping replay" in capsys.readouterr().out

    time.sleep(0.25)  # let the ~150ms one-shot fully finish

    q.put(dict(play_cmd))  # channel now free -> must be allowed to replay
    time.sleep(0.05)
    assert len(calls) == 2

    q.put(None)
    worker_thread.join(timeout=2)


def test_worker_replays_a_keyword_whose_old_channel_was_reused_by_another_sound(tmp_path, monkeypatch, capsys):
    """Regression test: existing["channel"].get_busy() alone can't tell "this keyword's own
    sound is still playing" apart from "pygame reassigned this exact channel object to a
    completely different sound after the original one finished." Channels are a shared pool -
    once a keyword's playback finishes and its channel goes idle, find_channel() is free to
    hand that same channel to a different keyword. A stale reference then sees "busy" and used
    to silently drop the replay - the main process's UI already shows it as freshly triggered
    by that point, so the DM sees no error, just no sound. Forcing a single channel guarantees
    real reuse between two different keywords instead of hoping pygame picks the same one."""
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    original_set_num_channels = pygame.mixer.set_num_channels
    monkeypatch.setattr(pygame.mixer, "set_num_channels", lambda _n: original_set_num_channels(1))

    boom_path = tmp_path / "boom.wav"
    _write_wav(boom_path, num_frames=int(8000 * 0.05), sample_rate=8000)  # ~50ms, finishes fast
    thunder_path = tmp_path / "thunder.wav"
    _write_wav(thunder_path, num_frames=8000 * 2, sample_rate=8000)  # long enough to still be busy

    calls = []
    original_find_channel = pygame.mixer.find_channel

    def spy(*args, **kwargs):
        result = original_find_channel(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(pygame.mixer, "find_channel", spy)

    q = queue.Queue()
    worker_thread = threading.Thread(target=pygame_worker_process, args=(q,), daemon=True)
    worker_thread.start()

    q.put({"action": "play", "keyword": "boom", "file_path": str(boom_path), "one_shot": True, "base_volume": 0.5})
    time.sleep(0.15)  # let "boom" finish and free the single shared channel
    assert len(calls) == 1

    q.put({"action": "play", "keyword": "thunder", "file_path": str(thunder_path), "one_shot": False, "base_volume": 0.5})
    time.sleep(0.1)  # "thunder" claims the now-free channel and starts playing on it
    assert len(calls) == 2

    q.put({"action": "play", "keyword": "boom", "file_path": str(boom_path), "one_shot": True, "base_volume": 0.5})
    time.sleep(0.1)

    q.put(None)
    worker_thread.join(timeout=2)

    # The fix: boom's replay must actually be attempted (a 3rd find_channel call) rather than
    # silently dropped just because its OLD channel object happens to be busy with thunder now.
    assert len(calls) == 3
    assert "its old channel was reassigned to another sound - proceeding" in capsys.readouterr().out
    assert not worker_thread.is_alive()


# ---------------------------------------------------------------------------
# AudioManager.play - loops, one-shots, periodic re-fires
# ---------------------------------------------------------------------------

def test_play_loop_dispatches_command_and_tracks_state(audio_manager, tmp_path):
    wav_path = tmp_path / "rain.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    callback = _recorder()
    root = FakeRoot()

    result = audio_manager.play(
        keyword="rain",
        file_info={"file_path": str(wav_path), "one_shot": False},
        on_ui_refresh_callback=callback,
        root_window_widget=root,
    )

    assert result is True
    assert len(callback.calls) == 1
    assert audio_manager.active_sounds["rain"]["is_one_shot"] is False
    assert audio_manager.active_sounds["rain"]["is_periodic"] is False

    queued = audio_manager.command_queue.get_nowait()
    assert queued == {
        "action": "play", "keyword": "rain", "file_path": str(wav_path),
        "one_shot": False, "base_volume": 0.5,
    }


def test_play_applies_context_volume_multiplier_to_effective_volume(audio_manager, tmp_path):
    """The context multiplier scales what's SENT to the worker, but must not overwrite the
    manually-set base_volume (that's the DM's slider baseline, persisted to volume_history.json
    - a "quiet explosion outside" must not permanently quiet the slider for the next one)."""
    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    audio_manager.volume_history["boom"] = 0.8

    audio_manager.play(
        keyword="boom",
        file_info={"file_path": str(wav_path), "one_shot": True},
        on_ui_refresh_callback=_recorder(),
        root_window_widget=FakeRoot(),
        context_volume_multiplier=0.5,
    )

    queued = audio_manager.command_queue.get_nowait()
    assert queued["base_volume"] == 0.4  # 0.8 * 0.5
    assert audio_manager.active_sounds["boom"]["base_volume"] == 0.8  # manual baseline untouched
    assert audio_manager.active_sounds["boom"]["context_volume_multiplier"] == 0.5


def test_play_stores_context_modifier_word_for_ui_display(audio_manager, tmp_path):
    """The source word (e.g. "faint") is carried alongside the multiplier purely so the UI can
    show the DM why a track sounds different from where its slider is sitting."""
    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)

    audio_manager.play(
        keyword="boom",
        file_info={"file_path": str(wav_path), "one_shot": True},
        on_ui_refresh_callback=_recorder(),
        root_window_widget=FakeRoot(),
        context_volume_multiplier=0.4,
        context_modifier_word="faint",
    )

    assert audio_manager.active_sounds["boom"]["context_modifier_word"] == "faint"


def test_play_defaults_context_modifier_word_to_none(audio_manager, tmp_path):
    wav_path = tmp_path / "rain.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)

    audio_manager.play(
        keyword="rain",
        file_info={"file_path": str(wav_path), "one_shot": False},
        on_ui_refresh_callback=_recorder(),
        root_window_widget=FakeRoot(),
    )

    assert audio_manager.active_sounds["rain"]["context_modifier_word"] is None


def test_play_clamps_effective_volume_at_one(audio_manager, tmp_path):
    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    audio_manager.volume_history["boom"] = 0.9

    audio_manager.play(
        keyword="boom",
        file_info={"file_path": str(wav_path), "one_shot": True},
        on_ui_refresh_callback=_recorder(),
        root_window_widget=FakeRoot(),
        context_volume_multiplier=1.5,  # 0.9 * 1.5 = 1.35, must clamp to 1.0
    )

    queued = audio_manager.command_queue.get_nowait()
    assert queued["base_volume"] == 1.0


def test_play_returns_false_for_missing_file(audio_manager):
    result = audio_manager.play(
        keyword="ghost",
        file_info={"file_path": "does/not/exist.wav", "one_shot": True},
        on_ui_refresh_callback=_recorder(),
        root_window_widget=FakeRoot(),
    )

    assert result is False
    assert "ghost" not in audio_manager.active_sounds


def test_play_returns_false_when_keyword_already_active(audio_manager, tmp_path):
    wav_path = tmp_path / "rain.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    file_info = {"file_path": str(wav_path), "one_shot": False}
    callback = _recorder()
    root = FakeRoot()

    assert audio_manager.play("rain", file_info, callback, root) is True
    assert audio_manager.play("rain", file_info, callback, root) is False
    assert len(callback.calls) == 1  # second attempt never re-triggered the callback


def test_play_one_shot_schedules_auto_clear(audio_manager, tmp_path):
    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    callback = _recorder()
    root = FakeRoot()

    audio_manager.play("boom", {"file_path": str(wav_path), "one_shot": True}, callback, root)

    assert "boom" in audio_manager.one_shot_timers
    task_id = audio_manager.one_shot_timers["boom"]

    root.fire(task_id)

    assert "boom" not in audio_manager.active_sounds
    assert "boom" not in audio_manager.one_shot_timers
    assert len(callback.calls) == 2  # once on trigger, once on auto-clear


def test_play_periodic_reschedules_and_refires(audio_manager, tmp_path):
    wav_path = tmp_path / "thunder.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    callback = _recorder()
    root = FakeRoot()
    file_info = {"file_path": str(wav_path), "one_shot": True}
    periodic_key = "thunder @ every 8s"

    result = audio_manager.play(periodic_key, file_info, callback, root, periodic_interval=8.0)

    assert result is True
    assert audio_manager.active_sounds[periodic_key]["is_periodic"] is True
    assert audio_manager.active_sounds[periodic_key]["duration"] == 8.0

    first_cmd = audio_manager.command_queue.get_nowait()
    assert first_cmd["keyword"] == periodic_key

    first_task_id = audio_manager.periodic_loops[periodic_key]
    first_start_time = audio_manager.active_sounds[periodic_key]["start_time"]

    root.fire(first_task_id)  # simulate the 8-second interval elapsing

    second_cmd = audio_manager.command_queue.get_nowait()
    assert second_cmd["keyword"] == periodic_key
    assert audio_manager.active_sounds[periodic_key]["start_time"] >= first_start_time
    # A fresh timer must be scheduled so it keeps re-firing indefinitely
    assert audio_manager.periodic_loops[periodic_key] != first_task_id


def test_execute_periodic_fire_carries_context_multiplier_forward(audio_manager, tmp_path):
    """A recurring 'distant explosion every 20 seconds' must stay distant-sounding on every
    re-fire, not just the first shot."""
    wav_path = tmp_path / "thunder.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    callback = _recorder()
    root = FakeRoot()
    file_info = {"file_path": str(wav_path), "one_shot": True}
    periodic_key = "thunder @ every 8s"
    audio_manager.volume_history["thunder"] = 0.8

    audio_manager.play(periodic_key, file_info, callback, root, periodic_interval=8.0, context_volume_multiplier=0.5)
    audio_manager.command_queue.get_nowait()  # discard the initial play command

    task_id = audio_manager.periodic_loops[periodic_key]
    root.fire(task_id)  # simulate the 8-second interval elapsing

    refire_cmd = audio_manager.command_queue.get_nowait()
    assert refire_cmd["base_volume"] == 0.4  # 0.8 * 0.5, still quieted on re-fire


def test_play_periodic_uses_cleaned_keyword_for_volume_history(audio_manager, tmp_path):
    wav_path = tmp_path / "thunder.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    audio_manager.volume_history["thunder"] = 0.77

    audio_manager.play(
        "thunder @ every 8s", {"file_path": str(wav_path), "one_shot": True},
        _recorder(), FakeRoot(), periodic_interval=8.0,
    )

    queued = audio_manager.command_queue.get_nowait()
    assert queued["base_volume"] == 0.77


# ---------------------------------------------------------------------------
# Volume controls
# ---------------------------------------------------------------------------

def test_update_master_volume_rescales_widgets_and_queues_command(audio_manager):
    widget = FakeWidget()
    audio_manager.active_sounds["rain"] = {"base_volume": 0.4, "visual_bar_widget": widget}

    audio_manager.update_master_volume("50")  # Tkinter Scale passes a string

    assert audio_manager.master_scale == 0.5
    assert widget.value == 20  # 0.4 * 0.5 * 100
    assert audio_manager.command_queue.get_nowait() == {"action": "update_master", "value": 0.5}


def test_update_master_volume_reflects_context_multiplier_in_the_output_bar(audio_manager):
    """The 'Live Output' bar must reflect what's actually playing (including any active
    context adjustment), not just base_volume * master_scale - otherwise a "faint" explosion
    would show the same output level as an unmodified one despite genuinely playing quieter."""
    widget = FakeWidget()
    audio_manager.active_sounds["boom"] = {
        "base_volume": 0.8, "context_volume_multiplier": 0.5, "visual_bar_widget": widget,
    }

    audio_manager.update_master_volume("50")

    assert widget.value == 20  # 0.8 * 0.5 (context) * 0.5 (master) * 100


def test_update_individual_volume_rescales_widget_and_queues_command(audio_manager):
    widget = FakeWidget()
    audio_manager.master_scale = 0.5
    audio_manager.active_sounds["rain"] = {"base_volume": 0.4, "visual_bar_widget": widget}

    audio_manager.update_individual_volume("rain", "90")

    assert audio_manager.active_sounds["rain"]["base_volume"] == 0.9
    assert widget.value == 45  # 0.9 * 0.5 * 100
    assert audio_manager.command_queue.get_nowait() == {
        "action": "update_individual", "keyword": "rain", "value": 0.9,
    }


def test_update_individual_volume_resets_context_multiplier_to_neutral(audio_manager):
    """A manual slider drag is the DM taking explicit control - the slider position should
    mean exactly what it shows, not 'your drag times an invisible automatic factor.'"""
    audio_manager.active_sounds["rain"] = {
        "base_volume": 0.4, "context_volume_multiplier": 0.5, "context_modifier_word": "faint",
    }

    audio_manager.update_individual_volume("rain", "70")

    assert audio_manager.active_sounds["rain"]["context_volume_multiplier"] == 1.0
    assert audio_manager.active_sounds["rain"]["context_modifier_word"] is None


def test_update_individual_volume_ignores_unknown_keyword(audio_manager):
    audio_manager.update_individual_volume("nonexistent", "90")

    with pytest.raises(queue.Empty):
        audio_manager.command_queue.get_nowait()


# ---------------------------------------------------------------------------
# Stopping tracks + volume-history persistence
# ---------------------------------------------------------------------------

def test_stop_track_persists_cleaned_history_key(audio_manager, tmp_path):
    history_file = tmp_path / "volume_history.json"
    audio_manager.active_sounds["fireball #2-123"] = {"base_volume": 0.7}
    audio_manager.one_shot_timers["fireball #2-123"] = 99
    callback = _recorder()

    result = audio_manager.stop_track_with_gui_sync("fireball #2-123", callback, fade_ms=500)

    assert result is True
    assert audio_manager.volume_history["fireball"] == 0.7
    assert json.loads(history_file.read_text())["fireball"] == 0.7
    assert "fireball #2-123" not in audio_manager.active_sounds
    assert "fireball #2-123" not in audio_manager.one_shot_timers
    assert len(callback.calls) == 1
    assert audio_manager.command_queue.get_nowait() == {
        "action": "stop_track", "keyword": "fireball #2-123", "fade_ms": 500,
    }


def test_stop_track_returns_false_for_inactive_keyword(audio_manager):
    assert audio_manager.stop_track_with_gui_sync("nothing", _recorder()) is False


def test_stop_all_sounds_persists_all_cleaned_history_keys(audio_manager, tmp_path):
    history_file = tmp_path / "volume_history.json"
    audio_manager.active_sounds = {
        "rain": {"base_volume": 0.6},
        "thunder @ every 8s": {"base_volume": 0.8},
        "fireball #3-482": {"base_volume": 0.9},
    }
    audio_manager.periodic_loops["thunder @ every 8s"] = 1
    audio_manager.one_shot_timers["fireball #3-482"] = 2
    callback = _recorder()

    audio_manager.stop_all_sounds_with_fade(save_history_callback=callback, fade_ms=250)

    saved = json.loads(history_file.read_text())
    assert saved == {"rain": 0.6, "thunder": 0.8, "fireball": 0.9}
    assert audio_manager.active_sounds == {}
    assert audio_manager.periodic_loops == {}
    assert audio_manager.one_shot_timers == {}
    assert len(callback.calls) == 1
    assert audio_manager.command_queue.get_nowait() == {"action": "stop_all", "fade_ms": 250}


def test_stop_all_sounds_tolerates_missing_callback(audio_manager):
    audio_manager.active_sounds = {"rain": {"base_volume": 0.5}}

    audio_manager.stop_all_sounds_with_fade(save_history_callback=None)  # must not raise

    assert audio_manager.active_sounds == {}


# ---------------------------------------------------------------------------
# Startup history loading
# ---------------------------------------------------------------------------

def test_load_history_from_disk_reads_existing_file(monkeypatch, tmp_path):
    history_file = tmp_path / "volume_history.json"
    history_file.write_text(json.dumps({"rain": 0.6}))
    monkeypatch.setattr("dm_mixer.audio.Queue", queue.Queue)
    monkeypatch.setattr("dm_mixer.audio.Process", FakeProcess)
    monkeypatch.setattr("dm_mixer.audio.HISTORY_FILE", str(history_file))

    manager = AudioManager()

    assert manager.volume_history == {"rain": 0.6}


def test_load_history_from_disk_ignores_corrupt_file(monkeypatch, tmp_path, capsys):
    history_file = tmp_path / "volume_history.json"
    history_file.write_text("{ not valid json")
    monkeypatch.setattr("dm_mixer.audio.Queue", queue.Queue)
    monkeypatch.setattr("dm_mixer.audio.Process", FakeProcess)
    monkeypatch.setattr("dm_mixer.audio.HISTORY_FILE", str(history_file))

    manager = AudioManager()  # must not raise

    assert manager.volume_history == {}
    assert "[ERROR-AUDIO-HISTORY]" in capsys.readouterr().err


def test_load_history_from_disk_ignores_empty_file(monkeypatch, tmp_path):
    history_file = tmp_path / "volume_history.json"
    history_file.write_text("")
    monkeypatch.setattr("dm_mixer.audio.Queue", queue.Queue)
    monkeypatch.setattr("dm_mixer.audio.Process", FakeProcess)
    monkeypatch.setattr("dm_mixer.audio.HISTORY_FILE", str(history_file))

    manager = AudioManager()

    assert manager.volume_history == {}


# ---------------------------------------------------------------------------
# Worker shutdown
# ---------------------------------------------------------------------------

def test_shutdown_terminates_a_hung_worker(monkeypatch, tmp_path):
    monkeypatch.setattr("dm_mixer.audio.Queue", queue.Queue)
    monkeypatch.setattr("dm_mixer.audio.Process", lambda target, args, daemon: FakeProcess(target, args, daemon, hangs=True))
    monkeypatch.setattr("dm_mixer.audio.HISTORY_FILE", str(tmp_path / "volume_history.json"))
    manager = AudioManager()

    manager.shutdown()

    assert manager.command_queue.get_nowait() is None
    assert manager.worker.terminated is True


def test_shutdown_leaves_a_cleanly_exiting_worker_alone(audio_manager):
    audio_manager.shutdown()

    assert audio_manager.command_queue.get_nowait() is None
    assert audio_manager.worker.terminated is False


# ---------------------------------------------------------------------------
# Worker process supervision
# ---------------------------------------------------------------------------

def _drain(command_queue):
    commands = []
    while True:
        try:
            commands.append(command_queue.get_nowait())
        except queue.Empty:
            break
    return commands


def test_check_worker_health_returns_false_when_worker_is_healthy(audio_manager):
    original_worker = audio_manager.worker

    result = audio_manager.check_worker_health(FakeRoot(), _recorder())

    assert result is False
    assert audio_manager.worker is original_worker  # untouched, no restart


def test_check_worker_health_restarts_a_dead_worker(audio_manager, capsys):
    audio_manager.worker._alive = False  # simulate a crash
    callback = _recorder()

    result = audio_manager.check_worker_health(FakeRoot(), callback)

    assert result is True
    assert audio_manager.worker.is_alive() is True  # a fresh worker was spawned and started
    assert callback.calls == [True]
    assert "crashed" in capsys.readouterr().err


def test_check_worker_health_restarts_a_hung_worker(audio_manager, capsys):
    """A worker that's still alive but hasn't ticked its heartbeat in far longer than it
    would even if fully idle - a stuck-but-not-crashed worker, not just a quiet one."""
    audio_manager.heartbeat.value = time.time() - 999

    result = audio_manager.check_worker_health(FakeRoot(), _recorder())

    assert result is True
    assert "stopped responding" in capsys.readouterr().err


def test_check_worker_health_kills_rather_than_just_terminates_a_hung_worker(audio_manager):
    """A hung worker may be ignoring terminate()'s SIGTERM outright - escalate straight to
    kill() (SIGKILL on POSIX, identical to terminate() on Windows) rather than hoping."""
    old_worker = audio_manager.worker
    audio_manager.heartbeat.value = time.time() - 999

    audio_manager.check_worker_health(FakeRoot(), _recorder())

    assert old_worker.killed is True


def test_check_worker_health_skips_kill_when_worker_is_already_dead(audio_manager):
    """No point signaling a process that's already gone - and multiprocessing.Process.kill()
    on an already-exited process is the kind of thing worth not relying on being harmless."""
    old_worker = audio_manager.worker
    old_worker._alive = False  # simulate a crash

    audio_manager.check_worker_health(FakeRoot(), _recorder())

    assert old_worker.killed is False


def test_check_worker_health_tolerates_teardown_failure_and_still_restarts(audio_manager, capsys):
    audio_manager.heartbeat.value = time.time() - 999  # hung, not dead - "dead" skips kill() entirely

    def raising_kill():
        raise RuntimeError("process handle already gone")

    audio_manager.worker.kill = raising_kill

    result = audio_manager.check_worker_health(FakeRoot(), _recorder())

    assert result is True
    assert audio_manager.worker.is_alive() is True  # a fresh worker still gets spawned
    assert "Trouble tearing down" in capsys.readouterr().err


def test_check_worker_health_warns_when_old_worker_will_not_die(audio_manager, capsys):
    """Spawning a replacement while the old one might still be holding the audio device is a
    known, accepted risk (SIGKILL essentially never fails in practice) - but it must be logged
    loudly rather than silently proceeding as if nothing unusual happened."""
    audio_manager.heartbeat.value = time.time() - 999
    audio_manager.worker.hangs = True  # even kill()/join() won't clear is_alive()

    result = audio_manager.check_worker_health(FakeRoot(), _recorder())

    assert result is True  # a replacement is still spawned
    assert audio_manager.worker.is_alive() is True  # the NEW worker, not the stuck old one
    assert "would not die" in capsys.readouterr().err


def test_check_worker_health_resumes_active_background_loops(audio_manager, tmp_path):
    wav_path = tmp_path / "rain.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    audio_manager.active_sounds["rain"] = {
        "base_volume": 0.6, "context_volume_multiplier": 0.5, "file_path": str(wav_path),
        "is_one_shot": False, "is_periodic": False,
    }
    audio_manager.master_scale = 0.7
    audio_manager.worker._alive = False

    audio_manager.check_worker_health(FakeRoot(), _recorder())

    commands = _drain(audio_manager.command_queue)
    assert {"action": "update_master", "value": 0.7} in commands
    # The DM's manual master volume must apply to the resumed loop too, not the worker's
    # fresh-restart default of 1.0.
    assert {
        "action": "play", "keyword": "rain", "file_path": str(wav_path),
        "one_shot": False, "base_volume": 0.3,  # 0.6 base x 0.5 context
    } in commands


def test_check_worker_health_does_not_resume_one_shots_or_periodic_entries(audio_manager, tmp_path):
    """One-shots are momentary - replaying one late would be the wrong sound at the wrong
    time. Periodic re-fires resume on their own next scheduled tick without help."""
    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    audio_manager.active_sounds["boom"] = {
        "base_volume": 0.5, "file_path": str(wav_path), "is_one_shot": True, "is_periodic": False,
    }
    audio_manager.active_sounds["thunder @ every 8s"] = {
        "base_volume": 0.5, "file_path": str(wav_path), "is_one_shot": False, "is_periodic": True,
    }
    audio_manager.worker._alive = False

    audio_manager.check_worker_health(FakeRoot(), _recorder())

    play_commands = [c for c in _drain(audio_manager.command_queue) if c["action"] == "play"]
    assert play_commands == []


def test_check_worker_health_skips_resuming_a_loop_whose_file_no_longer_exists(audio_manager, tmp_path):
    audio_manager.active_sounds["gone"] = {
        "base_volume": 0.5, "file_path": str(tmp_path / "missing.wav"),
        "is_one_shot": False, "is_periodic": False,
    }
    audio_manager.worker._alive = False

    audio_manager.check_worker_health(FakeRoot(), _recorder())

    play_commands = [c for c in _drain(audio_manager.command_queue) if c["action"] == "play"]
    assert play_commands == []


def test_worker_updates_heartbeat_even_when_idle(monkeypatch):
    """The worker must tick its heartbeat on its own even with nothing queued - otherwise an
    idle-but-healthy worker would look indistinguishable from a hung one."""
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.setattr("dm_mixer.audio.WORKER_QUEUE_POLL_SECONDS", 0.05)

    class FakeHeartbeat:
        value = 0.0

    heartbeat = FakeHeartbeat()
    q = queue.Queue()  # left empty on purpose, to force at least one idle tick

    def stop_soon():
        time.sleep(0.15)
        q.put(None)

    threading.Thread(target=stop_soon, daemon=True).start()
    before = time.time()

    pygame_worker_process(q, heartbeat)

    assert heartbeat.value >= before


# ---------------------------------------------------------------------------
# Disk-write / callback failure resilience
# ---------------------------------------------------------------------------

def test_stop_track_tolerates_history_write_failure(monkeypatch, audio_manager, capsys):
    # Point HISTORY_FILE at a directory, so open(..., "w") raises IsADirectoryError/PermissionError
    monkeypatch.setattr("dm_mixer.audio.HISTORY_FILE", ".")
    audio_manager.active_sounds["rain"] = {"base_volume": 0.5}

    result = audio_manager.stop_track_with_gui_sync("rain", _recorder())  # must not raise

    assert result is True
    # Regression: a DM's saved volume levels used to be able to silently fail to persist on
    # every session close with zero indication anything went wrong.
    assert "[ERROR-AUDIO-HISTORY]" in capsys.readouterr().err


def test_stop_all_tolerates_history_write_failure(monkeypatch, audio_manager, capsys):
    monkeypatch.setattr("dm_mixer.audio.HISTORY_FILE", ".")
    audio_manager.active_sounds["rain"] = {"base_volume": 0.5}

    audio_manager.stop_all_sounds_with_fade(save_history_callback=None)  # must not raise

    assert audio_manager.active_sounds == {}
    assert "[ERROR-AUDIO-HISTORY]" in capsys.readouterr().err


def test_stop_all_tolerates_save_history_callback_exception(audio_manager, capsys):
    def exploding_callback():
        raise RuntimeError("UI teardown failed")

    audio_manager.stop_all_sounds_with_fade(save_history_callback=exploding_callback)  # must not raise

    assert "[ERROR-AUDIO-CALLBACK]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Audio driver fallback
# ---------------------------------------------------------------------------

def test_worker_falls_back_to_dummy_driver_when_no_real_device_available(monkeypatch):
    attempts = []
    original_init = pygame.mixer.init

    def flaky_init(*args, **kwargs):
        attempts.append(dict(os.environ).get("SDL_AUDIODRIVER"))
        if len(attempts) < 3:
            raise pygame.error("no audio device")
        return original_init(*args, **kwargs)  # let the 3rd (dummy-driver) attempt really succeed

    monkeypatch.setattr(pygame.mixer, "init", flaky_init)
    monkeypatch.delenv("SDL_AUDIODRIVER", raising=False)

    q = queue.Queue()
    q.put(None)

    pygame_worker_process(q)  # must not raise despite the first two init attempts failing

    assert len(attempts) == 3
    assert attempts[-1] == "dummy"


def test_shutdown_tolerates_queue_put_failure(audio_manager, capsys):
    def raising_put(_value):
        raise RuntimeError("queue is closed")

    audio_manager.command_queue.put = raising_put

    audio_manager.shutdown()  # must not raise despite the put() failure

    assert audio_manager.worker.is_alive() is False
    assert "[ERROR-AUDIO-SHUTDOWN]" in capsys.readouterr().err


def test_play_one_shot_tolerates_stale_timer_cancel_failure(audio_manager, tmp_path, capsys):
    class RaisingAfterCancelRoot(FakeRoot):
        def after_cancel(self, task_id):
            raise RuntimeError("timer already gone")

    wav_path = tmp_path / "boom.wav"
    _write_wav(wav_path, num_frames=8000, sample_rate=8000)
    audio_manager.one_shot_timers["boom"] = 999  # stale timer, no matching active_sounds entry

    result = audio_manager.play(
        "boom", {"file_path": str(wav_path), "one_shot": True},
        _recorder(), RaisingAfterCancelRoot(),
    )

    assert result is True
    # Regression: this was the one remaining bare `except: pass` after the batch-1 sweep,
    # spotted in review - play() runs on speech.py's background thread, not the Tk main
    # thread, so a failure here can be a genuine cross-thread Tk issue, not just a harmless
    # stale timer id, and deserves the same logging as every other site now.
    assert "[ERROR-AUDIO-TIMER]" in capsys.readouterr().err
    assert "boom" in audio_manager.active_sounds


def test_worker_logs_and_continues_when_sound_construction_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    bogus_path = tmp_path / "not_really_audio.wav"
    bogus_path.write_bytes(b"this is not valid audio data at all")

    q = queue.Queue()
    q.put({"action": "play", "keyword": "bad", "file_path": str(bogus_path), "one_shot": True, "base_volume": 0.5})
    q.put(None)

    pygame_worker_process(q)  # must not crash; the bad Sound() call is caught and logged

    assert "[ERROR-AUDIO-WORKER] Failed playing" in capsys.readouterr().err
