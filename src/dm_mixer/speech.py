import queue
import sys
import threading
import traceback  # FIX: Added for deep error inspection logging
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

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
        """The core continuous evaluation pipeline. Runs inside a background thread."""
        print("[DEBUG-SPEECH-THREAD] Background evaluation thread AWAKE and checking chunks.")
        audio_buffer = np.zeros(0, dtype=np.float32)
        
        while self.is_running:
            try:
                # Non-blocking check with timeout
                try:
                    data = self.audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                
                audio_buffer = np.append(audio_buffer, data.ravel())
                
                # Check data block size bounds
                if len(audio_buffer) >= self.sample_rate * 2:
                    # Deep wrapper safety check directly around the Whisper engine parser
                    try:
                        segments, _ = self.model.transcribe(
                            audio_buffer, 
                            beam_size=3, 
                            vad_filter=True, 
                            language="en"
                        )
                        
                        # Forces execution out of generator state so we capture the true loop pass
                        segments = list(segments)
                    except Exception as e:
                        print(f"\n[ERROR-WHISPER-CORE] Model transcription crash: {e}")
                        traceback.print_exc()
                        continue
                    
                    for segment in segments:
                        clean_text = segment.text.lower().replace(".", "").replace(",", "").strip()
                        print(f"\r💬 Hearing description: {segment.text.strip()}", end="", flush=True)
                        
                        for keyword, file_info in self.keyword_mapping.items():
                            if keyword in clean_text:
                                try:
                                    self.audio_manager.play(
                                        keyword=keyword,
                                        file_info=file_info,
                                        on_ui_refresh_callback=self.on_keyword_triggered_callback,
                                        root_window_widget=self.root_window_widget
                                    )
                                except Exception as e:
                                    print(f"\n[ERROR-PLAYBACK-BRIDGE] Failed passing event to audio manager: {e}")
                                    traceback.print_exc()
                                
                    if len(audio_buffer) >= self.sample_rate * 4:
                        audio_buffer = np.zeros(0, dtype=np.float32)
                        
            except Exception as e:
                print(f"\n[CRITICAL-THREAD-FATAL] Unexpected error inside run_loop: {e}")
                traceback.print_exc()
                break
                
        print("\n[DEBUG-SPEECH-THREAD] Background evaluation thread has TERMINATED.")
