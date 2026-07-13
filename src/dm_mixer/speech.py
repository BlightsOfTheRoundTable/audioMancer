import queue
import sys
import threading
import traceback  # FIX: Added for deep error inspection logging
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
import random
import time

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
        """Coordinates the microphone stream and feeds chunks to Whisper."""
        print("[DEBUG-SPEECH] Initializing TranscriptionEngine Subsystem...")
        self.audio_manager = audio_manager
        self.on_keyword_triggered_callback = on_keyword_triggered_callback
        
        self.is_running = False
        self.audio_queue = queue.Queue()
        self.stream = None
        self.keyword_mapping = {}
        self.root_window_widget = None

        self.model_size = "base"
        self.sample_rate = 16000
        self.block_size = 4000

        try:
            print("⏳ Loading Whisper model into memory... (This takes a moment)")
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            print("[DEBUG-SPEECH] Whisper model loaded successfully.")
        except Exception as e:
            print(f"[CRITICAL-ERROR-SPEECH] Failed to load Whisper Model: {e}")
            traceback.print_exc()

    def audio_callback(self, indata, frames, time, status):
        """Intercepts raw sound blocks from the system's microphone device."""
        if status:
            print(f"[DEBUG-MIC-STATUS] {status}", file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def start(self, active_keyword_map, root_window_widget):
        """Arms the engine with configuration words and stores the window widget frame."""
        print(f"[DEBUG-SPEECH] start() invoked. Armed Keywords: {list(active_keyword_map.keys())}")
        self.keyword_mapping = active_keyword_map
        self.root_window_widget = root_window_widget
        self.is_running = True
        self.audio_queue = queue.Queue()
        
        try:
            print("[DEBUG-SPEECH] Opening audio device stream...")
            self.stream = sd.InputStream(
                samplerate=self.sample_rate, 
                channels=1, 
                callback=self.audio_callback, 
                blocksize=self.block_size, 
                dtype="float32"
            )
            self.stream.start()
            print("[DEBUG-SPEECH] Microphone device streaming live audio blocks.")
        except Exception as e:
            print(f"[CRITICAL-ERROR-SPEECH] Failed to open microphone stream: {e}")
            traceback.print_exc()
            return

        # Launch transcription loop on a background thread
        print("[DEBUG-SPEECH] Spawning background evaluation loop worker thread...")
        threading.Thread(target=self.run_loop, daemon=True).start()

    def stop(self, save_history_callback):
        """Safely tears down the mic stream and fades out the soundscapes."""
        print("[DEBUG-SPEECH] stop() invoked. Shutting down worker thread.")
        self.is_running = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
                print("[DEBUG-SPEECH] Microphone device stream successfully closed.")
            except Exception as e:
                print(f"⚠️ Error closing microphone stream: {e}")
        
        self.audio_manager.stop_all_sounds_with_fade(
            save_history_callback=save_history_callback
        )

    def run_loop(self):
        """The core continuous evaluation pipeline with keyword temporal phrase lockout gates."""
        print("[DEBUG-SPEECH-THREAD] Background evaluation thread AWAKE and checking chunks.")
        audio_buffer = np.zeros(0, dtype=np.float32)
        
        # FIX: Keep track of recently triggered keywords with absolute timestamps
        phrase_cooldowns = {}
        
        while self.is_running:
            try:
                try:
                    data = self.audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                audio_buffer = np.append(audio_buffer, data.ravel())
                current_timestamp = time.time()
                
                if len(audio_buffer) >= self.sample_rate * 2:
                    try:
                        segments, _ = self.model.transcribe(
                            audio_buffer, beam_size=3, vad_filter=True, language="en"
                        )
                        segments = list(segments)
                    except Exception as e:
                        print(f"\n[ERROR-WHISPER-CORE] Model transcription crash: {e}")
                        continue
                    
                    for segment in segments:
                        clean_text = segment.text.lower().strip()
                        print(f"\r💬 Hearing description: {segment.text.strip()}", end="", flush=True)
                        
                        for keyword, file_info in self.keyword_mapping.items():
                            if keyword in clean_text:
                                
                                # FIX: Check if this precise keyword fired within the last 4 seconds.
                                # If it did, completely skip it to block rolling sentence repetitions.
                                if keyword in phrase_cooldowns:
                                    if current_timestamp - phrase_cooldowns[keyword] < 4.0:
                                        continue
                                    else:
                                        del phrase_cooldowns[keyword]
                                
                                # Arm the text lockout cache block timestamp immediately
                                phrase_cooldowns[keyword] = current_timestamp
                                
                                fire_count = 1
                                
                                # --- UPGRADED FLEXIBLE SUB-STRING CONTEXT WINDOW ---
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
                                except Exception as e:
                                    print(f"\\n[DEBUG-BURST] Quantity fallback: {e}")

                                # --- STAGGERED VOLLEY DISPATCHER WITH UNIQUE INSTANCE TAGGING ---
                                if file_info["one_shot"] and fire_count > 1:
                                    print(f"\\n⚡ MULTI-BURST VOLLEY RESOLVED: [{fire_count}x {keyword.upper()}]")
                                    volley_id = str(int(time.time() * 100))[-3:]
                                    
                                    def dispatch_burst(total_shots, kw, info, callback, widget, group_id):
                                        for shot in range(total_shots):
                                            if shot == 0 and kw not in self.audio_manager.active_sounds:
                                                unique_kw_id = kw
                                            else:
                                                unique_kw_id = f"{kw} #{shot + 1}-{group_id}"
                                            
                                            self.audio_manager.play(
                                                keyword=unique_kw_id,
                                                file_info=info,
                                                on_ui_refresh_callback=callback,
                                                root_window_widget=widget
                                            )
                                            time.sleep(random.uniform(0.15, 0.45))
                                            
                                    threading.Thread(
                                        target=dispatch_burst, 
                                        args=(fire_count, keyword, file_info, self.on_keyword_triggered_callback, self.root_window_widget, volley_id),
                                        daemon=True
                                    ).start()
                                else:
                                    self.audio_manager.play(
                                        keyword=keyword,
                                        file_info=file_info,
                                        on_ui_refresh_callback=self.on_keyword_triggered_callback,
                                        root_window_widget=self.root_window_widget
                                    )
                                
                    if len(audio_buffer) >= self.sample_rate * 4:
                        audio_buffer = np.zeros(0, dtype=np.float32)
                        
            except Exception as e:
                print(f"\\n[CRITICAL-THREAD-FATAL] Unexpected error inside run_loop: {e}")
                break
