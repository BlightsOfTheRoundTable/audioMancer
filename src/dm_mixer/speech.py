import queue
import sys
import threading
import random
import time
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

QUANTITY_MAP = {
    "a": 1, "an": 1, "one": 1, "single": 1,
    "two": 2, "couple": 2, "twin": 2, "double": 2,
    "three": 3, "triple": 3, "several": 3, "multiple": 3,
    "four": 4, "few": 4, "many": 5, "handful": 5,
    "five": 5, "six": 6, "half-dozen": 6, "half dozen": 6,
    "dozen": 12, "countless": 5, "barrage": 6, "volley": 5
}

class TranscriptionEngine:
    def __init__(self, audio_manager, on_keyword_triggered_callback):
        self.audio_manager = audio_manager
        self.on_keyword_triggered_callback = on_keyword_triggered_callback
        self.is_running = False
        self.audio_queue = queue.Queue()
        self.stream = None
        self.keyword_mapping = {}
        self.root_window_widget = None

        self.whisper_target_rate = 16000
        self.block_size = 4000

        print("⏳ Loading Whisper model into memory... (This takes a moment)")
        self.model = WhisperModel("base", device="cpu", compute_type="int8")

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[MIC-STATUS] {status}", file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def start(self, active_keyword_map, root_window_widget):
        """Arms the engine and seamlessly starts the microphone stream."""
        self.keyword_mapping = active_keyword_map
        self.root_window_widget = root_window_widget
        self.is_running = True
        self.audio_queue = queue.Queue()
        
        try:
            device_info = sd.query_devices(kind='input')
            self.hardware_sample_rate = int(device_info['default_sample_rate'])
        except Exception:
            self.hardware_sample_rate = 16000

        # Since Pygame lives in a separate sandbox process, this opens flawlessly
        self.stream = sd.InputStream(
            samplerate=self.hardware_sample_rate, 
            channels=1, 
            callback=self.audio_callback, 
            blocksize=self.block_size, 
            dtype="float32"
        )
        self.stream.start()
        print("[DEBUG-SPEECH] Microphone device stream successfully acquired.")
        
        threading.Thread(target=self.run_loop, daemon=True).start()

    def stop(self, save_history_callback):
        self.is_running = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception: pass
        self.audio_manager.stop_all_sounds_with_fade(save_history_callback=save_history_callback)

    def run_loop(self):
        print("[DEBUG-SPEECH-THREAD] Background evaluation thread AWAKE.")
        audio_buffer = np.zeros(0, dtype=np.float32)
        phrase_cooldowns = {}
        
        TIME_WORDS = {
            "few": 4.0, "couple": 4.0, "so": 15.0, "often": 15.0,
            "five": 5.0, "ten": 10.0, "fifteen": 15.0, "thirty": 30.0,
            "minute": 60.0, "dozen": 12.0
        }
        
        while self.is_running:
            try:
                try: data = self.audio_queue.get(timeout=0.5)
                except queue.Empty: continue
                
                raw_samples = data.ravel()
                
                if self.hardware_sample_rate != self.whisper_target_rate:
                    num_target_samples = int(len(raw_samples) * self.whisper_target_rate / self.hardware_sample_rate)
                    audio_chunk_16k = np.interp(
                        np.linspace(0, len(raw_samples), num_target_samples, endpoint=False),
                        xp=np.arange(len(raw_samples)),
                        fp=raw_samples
                    )
                    audio_buffer = np.append(audio_buffer, audio_chunk_16k)
                else:
                    audio_buffer = np.append(audio_buffer, raw_samples)
                
                current_timestamp = time.time()
                
                if len(audio_buffer) >= self.whisper_target_rate * 2:
                    try:
                        segments, _ = self.model.transcribe(audio_buffer, beam_size=3, vad_filter=True, language="en")
                        segments = list(segments)
                    except Exception: continue
                    
                    for segment in segments:
                        clean_text = segment.text.lower().strip()
                        print(f"\r💬 Hearing description: {segment.text.strip()}", end="", flush=True)
                        
                        for keyword, file_info in self.keyword_mapping.items():
                            if keyword in clean_text:
                                if keyword in phrase_cooldowns and current_timestamp - phrase_cooldowns[keyword] < 4.0:
                                    continue
                                
                                phrase_cooldowns[keyword] = current_timestamp
                                periodic_seconds = None
                                
                                if "every" in clean_text:
                                    try:
                                        words = clean_text.split()
                                        if "every" in words:
                                            idx = words.index("every")
                                            sub_slice = words[idx:idx+4]
                                            for token in sub_slice:
                                                if token.isdigit():
                                                    periodic_seconds = float(token)
                                                    break
                                                elif token in TIME_WORDS:
                                                    periodic_seconds = TIME_WORDS[token]
                                                    break
                                            if periodic_seconds is None: periodic_seconds = 8.0
                                    except Exception: periodic_seconds = 8.0

                                if periodic_seconds is not None:
                                    unique_periodic_key = f"{keyword} @ every {int(periodic_seconds)}s"
                                    self.audio_manager.play(
                                        keyword=unique_periodic_key, file_info=file_info,
                                        on_ui_refresh_callback=self.on_keyword_triggered_callback,
                                        root_window_widget=self.root_window_widget, periodic_interval=periodic_seconds
                                    )
                                    continue
                                    
                                fire_count = 1
                                try:
                                    char_index = clean_text.find(keyword)
                                    left_chunk = clean_text[max(0, char_index - 40):char_index].strip()
                                    chunk_words = left_chunk.split()
                                    context_words = chunk_words[-3:] if len(chunk_words) >= 3 else chunk_words
                                    
                                    for word in context_words:
                                        word_clean = word.replace(".", "").replace(",", "").strip()
                                        if word_clean in QUANTITY_MAP:
                                            fire_count = QUANTITY_MAP[word_clean]
                                            break
                                        elif word_clean.isdigit():
                                            fire_count = min(15, int(word_clean))
                                            break
                                except Exception: pass

                                if file_info["one_shot"] and fire_count > 1:
                                    print(f"\n⚡ MULTI-BURST VOLLEY RESOLVED: [{fire_count}x {keyword.upper()}]")
                                    volley_id = str(int(time.time() * 100))[-3:]
                                    
                                    def dispatch_burst(total_shots, kw, info, callback, widget, group_id):
                                        for shot in range(total_shots):
                                            unique_kw_id = f"{kw} #{shot + 1}-{group_id}" if shot > 0 or kw in self.audio_manager.active_sounds else kw
                                            self.audio_manager.play(
                                                keyword=unique_kw_id, file_info=info,
                                                on_ui_refresh_callback=callback, root_window_widget=widget
                                            )
                                            time.sleep(random.uniform(0.15, 0.45))
                                            
                                    threading.Thread(
                                        target=dispatch_burst, 
                                        args=(fire_count, keyword, file_info, self.on_keyword_triggered_callback, self.root_window_widget, volley_id),
                                        daemon=True
                                    ).start()
                                else:
                                    self.audio_manager.play(
                                        keyword=keyword, file_info=file_info,
                                        on_ui_refresh_callback=self.on_keyword_triggered_callback,
                                        root_window_widget=self.root_window_widget
                                    )
                                
                    if len(audio_buffer) >= self.whisper_target_rate * 4:
                        audio_buffer = np.zeros(0, dtype=np.float32)
            except Exception: break

