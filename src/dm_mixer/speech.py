import queue
import sys
import threading  # FIX: Added threading import to spawn background workers
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

class TranscriptionEngine:
    def __init__(self, audio_manager, on_keyword_triggered_callback):
        """
        Coordinates the microphone stream and feeds chunks to Whisper.
        
        :param audio_manager: The active instance of our Pygame AudioManager
        :param on_keyword_triggered_callback: A function to refresh the Mixer GUI
        """
        self.audio_manager = audio_manager
        self.on_keyword_triggered_callback = on_keyword_triggered_callback
        
        self.is_running = False
        self.audio_queue = queue.Queue()
        self.stream = None
        self.keyword_mapping = {}  # Set dynamically before we boot the mic

        # Whisper Core Parameters
        self.model_size = "base"
        self.sample_rate = 16000
        self.block_size = 4000

        print("⏳ Loading Whisper model into memory... (This takes a moment)")
        self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

    def audio_callback(self, indata, frames, time, status):
        """Intercepts raw sound blocks from the system's microphone device."""
        if status:
            print(status, file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def start(self, active_keyword_map):
        """Arms the engine with the latest config words and fires up the mic thread."""
        self.keyword_mapping = active_keyword_map
        self.is_running = True
        self.audio_queue = queue.Queue()
        
        self.stream = sd.InputStream(
            samplerate=self.sample_rate, 
            channels=1, 
            callback=self.audio_callback, 
            blocksize=self.block_size, 
            dtype="float32"
        )
        self.stream.start()

        # FIX: Launch the continuous transcription evaluation loop on a background thread
        # This keeps the microphone checking audio chunks without freezing the main Tkinter window
        threading.Thread(target=self.run_loop, daemon=True).start()

    def stop(self, save_history_callback):
        """Safely tears down the mic stream and fades out the soundscapes."""
        self.is_running = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print(f"⚠️ Error closing microphone stream: {e}")
        
        # FIX: Pass save_history_callback explicitly as a named keyword argument
        # This prevents argument shifting from scrambling it into the fade_ms variable
        self.audio_manager.stop_all_sounds_with_fade(
            save_history_callback=save_history_callback
        )


    def run_loop(self):
        """The core continuous evaluation pipeline. Runs inside a background thread."""
        audio_buffer = np.zeros(0, dtype=np.float32)
        
        while self.is_running:
            try:
                # Pull raw microphone arrays from the queue with a non-blocking timeout
                data = self.audio_queue.get(timeout=0.5)
                audio_buffer = np.append(audio_buffer, data.ravel())
                
                # Evaluate chunks in roughly 2-second narrative slices
                if len(audio_buffer) >= self.sample_rate * 2:
                    segments, _ = self.model.transcribe(
                        audio_buffer, 
                        beam_size=3, 
                        vad_filter=True, 
                        language="en"
                    )
                    
                    for segment in segments:
                        clean_text = segment.text.lower().replace(".", "").replace(",", "").strip()
                        print(f"\r💬 Hearing description: {segment.text.strip()}", end="", flush=True)
                        
                        # Look for campaign sound dictionary matches
                        for keyword, file_info in self.keyword_mapping.items():
                            if keyword in clean_text:
                                # Delegate play handling to the core audio.py manager module
                                self.audio_manager.play(
                                    keyword=keyword,
                                    file_info=file_info,
                                    on_ui_refresh_callback=self.on_keyword_triggered_callback
                                )
                                
                    # Prevent buffer overflows and old sentence echo overlapping loops
                    if len(audio_buffer) >= self.sample_rate * 4:
                        audio_buffer = np.zeros(0, dtype=np.float32)
                        
            except queue.Empty:
                continue
