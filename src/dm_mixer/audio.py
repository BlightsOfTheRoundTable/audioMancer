import os
import json
import pygame
from dm_mixer.utils import calculate_gains, HISTORY_FILE

class AudioManager:
    def __init__(self):
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(16)
        self.active_sounds = {}
        self.master_scale = 1.0
        
        # Centralized volume configuration cache
        self.volume_history = {}
        self.load_history_from_disk()

    def load_history_from_disk(self):
        """Populates the local cache directly from the user profile folder on boot."""
        if os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > 0:
            try:
                with open(HISTORY_FILE, "r") as f:
                    self.volume_history = json.load(f)
                print(f"💾 AudioManager loaded volume balances for {len(self.volume_history)} assets.")
            except Exception as e:
                print(f"⚠️ Could not load history file: {e}")

    def play(self, keyword, file_info, on_ui_refresh_callback):
        """Finds an open audio channel and loops or hits an asset file automatically."""
        file_path = file_info["file_path"]
        if keyword in self.active_sounds or not os.path.exists(file_path):
            return False
            
        try:
            sound = pygame.mixer.Sound(file_path)
            channel = pygame.mixer.find_channel()
            if not channel:
                print("\n⚠️ No free audio channels available!")
                return False
                
            # Recover historical mix volume, fallback to 50%
            target_base_volume = self.volume_history.get(keyword, 0.5)
            channel.set_volume(calculate_gains(target_base_volume, self.master_scale))
            
            if file_info["one_shot"]:
                channel.play(sound, loops=0)
                print(f"\n💥 ONE-SHOT EFFECT TRIGGERED: '{keyword}'")
            else:
                channel.play(sound, loops=-1)
                self.active_sounds[keyword] = {
                    "channel": channel, 
                    "sound": sound, 
                    "base_volume": target_base_volume,
                    "slider_widget": None,
                    "visual_bar_widget": None
                }
                on_ui_refresh_callback()
            return True
        except Exception as e:
            print(f"\n❌ Error playing {file_path}: {e}")
            return False

    def update_master_volume(self, val):
        self.master_scale = float(val) / 100.0
        for keyword, sound_data in self.active_sounds.items():
            scaled_vol = sound_data["base_volume"] * self.master_scale
            sound_data["channel"].set_volume(scaled_vol)
            if sound_data.get("visual_bar_widget"):
                sound_data["visual_bar_widget"].config(value=int(scaled_vol * 100))

    def update_individual_volume(self, keyword, val):
        if keyword in self.active_sounds:
            base_vol_float = float(val) / 100.0
            self.active_sounds[keyword]["base_volume"] = base_vol_float
            self.active_sounds[keyword]["channel"].set_volume(base_vol_float * self.master_scale)
            if self.active_sounds[keyword].get("visual_bar_widget"):
                scaled_percentage = int(base_vol_float * self.master_scale * 100)
                self.active_sounds[keyword]["visual_bar_widget"].config(value=scaled_percentage)

    def stop_track_with_gui_sync(self, keyword, callback, fade_ms=1000):
        """Stops a single channel via UI click and saves its volume parameter."""
        if keyword in self.active_sounds:
            self.volume_history[keyword] = self.active_sounds[keyword]["base_volume"]
            try:
                with open(HISTORY_FILE, "w") as f:
                    json.dump(self.volume_history, f, indent=2)
            except Exception as e:
                print(f"❌ Error updating track history: {e}")

            channel = self.active_sounds[keyword]["channel"]
            if channel.get_busy():
                channel.fadeout(fade_ms)
            del self.active_sounds[keyword]
            callback()
            return True
        return False

    def stop_all_sounds_with_fade(self, save_history_callback, fade_ms=3000):
        """Commits mixing cache to file registry, clears screen layout, and drops audio gains."""
        # 1. Update the local configuration dictionary cache
        for keyword, sound_data in self.active_sounds.items():
            self.volume_history[keyword] = sound_data["base_volume"]
            
        # 2. Write everything out cleanly to our physical home workspace json file
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.volume_history, f, indent=2)
            print("\n💾 Track levels saved to system workspace profile.")
        except Exception as e:
            print(f"❌ Error writing history file: {e}")

        # 3. Create a local copy of our hardware channel connections before wiping memory references
        channels_to_fade = [data["channel"] for data in self.active_sounds.values()]
        
        # 4. WIPE THE MEMORY DICTIONARY BEFORE INVOKING THE REPAINT HANDLER
        # This guarantees that app.py sees a completely blank dashboard registry list!
        self.active_sounds.clear()
        
        # 5. Tell the main window UI layout tab to instantly erase the slider rows
        if save_history_callback:
            try:
                save_history_callback()
            except Exception as e:
                print(f"⚠️ UI layout refresh callback failed: {e}")

        # 6. Smoothly drop our background channel audio levels to silence
        for channel in channels_to_fade:
            try:
                if channel.get_busy():
                    channel.fadeout(fade_ms)
            except Exception as e:
                pass
