import queue
import re
import sys
import threading
import traceback
import random
import time
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from dm_mixer import context_analysis


def _describe_trigger(keyword, fire_count=1, periodic_seconds=None, volume_multiplier=1.0, volume_modifier_word=None):
    """Builds a human-readable console description of exactly why/how a trigger fired, e.g.
    "'explosion' (faint -> quieter, 0.40x)" vs plain "'explosion'" - so it's obvious from the
    terminal alone which circumstance caused which sound, not just that something fired."""
    parts = [f"'{keyword}'"]
    if periodic_seconds is not None:
        parts.append(f"(recurring every {int(periodic_seconds)}s)")
    if fire_count > 1:
        parts.append(f"x{fire_count}")
    if volume_modifier_word:
        direction = "quieter" if volume_multiplier < 1.0 else "louder"
        parts.append(f"({volume_modifier_word} -> {direction}, {volume_multiplier:.2f}x)")
    return " ".join(parts)


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

        # How long to let audio accumulate before transcribing/acting on it. Widened from an
        # original 2.0s/4.0s pair to give trailing verbal modifiers ("...off in the distance")
        # more time to actually be spoken before the engine commits to firing on a bare keyword
        # mention - a quick mitigation for keywords firing before a qualifier that follows them
        # has even been said yet. Trades a bit of added latency on every trigger for that.
        self.min_buffer_seconds = 3.5
        self.buffer_reset_seconds = 7.0

        print("[LOADING] Loading Whisper model into memory... (This takes a moment)")
        self.model = WhisperModel("base", device="cpu", compute_type="int8")

        print("[LOADING] Loading spaCy language model for context analysis...")
        context_analysis.get_nlp()

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[MIC-STATUS] {status}", file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def start(self, active_keyword_map, root_window_widget):
        """Arms the engine and starts the microphone stream. Returns True on success,
        False if the microphone couldn't be opened (no device, in use, permission denied)."""
        self.keyword_mapping = active_keyword_map
        self.root_window_widget = root_window_widget
        self.audio_queue = queue.Queue()

        try:
            device_info = sd.query_devices(kind='input')
            self.hardware_sample_rate = int(device_info['default_sample_rate'])
        except Exception:
            self.hardware_sample_rate = 16000

        try:
            # Since Pygame lives in a separate sandbox process, this opens flawlessly
            self.stream = sd.InputStream(
                samplerate=self.hardware_sample_rate,
                channels=1,
                callback=self.audio_callback,
                blocksize=self.block_size,
                dtype="float32"
            )
            self.stream.start()
        except Exception as e:
            print(f"[CRITICAL-ERROR-SPEECH] Failed to open microphone stream: {e}", file=sys.stderr)
            self.stream = None
            self.is_running = False
            return False

        print("[DEBUG-SPEECH] Microphone device stream successfully acquired.")
        self.is_running = True
        threading.Thread(target=self.run_loop, daemon=True).start()
        return True

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
                
                if len(audio_buffer) >= self.whisper_target_rate * self.min_buffer_seconds:
                    try:
                        segments, _ = self.model.transcribe(audio_buffer, beam_size=3, vad_filter=True, language="en")
                        segments = list(segments)
                    except Exception: continue
                    
                    for segment in segments:
                        clean_text = segment.text.lower().strip()
                        print(f"\rHearing description: {segment.text.strip()}", end="", flush=True)

                        # One spaCy parse per chunk, reused for every keyword checked against it -
                        # much cheaper than re-parsing the same sentence once per keyword.
                        doc = context_analysis.parse_chunk(clean_text)

                        for keyword, file_info in self.keyword_mapping.items():
                            try:
                                # Left boundary only (not \bkeyword\b): stops "rat" firing inside
                                # "narrate", while still matching spoken plurals/suffixes like
                                # "arrow" inside "arrows" or "goblin" inside "goblins".
                                # finditer (not search): a single transcribed chunk can legitimately
                                # contain more than one distinct mention of the same keyword (e.g.
                                # "two explosions ... and then two more explosions") - matching only
                                # the first would silently drop every later mention.
                                matches = list(re.finditer(r'\b' + re.escape(keyword), clean_text))
                                if not matches:
                                    continue

                                # Periodic recurrence ("every N seconds", "once in a while", ...) is
                                # sentence-wide, not tied to a specific mention, and is checked BEFORE
                                # the cooldown gate below - deliberately not gated by phrase_cooldowns
                                # at all. The audio buffer gets re-transcribed in full every ~0.1-0.25s
                                # while it's between min_buffer_seconds and buffer_reset_seconds long,
                                # so when the recurrence modifier is spoken AFTER the keyword ("you
                                # hear thunder every 2 seconds"), an earlier partial pass can match the
                                # bare keyword alone (before "every 2 seconds" has even been spoken),
                                # fire it as a one-shot, and lock the cooldown - which used to
                                # permanently swallow the later, fuller pass's periodic detection for
                                # the rest of that cooldown window. AudioManager.play() already dedupes
                                # repeat periodic starts via the unique "keyword @ every Ns" key, so
                                # this path doesn't need the cooldown's protection the way the
                                # one-shot/burst path below does.
                                first_cues = context_analysis.analyze_occurrence(doc, matches[0].start(), matches[0].end())
                                if first_cues.periodic_seconds is not None:
                                    unique_periodic_key = f"{keyword} @ every {int(first_cues.periodic_seconds)}s"
                                    played = self.audio_manager.play(
                                        keyword=unique_periodic_key, file_info=file_info,
                                        on_ui_refresh_callback=self.on_keyword_triggered_callback,
                                        root_window_widget=self.root_window_widget, periodic_interval=first_cues.periodic_seconds,
                                        context_volume_multiplier=first_cues.volume_multiplier,
                                        context_modifier_word=first_cues.volume_modifier_word,
                                    )
                                    if played:
                                        print(f"\nTriggering {_describe_trigger(keyword, periodic_seconds=first_cues.periodic_seconds, volume_multiplier=first_cues.volume_multiplier, volume_modifier_word=first_cues.volume_modifier_word)}")
                                    else:
                                        print(f"\nSkipped '{keyword}' (every {int(first_cues.periodic_seconds)}s) - already active")
                                    continue

                                if keyword in phrase_cooldowns and current_timestamp - phrase_cooldowns[keyword] < 4.0:
                                    continue

                                phrase_cooldowns[keyword] = current_timestamp

                                # Sum quantities across every distinct mention of this keyword in
                                # the chunk, so "two explosions ... two more explosions" fires 4
                                # total rather than only counting the first mention. The volume
                                # reading with the largest deviation from neutral (1.0) across all
                                # mentions applies to the whole burst/play.
                                fire_count = 0
                                volume_multiplier = 1.0
                                volume_modifier_word = None
                                strongest_deviation = 0.0
                                for occurrence in matches:
                                    cues = context_analysis.analyze_occurrence(doc, occurrence.start(), occurrence.end())
                                    fire_count += cues.fire_count
                                    deviation = abs(cues.volume_multiplier - 1.0)
                                    if deviation > strongest_deviation:
                                        strongest_deviation = deviation
                                        volume_multiplier = cues.volume_multiplier
                                        volume_modifier_word = cues.volume_modifier_word
                                fire_count = min(15, fire_count)

                                if file_info["one_shot"] and fire_count > 1:
                                    print(f"\nTriggering {_describe_trigger(keyword, fire_count=fire_count, volume_multiplier=volume_multiplier, volume_modifier_word=volume_modifier_word)}")
                                    volley_id = str(int(time.time() * 100))[-3:]

                                    def dispatch_burst(total_shots, kw, info, callback, widget, group_id, vol_multiplier, vol_word):
                                        for shot in range(total_shots):
                                            unique_kw_id = f"{kw} #{shot + 1}-{group_id}" if shot > 0 or kw in self.audio_manager.active_sounds else kw
                                            played = self.audio_manager.play(
                                                keyword=unique_kw_id, file_info=info,
                                                on_ui_refresh_callback=callback, root_window_widget=widget,
                                                context_volume_multiplier=vol_multiplier,
                                                context_modifier_word=vol_word,
                                            )
                                            if not played:
                                                print(f"\nSkipped burst shot {shot + 1}/{total_shots} for '{kw}' - already active")
                                            time.sleep(random.uniform(0.15, 0.45))

                                    threading.Thread(
                                        target=dispatch_burst,
                                        args=(fire_count, keyword, file_info, self.on_keyword_triggered_callback, self.root_window_widget, volley_id, volume_multiplier, volume_modifier_word),
                                        daemon=True
                                    ).start()
                                else:
                                    played = self.audio_manager.play(
                                        keyword=keyword, file_info=file_info,
                                        on_ui_refresh_callback=self.on_keyword_triggered_callback,
                                        root_window_widget=self.root_window_widget,
                                        context_volume_multiplier=volume_multiplier,
                                        context_modifier_word=volume_modifier_word,
                                    )
                                    if played:
                                        print(f"\nTriggering {_describe_trigger(keyword, volume_multiplier=volume_multiplier, volume_modifier_word=volume_modifier_word)}")
                                    else:
                                        print(f"\nSkipped '{keyword}' - already active/playing")
                            except Exception:
                                # Never let one keyword's analysis take down the whole listening
                                # session - log it and keep checking the REST of the keywords
                                # against this same chunk.
                                print(f"\n[ERROR-CONTEXT-ANALYSIS] Failed processing keyword {keyword!r}:", file=sys.stderr)
                                traceback.print_exc()
                                continue

                    if len(audio_buffer) >= self.whisper_target_rate * self.buffer_reset_seconds:
                        audio_buffer = np.zeros(0, dtype=np.float32)
            except Exception:
                # A truly unexpected error must not silently end the whole listening session -
                # log it so it's diagnosable, and keep the mic stream alive for the next chunk
                # rather than dying with zero indication of why keywords stopped firing.
                print("\n[CRITICAL-ERROR-SPEECH] Unexpected error inside run_loop:", file=sys.stderr)
                traceback.print_exc()
                continue

