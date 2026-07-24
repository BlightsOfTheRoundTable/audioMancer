import os
import json
import platform
import queue
import re
import sys
import pygame
import time
import struct
import traceback
from multiprocessing import Process, Queue, Value
from dm_mixer.utils import calculate_gains, effective_volume, HISTORY_FILE

# How often the worker wakes up on its own even with nothing queued, so its heartbeat still
# advances while genuinely idle - without this, an idle-but-healthy worker would look "stuck"
# to the health check below just from having no commands to process.
WORKER_QUEUE_POLL_SECONDS = 1.0

# How stale the heartbeat can get before the worker is considered hung, not just quiet. Must
# stay comfortably above WORKER_QUEUE_POLL_SECONDS so a healthy-but-idle worker never trips it.
HEARTBEAT_STALE_SECONDS = 5.0

# How often AudioManager.check_worker_health() should be polled by the caller (app.py).
WORKER_HEALTH_CHECK_INTERVAL_MS = 2000

def get_audio_file_duration(file_path):
    """Extracts absolute playback length in seconds by walking the RIFF chunk table."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".wav":
            with open(file_path, 'rb') as f:
                riff_id, _, wave_id = struct.unpack('<4sI4s', f.read(12))
                if riff_id != b'RIFF' or wave_id != b'WAVE':
                    return 3.0

                channels = sample_rate = bits_per_sample = data_size = None
                while True:
                    header = f.read(8)
                    if len(header) < 8:
                        break
                    chunk_id, chunk_size = struct.unpack('<4sI', header)

                    if chunk_id == b'fmt ':
                        fmt_data = f.read(chunk_size)
                        channels = struct.unpack('<H', fmt_data[2:4])[0]
                        sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
                        bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
                    elif chunk_id == b'data':
                        data_size = chunk_size
                        break
                    else:
                        # Skip unknown chunks (LIST/JUNK/etc), honoring word-alignment padding
                        f.seek(chunk_size + (chunk_size % 2), 1)

                if not all([channels, sample_rate, bits_per_sample, data_size]):
                    return 3.0

                duration = data_size / (sample_rate * channels * (bits_per_sample / 8))
                return max(0.5, duration)
        elif ext in [".mp3", ".ogg"]:
            # Fallback estimation based on average bitrates if using compressed campaigns assets
            file_size = os.path.getsize(file_path)
            return max(1.0, (file_size * 8) / 128000) # Assumes standard 128kbps baseline
    except Exception:
        pass
    return 3.0  # Production fallback guess if metadata headers are corrupted

def _base_keyword(keyword):
    """Strips periodic (' @ every Ns') and burst (' #N-id') suffixes back to the source keyword."""
    base = keyword.split(" @")[0]
    return re.sub(r" #\d+-\d+$", "", base)

def pygame_worker_process(command_queue, heartbeat=None):
    """A completely isolated hardware worker process running the Pygame mixer sandbox.

    heartbeat, if given, is a multiprocessing.Value('d', ...) updated on every loop tick -
    including idle ticks where nothing was queued - so AudioManager.check_worker_health() can
    tell a merely-idle worker apart from one that's actually hung."""
    if platform.system() == "Windows" and "SDL_AUDIODRIVER" not in os.environ:
        # DirectSound avoids exclusive-mode WASAPI collisions with the sounddevice mic stream.
        # macOS (CoreAudio) and Linux don't have this collision, so let SDL pick its own default there.
        # (The explicit "not already set" check also lets tests force the dummy driver.)
        os.environ['SDL_AUDIODRIVER'] = 'directsound'
    try:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    except pygame.error:
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        except pygame.error:
            # No usable audio device at all (e.g. a headless machine) - fall back to SDL's
            # silent driver so the app still runs rather than crashing on startup.
            os.environ['SDL_AUDIODRIVER'] = 'dummy'
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)

    pygame.mixer.set_num_channels(16)
    active_sounds = {}
    master_scale = 1.0
    
    while True:
        try:
            try:
                cmd = command_queue.get(timeout=WORKER_QUEUE_POLL_SECONDS)
            except queue.Empty:
                if heartbeat is not None:
                    heartbeat.value = time.time()
                continue

            if heartbeat is not None:
                heartbeat.value = time.time()

            if cmd is None:
                for sound_data in active_sounds.values():
                    sound_data["channel"].stop()
                pygame.mixer.quit()
                break

            action = cmd.get("action")
            
            if action == "play":
                kw = cmd["keyword"]
                file_path = cmd["file_path"]
                one_shot = cmd["one_shot"]
                base_vol = cmd["base_volume"]
                
                if not os.path.exists(file_path):
                    continue

                existing = active_sounds.get(kw)
                if existing and existing["channel"].get_busy():
                    continue

                try:
                    sound = pygame.mixer.Sound(file_path)
                    channel = pygame.mixer.find_channel()
                    if channel:
                        final_volume = calculate_gains(base_vol, master_scale)
                        channel.set_volume(final_volume)
                        print(f"[WORKER] '{kw}': channel.set_volume({final_volume:.3f}) = base_volume({base_vol:.3f}) x master_scale({master_scale:.3f}); channel reports back {channel.get_volume():.3f}")
                        if one_shot:
                            channel.play(sound, loops=0)
                        else:
                            channel.play(sound, loops=-1)
                        active_sounds[kw] = {"channel": channel, "sound": sound, "base_volume": base_vol}
                except Exception:
                    print(f"\n[ERROR-AUDIO-WORKER] Failed playing {file_path!r}:", file=sys.stderr)
                    traceback.print_exc()

            elif action == "update_master":
                master_scale = cmd["value"]
                for sound_data in active_sounds.values():
                    scaled_vol = sound_data["base_volume"] * master_scale
                    sound_data["channel"].set_volume(scaled_vol)
                    
            elif action == "update_individual":
                kw = cmd["keyword"]
                base_vol = cmd["value"]
                if kw in active_sounds:
                    active_sounds[kw]["base_volume"] = base_vol
                    active_sounds[kw]["channel"].set_volume(base_vol * master_scale)
                    
            elif action == "stop_track":
                kw = cmd["keyword"]
                fade_ms = cmd["fade_ms"]
                if kw in active_sounds:
                    channel = active_sounds[kw]["channel"]
                    if channel.get_busy():
                        channel.fadeout(fade_ms)
                    del active_sounds[kw]
                    
            elif action == "stop_all":
                fade_ms = cmd["fade_ms"]
                for sound_data in active_sounds.values():
                    if sound_data["channel"].get_busy():
                        sound_data["channel"].fadeout(fade_ms)
                active_sounds.clear()
                
        except Exception:
            # A malformed/unexpected command must not kill the worker process outright - the
            # main process would keep queuing commands into it with no way to know it died,
            # silently losing all audio for the rest of the session. Log and keep processing
            # the next command instead.
            print("\n[ERROR-AUDIO-WORKER] Unexpected error processing a command:", file=sys.stderr)
            traceback.print_exc()

