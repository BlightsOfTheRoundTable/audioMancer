import os
import pygame
from dm_mixer.utils import calculate_gains

class AudioManager:
    def __init__(self):
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(16)
        self.active_sounds = {}
        self.master_scale = 1.0

    def play(self, keyword, file_info, base_vol, on_ui_refresh_callback):
        if keyword in self.active_sounds or not os.path.exists(file_info["file_path"]):
            return False
            
        sound = pygame.mixer.Sound(file_info["file_path"])
        channel = pygame.mixer.find_channel()
        if not channel:
            return False
            
        channel.set_volume(calculate_gains(base_vol, self.master_scale))
        
        if file_info["one_shot"]:
            channel.play(sound, loops=0)
        else:
            channel.play(sound, loops=-1)
            self.active_sounds[keyword] = {
                "channel": channel, "sound": sound, "base_volume": base_vol
            }
            on_ui_refresh_callback()
        return True

    def stop_track(self, keyword, fade_ms=1000):
        if keyword in self.active_sounds:
            channel = self.active_sounds[keyword]["channel"]
            if channel.get_busy():
                channel.fadeout(fade_ms)
            del self.active_sounds[keyword]
            return True
        return False
