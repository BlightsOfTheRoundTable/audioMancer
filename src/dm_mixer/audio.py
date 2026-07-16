import os
import json
import pygame
import time
import struct # Native high-performance binary block decoder
from multiprocessing import Process, Queue
from dm_mixer.utils import calculate_gains, HISTORY_FILE

def get_audio_file_duration(file_path):
    """Extracts absolute playback length in seconds using zero-overhead binary block decoding."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".wav":
            with open(file_path, 'rb') as f:
                # Rip the RIFF file format layout properties straight from byte index anchors
                f.seek(22)
                channels = struct.unpack('<H', f.read(2))[0]
                sample_rate = struct.unpack('<I', f.read(4))[0]
                f.seek(34)
                bits_per_sample = struct.unpack('<H', f.read(2))[0]
                f.seek(40)
                data_size = struct.unpack('<I', f.read(4))[0]
                
                duration = data_size / (sample_rate * channels * (bits_per_sample / 8))
                return max(0.5, duration)
        elif ext in [".mp3", ".ogg"]:
            # Fallback estimation based on average bitrates if using compressed campaigns assets
            file_size = os.path.getsize(file_path)
            return max(1.0, (file_size * 8) / 128000) # Assumes standard 128kbps baseline
    except Exception:
        pass
    return 3.0  # Production fallback guess if metadata headers are corrupted

def pygame_worker_process(command_queue):
    """A completely isolated hardware worker process running the Pygame mixer sandbox."""
    os.environ['SDL_AUDIODRIVER'] = 'directsound'
    try:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    except pygame.error:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        
    pygame.mixer.set_num_channels(16)
    active_sounds = {}
    master_scale = 1.0
    
    while True:
        try:
            cmd = command_queue.get()
            if cmd is None:
                break
                
            action = cmd.get("action")
            
            if action == "play":
                kw = cmd["keyword"]
                file_path = cmd["file_path"]
                one_shot = cmd["one_shot"]
                base_vol = cmd["base_volume"]
                
                if kw in active_sounds or not os.path.exists(file_path):
                    continue
                    
                try:
                    sound = pygame.mixer.Sound(file_path)
                    channel = pygame.mixer.find_channel()
                    if channel:
                        channel.set_volume(calculate_gains(base_vol, master_scale))
                        if one_shot:
                            channel.play(sound, loops=0)
                        else:
                            channel.play(sound, loops=-1)
                        active_sounds[kw] = {"channel": channel, "sound": sound, "base_volume": base_vol}
                except Exception as e:
                    print(f"\n❌ Worker error playing {file_path}: {e}")
                    
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
            pass

class AudioManager:
    def __init__(self):
        self.active_sounds = {}
        self.master_scale = 1.0
        self.one_shot_timers = {}
        self.periodic_loops = {}
        
        self.volume_history = {}
        self.load_history_from_disk()
        
        self.command_queue = Queue()
        self.worker = Process(target=pygame_worker_process, args=(self.command_queue,), daemon=True)
        self.worker.start()
        print("🔊 AudioManager Sandboxed Worker Process spawned successfully.")

    def load_history_from_disk(self):
        if os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > 0:
            try:
                with open(HISTORY_FILE, "r") as f:
                    self.volume_history = json.load(f)
                print(f"💾 AudioManager loaded volume balances for {len(self.volume_history)} assets.")
            except Exception as e:
                print(f"⚠️ Could not load history file: {e}")

    def play(self, keyword, file_info, on_ui_refresh_callback, root_window_widget, periodic_interval=None):
        """Dispatches non-blocking IPC commands to the sandboxed audio hardware worker process."""
        file_path = file_info["file_path"]
        if keyword in self.active_sounds or not os.path.exists(file_path):
            return False
            
        clean_key = keyword.split(" @")[0]
        target_base_volume = self.volume_history.get(clean_key, 0.5)
        
        # FIX: Extract duration via high-performance structural binary scraping instead of Pygame init!
        duration_seconds = get_audio_file_duration(file_path) if periodic_interval is None else periodic_interval
        start_time = time.time()
        
        if periodic_interval is not None:
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_path, "one_shot": True, "base_volume": target_base_volume})
            self.active_sounds[keyword] = {"base_volume": target_base_volume, "is_one_shot": False, "is_periodic": True, "duration": periodic_interval, "start_time": start_time}
            on_ui_refresh_callback()
            self.schedule_next_periodic_pass(keyword, file_info, periodic_interval, on_ui_refresh_callback, root_window_widget)
            return True
            
        if file_info["one_shot"]:
            if keyword in self.one_shot_timers:
                try: root_window_widget.after_cancel(self.one_shot_timers[keyword])
                except Exception: pass
            
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_path, "one_shot": True, "base_volume": target_base_volume})
            self.active_sounds[keyword] = {"base_volume": target_base_volume, "is_one_shot": True, "is_periodic": False, "duration": duration_seconds, "start_time": start_time}
            on_ui_refresh_callback()
            
            duration_ms = int(duration_seconds * 1000) + 200
            task_id = root_window_widget.after(duration_ms, lambda: self.auto_clear_expired_one_shot(keyword, on_ui_refresh_callback))
            self.one_shot_timers[keyword] = task_id
        else:
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_path, "one_shot": False, "base_volume": target_base_volume})
            self.active_sounds[keyword] = {"base_volume": target_base_volume, "is_one_shot": False, "is_periodic": False, "duration": duration_seconds, "start_time": start_time}
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
            current_slider_vol = self.active_sounds[keyword]["base_volume"]
            self.command_queue.put({"action": "play", "keyword": keyword, "file_path": file_info["file_path"], "one_shot": True, "base_volume": current_slider_vol})
            self.active_sounds[keyword]["start_time"] = time.time()
        self.schedule_next_periodic_pass(keyword, file_info, interval, callback, root)

    def auto_clear_expired_one_shot(self, keyword, on_ui_refresh_callback):
        if keyword in self.active_sounds: del self.active_sounds[keyword]
        if keyword in self.one_shot_timers: del self.one_shot_timers[keyword]
        on_ui_refresh_callback()

    def update_master_volume(self, val):
        self.master_scale = float(val) / 100.0
        self.command_queue.put({"action": "update_master", "value": self.master_scale})

    def update_individual_volume(self, keyword, val):
        if keyword in self.active_sounds:
            base_vol_float = float(val) / 100.0
            self.active_sounds[keyword]["base_volume"] = base_vol_float
            self.command_queue.put({"action": "update_individual", "keyword": keyword, "value": base_vol_float})

    def stop_track_with_gui_sync(self, keyword, callback, fade_ms=1000):
        if keyword in self.active_sounds:
            clean_key = keyword.split(" @")[0]
            self.volume_history[clean_key] = self.active_sounds[keyword]["base_volume"]
            try:
                with open(HISTORY_FILE, "w") as f: json.dump(self.volume_history, f, indent=2)
            except Exception: pass

            if keyword in self.periodic_loops: del self.periodic_loops[keyword]
            if keyword in self.one_shot_timers: del self.one_shot_timers[keyword]

            self.command_queue.put({"action": "stop_track", "keyword": keyword, "fade_ms": fade_ms})
            del self.active_sounds[keyword]
            callback()
            return True
        return False

    def stop_all_sounds_with_fade(self, save_history_callback, fade_ms=3000):
        for keyword, sound_data in self.active_sounds.items():
            clean_key = keyword.split(" @")[0]
            self.volume_history[clean_key] = sound_data["base_volume"]
        try:
            with open(HISTORY_FILE, "w") as f: json.dump(self.volume_history, f, indent=2)
        except Exception: pass

        self.periodic_loops.clear()
        self.one_shot_timers.clear()
        self.command_queue.put({"action": "stop_all", "fade_ms": fade_ms})
        self.active_sounds.clear()
        if save_history_callback:
            try: save_history_callback()
            except Exception: pass