class AudioManager:
    def __init__(self):
        self.active_sounds = {}
        self.master_scale = 1.0
        self.one_shot_timers = {}
        self.periodic_loops = {}
        
        self.volume_history = {}
        self.load_history_from_disk()

        self.command_queue = None
        self.heartbeat = None
        self.worker = None
        self._spawn_worker()

    def _spawn_worker(self):
        """Creates a fresh command queue, heartbeat, and worker process. Used both at startup
        and by check_worker_health() to replace a dead/hung worker without reusing any of its
        possibly-corrupted state."""
        self.command_queue = Queue()
        self.heartbeat = Value('d', time.time())
        self.worker = Process(target=pygame_worker_process, args=(self.command_queue, self.heartbeat), daemon=True)
        self.worker.start()
        print("[AUDIO] AudioManager Sandboxed Worker Process spawned successfully.")

    def check_worker_health(self, root_window_widget, on_ui_refresh_callback):
        """Detects a crashed or hung worker process and transparently restarts it, resuming
        any active background loops. Without this, a dead worker would silently lose all audio
        for the rest of the session, with restarting the whole app mid-table as the only fix.
        Returns True if a restart happened, so the caller can surface it to the DM."""
        worker_dead = not self.worker.is_alive()
        worker_hung = not worker_dead and (time.time() - self.heartbeat.value) > HEARTBEAT_STALE_SECONDS
        if not worker_dead and not worker_hung:
            return False

        reason = "crashed" if worker_dead else "stopped responding"
        print(f"\n[ERROR-AUDIO-WORKER] Worker process {reason} - restarting and resuming active loops.", file=sys.stderr)

        if self.worker.is_alive():
            # Escalate straight to kill() rather than terminate() - a hung worker may be
            # ignoring SIGTERM (terminate()'s signal on POSIX; the two are identical on
            # Windows, which has no signal distinction of its own). Spawning a replacement
            # before this one is confirmed dead risks two processes holding the audio device
            # at once, and leaves the old one's handle orphaned and untracked the moment
            # self.worker gets reassigned to the new process below.
            try:
                self.worker.kill()
                self.worker.join(timeout=1.0)
            except Exception:
                print("\n[ERROR-AUDIO-WORKER] Trouble tearing down the old worker before restart:", file=sys.stderr)
                traceback.print_exc()

            if self.worker.is_alive():
                print("\n[ERROR-AUDIO-WORKER] Old worker process would not die - spawning a replacement anyway; the audio device may be briefly contended.", file=sys.stderr)

        self._spawn_worker()

        # Restore the DM's master volume before resuming anything - a fresh worker starts at
        # master_scale=1.0, which would make resumed loops jump to the wrong level.
        self.command_queue.put({"action": "update_master", "value": self.master_scale})

        # Only resume background loops. One-shots are momentary - replaying one late (however
        # long "late" turns out to be) would be the wrong sound at the wrong time. Periodic
        # re-fires resume on their own next scheduled tick without help, since
        # execute_periodic_fire() reads self.command_queue fresh each call rather than
        # capturing a reference to the (now-replaced) old one.
        for keyword, sound_data in list(self.active_sounds.items()):
            if sound_data.get("is_one_shot") or sound_data.get("is_periodic"):
                continue
            file_path = sound_data.get("file_path")
            if not file_path or not os.path.exists(file_path):
                continue
            send_volume = effective_volume(sound_data["base_volume"], sound_data.get("context_volume_multiplier", 1.0))
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_path, "one_shot": False, "base_volume": send_volume})

        on_ui_refresh_callback()
        return True

    def shutdown(self):
        """Gracefully stops the sandboxed worker process on application exit."""
        try:
            self.command_queue.put(None)
        except Exception:
            print("\n[ERROR-AUDIO-SHUTDOWN] Could not signal worker to stop:", file=sys.stderr)
            traceback.print_exc()
        self.worker.join(timeout=2)
        if self.worker.is_alive():
            self.worker.terminate()

    def load_history_from_disk(self):
        if os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > 0:
            try:
                with open(HISTORY_FILE, "r") as f:
                    self.volume_history = json.load(f)
                print(f"[AUDIO] AudioManager loaded volume balances for {len(self.volume_history)} assets.")
            except Exception:
                print("\n[ERROR-AUDIO-HISTORY] Could not load history file:", file=sys.stderr)
                traceback.print_exc()

    def play(self, keyword, file_info, on_ui_refresh_callback, root_window_widget, periodic_interval=None, context_volume_multiplier=1.0, context_modifier_word=None):
        """Dispatches non-blocking IPC commands to the sandboxed audio hardware worker process.

        context_volume_multiplier is an automatic per-utterance adjustment (e.g. "outside" ->
        quieter) layered on top of the DM's manually-set slider baseline. It's kept separate
        from base_volume so it never gets persisted to volume_history.json - a "quiet explosion
        outside" must not permanently quiet the slider for the next fresh explosion.
        context_modifier_word is the actual word that produced the multiplier (e.g. "faint"),
        carried alongside purely so the UI can show the DM why a track sounds different from
        where its slider is sitting.
        """
        file_path = file_info["file_path"]
        if keyword in self.active_sounds or not os.path.exists(file_path):
            return False

        target_base_volume = self.volume_history.get(_base_keyword(keyword), 0.5)
        send_volume = effective_volume(target_base_volume, context_volume_multiplier)
        print(f"[VOLUME] '{keyword}': base_volume={target_base_volume:.3f} x context_multiplier={context_volume_multiplier:.3f} = effective_volume={send_volume:.3f} (sent to worker, before master scale)")

        # FIX: Extract duration via high-performance structural binary scraping instead of Pygame init!
        duration_seconds = get_audio_file_duration(file_path) if periodic_interval is None else periodic_interval
        start_time = time.time()

        if periodic_interval is not None:
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_path, "one_shot": True, "base_volume": send_volume})
            self.active_sounds[keyword] = {"base_volume": target_base_volume, "file_path": file_path, "context_volume_multiplier": context_volume_multiplier, "context_modifier_word": context_modifier_word, "is_one_shot": False, "is_periodic": True, "duration": periodic_interval, "start_time": start_time}
            on_ui_refresh_callback()
            self.schedule_next_periodic_pass(keyword, file_info, periodic_interval, on_ui_refresh_callback, root_window_widget)
            return True

        if file_info["one_shot"]:
            if keyword in self.one_shot_timers:
                try:
                    root_window_widget.after_cancel(self.one_shot_timers[keyword])
                except Exception:
                    # play() is called from speech.py's background listening thread, not the
                    # Tk main thread, so this isn't just "stale timer id" (Tcl's `after cancel`
                    # is a documented no-op for that case) - it can also be a genuine cross-
                    # thread Tk call failing. Not fatal either way (the stale timer firing late
                    # is harmless), but worth logging rather than swallowing blind.
                    print(f"\n[ERROR-AUDIO-TIMER] Could not cancel prior timer for {keyword!r}:", file=sys.stderr)
                    traceback.print_exc()

            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_path, "one_shot": True, "base_volume": send_volume})
            self.active_sounds[keyword] = {"base_volume": target_base_volume, "file_path": file_path, "context_volume_multiplier": context_volume_multiplier, "context_modifier_word": context_modifier_word, "is_one_shot": True, "is_periodic": False, "duration": duration_seconds, "start_time": start_time}
            on_ui_refresh_callback()

            duration_ms = int(duration_seconds * 1000) + 200
            task_id = root_window_widget.after(duration_ms, lambda: self.auto_clear_expired_one_shot(keyword, on_ui_refresh_callback))
            self.one_shot_timers[keyword] = task_id
        else:
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_path, "one_shot": False, "base_volume": send_volume})
            self.active_sounds[keyword] = {"base_volume": target_base_volume, "file_path": file_path, "context_volume_multiplier": context_volume_multiplier, "context_modifier_word": context_modifier_word, "is_one_shot": False, "is_periodic": False, "duration": duration_seconds, "start_time": start_time}
            on_ui_refresh_callback()
        return True

    def schedule_next_periodic_pass(self, keyword, file_info, interval, callback, root):
        if keyword not in self.active_sounds: return
        interval_ms = int(interval * 1000)
        task_id = root.after(interval_ms, lambda: self.execute_periodic_fire(keyword, file_info, interval, callback, root))
        self.periodic_loops[keyword] = task_id

    def execute_periodic_fire(self, keyword, file_info, interval, callback, root):
        if keyword not in self.active_sounds: return
        if os.path.exists(file_info["file_path"]):
            entry = self.active_sounds[keyword]
            send_volume = effective_volume(entry["base_volume"], entry.get("context_volume_multiplier", 1.0))
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_info["file_path"], "one_shot": True, "base_volume": send_volume})
            entry["start_time"] = time.time()
        self.schedule_next_periodic_pass(keyword, file_info, interval, callback, root)

    def auto_clear_expired_one_shot(self, keyword, on_ui_refresh_callback):
        if keyword in self.active_sounds: del self.active_sounds[keyword]
        if keyword in self.one_shot_timers: del self.one_shot_timers[keyword]
        on_ui_refresh_callback()

    def update_master_volume(self, val):
        self.master_scale = float(val) / 100.0
        self.command_queue.put({"action": "update_master", "value": self.master_scale})
        for sound_data in self.active_sounds.values():
            if sound_data.get("visual_bar_widget"):
                # Include context_volume_multiplier so the "Live Output" bar reflects what's
                # actually playing, not just the slider baseline - a "faint" explosion should
                # visibly show as quieter than its slider position suggests, not just sound it.
                scaled_percentage = int(effective_volume(sound_data["base_volume"], sound_data.get("context_volume_multiplier", 1.0)) * self.master_scale * 100)
                sound_data["visual_bar_widget"].config(value=scaled_percentage)

    def update_individual_volume(self, keyword, val):
        if keyword in self.active_sounds:
            base_vol_float = float(val) / 100.0
            self.active_sounds[keyword]["base_volume"] = base_vol_float
            # A manual drag is the DM taking explicit control - the slider position should mean
            # exactly what it shows, not "your drag times an invisible automatic factor."
            self.active_sounds[keyword]["context_volume_multiplier"] = 1.0
            self.active_sounds[keyword]["context_modifier_word"] = None
            self.command_queue.put({"action": "update_individual", "keyword": keyword, "value": base_vol_float})
            if self.active_sounds[keyword].get("visual_bar_widget"):
                scaled_percentage = int(base_vol_float * self.master_scale * 100)
                self.active_sounds[keyword]["visual_bar_widget"].config(value=scaled_percentage)

    def _save_volume_history(self):
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.volume_history, f, indent=2)
        except Exception:
            print("\n[ERROR-AUDIO-HISTORY] Could not save history file:", file=sys.stderr)
            traceback.print_exc()

    def stop_track_with_gui_sync(self, keyword, callback, fade_ms=1000):
        if keyword in self.active_sounds:
            self.volume_history[_base_keyword(keyword)] = self.active_sounds[keyword]["base_volume"]
            self._save_volume_history()

            if keyword in self.periodic_loops: del self.periodic_loops[keyword]
            if keyword in self.one_shot_timers: del self.one_shot_timers[keyword]

            self.command_queue.put({"action": "stop_track", "keyword": keyword, "fade_ms": fade_ms})
            del self.active_sounds[keyword]
            callback()
            return True
        return False

    def stop_all_sounds_with_fade(self, save_history_callback, fade_ms=3000):
        for keyword, sound_data in self.active_sounds.items():
            self.volume_history[_base_keyword(keyword)] = sound_data["base_volume"]
        self._save_volume_history()

        self.periodic_loops.clear()
        self.one_shot_timers.clear()
        self.command_queue.put({"action": "stop_all", "fade_ms": fade_ms})
        self.active_sounds.clear()
        if save_history_callback:
            try:
                save_history_callback()
            except Exception:
                print("\n[ERROR-AUDIO-CALLBACK] save_history_callback failed:", file=sys.stderr)
                traceback.print_exc()
