from faster_whisper import WhisperModel

# Choose your model size: "tiny", "base", "small", "medium", or "large-v3"
model_size = "small"

# Configure device: Use "cuda" for GPU or "cpu" for CPU execution
# Configure compute_type: Use "float16" for GPU, "int8" for CPU optimization
model = WhisperModel(model_size, device="cuda", compute_type="float16")

# Start transcription
print("Transcribing audio file...")
segments, info = model.transcribe("audio.mp3", beam_size=5)

print(f"Detected language '{info.language}' with probability {info.language_probability:.2f}")

# Print the segments with timestamps
for segment in segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")