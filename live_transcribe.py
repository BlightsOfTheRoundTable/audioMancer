import queue
import sys
import os
import json
import shutil
import threading
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

# Suppress pygame welcome
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# =====================================================================
# 1. ENVIRONMENT WORKSPACE & DATA PATH ARCHITECTURE
# =====================================================================
# Set up a permanent user profile folder outside the project directory
USER_DATA_DIR = os.path.join(os.path.expanduser("~"), ".dm_sound_mixer")
USER_SOUNDS_DIR = os.path.join(USER_DATA_DIR, "custom_sounds")
CONFIG_FILE = os.path.join(USER_DATA_DIR, "config.json")
HISTORY_FILE = os.path.join(USER_DATA_DIR, "volume_history.json")

# Ensure the baseline directories exist immediately on boot
os.makedirs(USER_SOUNDS_DIR, exist_ok=True)

# Generate a default configuration file if a fresh user initializes the program
if not os.path.exists(CONFIG_FILE):
    default_config = {
        "example_placeholder.mp3": ["snake", "alert"]
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(default_config, f, indent=2)

KEYWORD_MAPPING = {}
volume_history = {}

def reload_sound_configuration():
    """Reads the configuration file and identifies looping vs one-shot keywords."""
    global KEYWORD_MAPPING
    KEYWORD_MAPPING.clear()
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                raw_data = json.load(f)
                for file_path, keywords in raw_data.items():
                    if isinstance(keywords, list):
                        for kw in keywords:
                            kw_clean = kw.lower().strip()
                            # Check if it's a one-shot effect
                            is_one_shot = kw_clean.startswith("!")
                            search_term = kw_clean.lstrip("!") # Strip the '!' for voice matching
                            
                            KEYWORD_MAPPING[search_term] = {
                                "file_path": file_path,
                                "one_shot": is_one_shot
                            }
                    else:
                        kw_clean = keywords.lower().strip()
                        is_one_shot = kw_clean.startswith("!")
                        search_term = kw_clean.lstrip("!")
                        KEYWORD_MAPPING[search_term] = {
                            "file_path": file_path,
                            "one_shot": is_one_shot
                        }
        print(f"🔄 Soundbank reloaded. {len(KEYWORD_MAPPING)} keywords armed.")
    except Exception as e:
        print(f"⚠️ Error hot-reloading configuration mapping: {e}")


# Initial run configuration seed
reload_sound_configuration()

# Load Volume History Safely
if os.path.exists(HISTORY_FILE):
    try:
        if os.path.getsize(HISTORY_FILE) > 0:
            with open(HISTORY_FILE, "r") as f:
                volume_history = json.load(f)
            print(f"💾 Loaded volume history for {len(volume_history)} keywords.")
        else:
            volume_history = {}
    except Exception as e:
        print(f"⚠️ Could not parse volume history: {e}")

# Initialize audio mixer
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
pygame.mixer.set_num_channels(16)

active_sounds = {}
master_volume_scale = 1.0
# =====================================================================
# 2. AUDIO PLAYBACK & MIXING LOGIC WITH MEMORY
# =====================================================================
def save_volume_history():
    global active_sounds, volume_history
    for keyword, sound_data in active_sounds.items():
        volume_history[keyword] = sound_data["base_volume"]
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(volume_history, f, indent=2)
        print("\n💾 Volume history file updated successfully.")
    except Exception as e:
        print(f"\n❌ Error writing volume history: {e}")

def play_sound(keyword, file_info):
    """Plays audio file. Loops atmospheres indefinitely; plays effects exactly once."""
    global active_sounds, volume_history, master_volume_scale
    file_path = file_info["file_path"]
    is_one_shot = file_info["one_shot"]
    
    if keyword in active_sounds:
        return  # Already tracking/playing

    if os.path.exists(file_path):
        try:
            sound = pygame.mixer.Sound(file_path)
            channel = pygame.mixer.find_channel()
            if channel:
                target_base_volume = volume_history.get(keyword, 0.5)
                channel.set_volume(target_base_volume * master_volume_scale)
                
                if is_one_shot:
                    # loops=0 plays the sound effect exactly once
                    channel.play(sound, loops=0)
                    print(f"\n💥 ONE-SHOT EFFECT TRIGGERED: '{keyword}'")
                else:
                    # loops=-1 loops background ambient track infinitely
                    channel.play(sound, loops=-1)
                    active_sounds[keyword] = {
                        "channel": channel, 
                        "sound": sound, 
                        "base_volume": target_base_volume,
                        "slider_widget": None,
                        "visual_bar_widget": None
                    }
                    root.after(0, update_keyword_list_gui)
            else:
                print("\n⚠️ No free audio channels available!")
        except Exception as e:
            print(f"\n❌ Error playing {file_path}: {e}")
    else:
        print(f"\n⚠️ Missing sound file: {file_path}")


def update_individual_volume(keyword, val):
    global active_sounds, master_volume_scale
    if keyword in active_sounds:
        base_vol_float = float(val) / 100.0
        active_sounds[keyword]["base_volume"] = base_vol_float
        active_sounds[keyword]["channel"].set_volume(base_vol_float * master_volume_scale)
        if active_sounds[keyword]["visual_bar_widget"]:
            scaled_percentage = int(base_vol_float * master_volume_scale * 100)
            active_sounds[keyword]["visual_bar_widget"].config(value=scaled_percentage)

def update_master_volume(val):
    global active_sounds, master_volume_scale
    master_volume_scale = float(val) / 100.0
    for keyword, sound_data in active_sounds.items():
        scaled_vol = sound_data["base_volume"] * master_volume_scale
        sound_data["channel"].set_volume(scaled_vol)
        if sound_data["visual_bar_widget"]:
            sound_data["visual_bar_widget"].config(value=int(scaled_vol * 100))

def stop_single_sound(keyword, fade_ms=1000):
    """Fades out and stops a single sound layer, clearing all tracking data so it can re-trigger immediately."""
    global active_sounds, volume_history
    if keyword in active_sounds:
        # 1. Save the track's volume to history before closing it
        volume_history[keyword] = active_sounds[keyword]["base_volume"]
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(volume_history, f, indent=2)
        except Exception as e:
            print(f"❌ Error updating track history: {e}")

        # 2. Fade out and stop the mixer channel
        sound_data = active_sounds[keyword]
        channel = sound_data["channel"]
        if channel.get_busy():
            channel.fadeout(fade_ms)
            
        # 3. BUG FIX: Remove from active memory dictionary completely
        del active_sounds[keyword]
        
        # 4. Refresh the GUI layout to reflect the change
        update_keyword_list_gui()

def stop_all_sounds_with_fade(fade_ms=3000):
    global active_sounds
    save_volume_history()
    for keyword, data in list(active_sounds.items()):
        channel = data["channel"]
        if channel.get_busy():
            channel.fadeout(fade_ms)
    active_sounds.clear()
    update_keyword_list_gui()

# =====================================================================
# 3. WHISPER PROCESSING ENGINE
# =====================================================================
MODEL_SIZE = "base"
SAMPLE_RATE = 16000
BLOCK_SIZE = 4000

print("⏳ Loading Whisper model into memory... (This takes a moment)")
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")

class TranscriptionEngine:
    def __init__(self):
        self.is_running = False
        self.audio_queue = queue.Queue()
        self.stream = None

    def audio_callback(self, indata, frames, time, status):
        if status: print(status, file=sys.stderr)
        self.audio_queue.put(indata.copy())

    def start(self):
        self.is_running = True
        self.audio_queue = queue.Queue()
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, 
            callback=self.audio_callback, blocksize=BLOCK_SIZE, dtype="float32"
        )
        self.stream.start()
        threading.Thread(target=self.loop, daemon=True).start()

    def stop(self):
        self.is_running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        stop_all_sounds_with_fade(fade_ms=3000)

    def loop(self):
        audio_buffer = np.zeros(0, dtype=np.float32)
        while self.is_running:
            try:
                data = self.audio_queue.get(timeout=0.5)
                audio_buffer = np.append(audio_buffer, data.ravel())
                if len(audio_buffer) >= SAMPLE_RATE * 2:
                    segments, _ = model.transcribe(audio_buffer, beam_size=3, vad_filter=True, language="en")
                    for segment in segments:
                        clean_text = segment.text.lower().replace(".","").replace(",","").strip()
                        print(f"\r💬 Hearing description: {segment.text.strip()}", end="", flush=True)
                        for keyword, file_info in KEYWORD_MAPPING.items():
                            if keyword in clean_text:
                                if keyword not in active_sounds:
                                    print(f"\n🎯 ATMOSPHERE DETECTED: '{keyword}'")
                                    play_sound(keyword, file_info)
                    if len(audio_buffer) >= SAMPLE_RATE * 4:
                        audio_buffer = np.zeros(0, dtype=np.float32)
            except queue.Empty:
                continue

engine = TranscriptionEngine()

from tkinter import ttk
# =====================================================================
# 4. DM INTEGRATED SOUND ENGINEER GUI PANEL
# =====================================================================
root = tk.Tk()
root.title("DM Sound Dashboard v1.2")
root.geometry("580x600")
root.configure(bg="#1e1e1e")

# Style configuration for flat custom progress layout
style = ttk.Style()
style.theme_use('default')
style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
style.configure("TNotebook.Tab", background="#2d2d2d", fg="#ffffff", font=("Arial", 10, "bold"), padding=8)
style.map("TNotebook.Tab", background=[("selected", "#ffcc00")], foreground=[("selected", "#1e1e1e")])
style.configure("Horizontal.TProgressbar", thickness=6, troughcolor="#1e1e1e", background="#ffcc00")

# Instantiate a Master Notebook Component to hold separate tabs
notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True, padx=5, pady=5)

tab_mixer = tk.Frame(notebook, bg="#1e1e1e")
tab_studio = tk.Frame(notebook, bg="#1e1e1e")

notebook.add(tab_mixer, text=" 🎛️ Live Scene Mixer ")
notebook.add(tab_studio, text=" 🎵 Soundbank Studio ")

# --- TAB 1 CORE LOGIC ASSEMBLY (THE MIXER PANEL) ---
status_label = tk.Label(tab_mixer, text="System: Idle", fg="#aaaaaa", bg="#1e1e1e", font=("Arial", 12))
status_label.pack(pady=10)

def toggle_listening():
    if not engine.is_running:
        engine.start()
        btn.config(text="End Scene", bg="#d9534f", fg="white")
        status_label.config(text=" Listening...", fg="#5cb85c")
    else:
        engine.stop()
        btn.config(text="Set the Scene", bg="#5cb85c", fg="white")
        status_label.config(text="System: Idle (Audio Fading & Saving)", fg="#aaaaaa")

btn = tk.Button(tab_mixer, text="Set the Scene", font=("Arial", 14, "bold"), 
                bg="#5cb85c", fg="white", activebackground="#4cae4c", 
                command=toggle_listening, relief="flat", padx=10, pady=5)
btn.pack(pady=5)

master_frame = tk.Frame(tab_mixer, bg="#1e1e1e", pady=5)
master_frame.pack(fill="x", padx=20)

master_lbl = tk.Label(master_frame, text="🎛️ MASTER VOLUME", fg="#ffcc00", bg="#1e1e1e", font=("Arial", 10, "bold"), width=15, anchor="w")
master_lbl.pack(side="left")

master_slider = tk.Scale(
    master_frame, from_=0, to=100, orient="horizontal", showvalue=True,
    bg="#1e1e1e", fg="#ffffff", highlightthickness=0, troughcolor="#2d2d2d", activebackground="#ffcc00",
    command=update_master_volume
)
master_slider.set(100)
master_slider.pack(side="left", fill="x", expand=True, padx=10)

list_header = tk.Label(tab_mixer, text="Active Channels (Mix Ratio | Live Output):", fg="#ffffff", bg="#1e1e1e", font=("Arial", 10, "bold"))
list_header.pack(pady=(15, 2), anchor="w", padx=20)

list_frame = tk.Frame(tab_mixer, bg="#2d2d2d", bd=1, relief="solid")
list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

def update_keyword_list_gui():
    global master_volume_scale
    for widget in list_frame.winfo_children():
        widget.destroy()
    if not active_sounds:
        empty_lbl = tk.Label(list_frame, text="No background layers active", fg="#777777", bg="#2d2d2d", font=("Arial", 10, "italic"))
        empty_lbl.pack(pady=20)
        return
    for keyword, sound_data in active_sounds.items():
        row = tk.Frame(list_frame, bg="#2d2d2d", pady=6)
        row.pack(fill="x", padx=10)
        
        lbl = tk.Label(row, text=f"  {keyword.capitalize()}", fg="#ffffff", bg="#2d2d2d", font=("Arial", 11), width=12, anchor="w")
        lbl.pack(side="left")
        
        current_vol_percentage = int(sound_data["base_volume"] * 100)
        slider = tk.Scale(
            row, from_=0, to=100, orient="horizontal", showvalue=False,
            bg="#2d2d2d", fg="#ffffff", highlightthickness=0, troughcolor="#1e1e1e", activebackground="#5cb85c",
            command=lambda val, kw=keyword: update_individual_volume(kw, val)
        )
        slider.set(current_vol_percentage)
        slider.pack(side="left", fill="x", expand=True, padx=10)
        sound_data["slider_widget"] = slider
        
        scaled_percentage = int(sound_data["base_volume"] * master_volume_scale * 100)
        progress_bar = ttk.Progressbar(row, orient="horizontal", length=60, mode="determinate", style="Horizontal.TProgressbar")
        progress_bar.config(maximum=100, value=scaled_percentage)
        progress_bar.pack(side="left", padx=(5, 10))
        sound_data["visual_bar_widget"] = progress_bar
        
        kill_btn = tk.Button(
            row, text="✕", fg="#ffffff", bg="#d9534f", activebackground="#c9302c", 
            relief="flat", font=("Arial", 9, "bold"),
            command=lambda kw=keyword: stop_single_sound(kw)
        )
        kill_btn.pack(side="right", padx=(5, 0))
# --- TAB 2 LOGIC ASSEMBLY (THE SOUNDBANK STUDIO WITH GRID INVENTORY TABLE) ---
class SoundbankStudioController:
    def __init__(self, frame):
        self.frame = frame
        self.selected_file_path = None
        
        # 1. IMPORT CONTAINER BLOCK
        title = tk.Label(frame, text="⚙️ IMPORT CUSTOM ATMOSPHERE ASSETS", fg="#ffcc00", bg="#1e1e1e", font=("Arial", 11, "bold"))
        title.pack(pady=(10, 5))
        
        box = tk.Frame(frame, bg="#2d2d2d", bd=1, relief="solid", pady=10, padx=15)
        box.pack(fill="x", padx=25)
        
        # Row A: Browse Selector
        row_a = tk.Frame(box, bg="#2d2d2d")
        row_a.pack(fill="x", pady=2)
        self.file_label = tk.Label(row_a, text="No Audio Track Picked...", fg="#888888", bg="#2d2d2d", width=32, anchor="w", font=("Arial", 10, "italic"))
        self.file_label.pack(side="left", padx=5)
        browse_btn = tk.Button(row_a, text="Browse Audio File", font=("Arial", 9, "bold"), bg="#ffcc00", fg="#1e1e1e", command=self.browse)
        browse_btn.pack(side="right")
        
        # Row B: Text Inputs
        tk.Label(box, text="Voice Trigger Keywords (Separate multiple with commas):", fg="#ffffff", bg="#2d2d2d", font=("Arial", 9)).pack(anchor="w", padx=5, pady=(8, 2))
        self.kw_entry = tk.Entry(box, bg="#1e1e1e", fg="#ffffff", insertbackground="white", font=("Arial", 10), bd=1, relief="solid")
        self.kw_entry.pack(fill="x", padx=5, pady=2)
        
        # Row C: Looping Checkbox Layout
        self.loop_var = tk.BooleanVar(value=False)
        checkbox_frame = tk.Frame(box, bg="#2d2d2d")
        checkbox_frame.pack(fill="x", pady=(10, 2))
        
        chk = tk.Checkbutton(
            checkbox_frame, text="Loop Background Audio (Uncheck for instant One-Shot Effects)", 
            variable=self.loop_var, onvalue=True, offvalue=False,
            bg="#2d2d2d", fg="#ffffff", selectcolor="#1e1e1e", activebackground="#2d2d2d", activeforeground="#ffffff",
            font=("Arial", 9)
        )
        chk.pack(side="left", padx=2)
        
        # Row D: Action Save Trigger
        save_btn = tk.Button(frame, text="➕ Add Sound to Campaign Library", font=("Arial", 11, "bold"), bg="#5cb85c", fg="white", activebackground="#4cae4c", relief="flat", padx=10, pady=4, command=self.save)
        save_btn.pack(pady=10)

        # 2. TABULAR INVENTORY VIEW BLOCK
        library_title = tk.Label(frame, text="📜 CAMPAIGN SOUND LIBRARY INVENTORY", fg="#ffffff", bg="#1e1e1e", font=("Arial", 10, "bold"))
        library_title.pack(anchor="w", padx=25, pady=(10, 2))

        # Core outer frame container
        self.table_container = tk.Frame(frame, bg="#1e1e1e")
        self.table_container.pack(fill="both", expand=True, padx=25, pady=(0, 15))
        
        # Style tree grid colors to match the dark DM theme palette
        style.configure("Treeview", background="#2d2d2d", fieldbackground="#2d2d2d", foreground="#ffffff", rowheight=24)
        style.configure("Treeview.Heading", background="#444444", foreground="#ffffff", font=("Arial", 9, "bold"))
        style.map("Treeview.Heading", background=[("active", "#ffcc00")], foreground=[("active", "#1e1e1e")])

        # Define grid columns
        self.tree = ttk.Treeview(self.table_container, columns=("File", "Type", "Keywords"), show="headings", selectmode="none")
        
        # Adjust table formatting spacing dimensions
        self.tree.heading("File", text=" File Name", anchor="w")
        self.tree.heading("Type", text="Playback Type", anchor="center")
        self.tree.heading("Keywords", text=" Trigger Keywords", anchor="w")
        
        self.tree.column("File", width=140, minwidth=100, anchor="w")
        self.tree.column("Type", width=90, minwidth=80, anchor="center")
        self.tree.column("Keywords", width=180, minwidth=120, anchor="w")
        
        self.tree.pack(side="left", fill="both", expand=True)

        # Right sidebar row to stack delete alignment buttons right next to the grid rows
        self.btn_sidebar = tk.Frame(self.table_container, bg="#1e1e1e")
        self.btn_sidebar.pack(side="right", fill="y", padx=(5, 0))

        # Initial data render
        self.update_library_inventory_gui()

    def browse(self):
        fp = filedialog.askopenfilename(title="Select Atmosphere Sound Track", filetypes=[("Audio Files Files", "*.mp3 *.wav *.ogg")])
        if fp:
            self.selected_file_path = fp
            self.file_label.config(text=os.path.basename(fp), fg="#5cb85c", font=("Arial", 10, "bold"))

    def save(self):
        if not self.selected_file_path or not self.kw_entry.get().strip():
            messagebox.showwarning("Incomplete Data", "Please link an audio file and supply trigger words first!")
            return
        fn = os.path.basename(self.selected_file_path)
        dest_path = os.path.join(USER_SOUNDS_DIR, fn)
        try:
            shutil.copy(self.selected_file_path, dest_path)
            raw_kws = [kw.lower().strip() for kw in self.kw_entry.get().split(",") if kw.strip()]
            
            new_kws = []
            for kw in raw_kws:
                if not self.loop_var.get():
                    new_kws.append(f"!{kw}")
                else:
                    new_kws.append(kw)
            
            current_config = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    current_config = json.load(f)
            
            current_config[dest_path] = new_kws
            with open(CONFIG_FILE, "w") as f:
                json.dump(current_config, f, indent=2)
                
            messagebox.showinfo("Success!", f"Successfully mapped '{fn}' to your database keyword registry.")
            
            self.file_label.config(text="No Audio Track Picked...", fg="#888888", font=("Arial", 10, "italic"))
            self.kw_entry.delete(0, tk.END)
            self.loop_var.set(value=False)
            self.selected_file_path = None
            
            reload_sound_configuration()
            self.update_library_inventory_gui()
        except Exception as e:
            messagebox.showerror("Save Failure", f"Could not process copy asset request:\n{e}")

    def update_library_inventory_gui(self):
        """Wipes and re-renders the clean data rows inside the matrix layout table grid."""
        # 1. Clear out old table entries
        for item in self.tree.get_children():
            self.tree.delete(item)
        for widget in self.btn_sidebar.winfo_children():
            widget.destroy()

        try:
            if not os.path.exists(CONFIG_FILE):
                return

            with open(CONFIG_FILE, "r") as f:
                current_config = json.load(f)

            filtered_config = {k: v for k, v in current_config.items() if not k.endswith("example_placeholder.mp3")}

            if not filtered_config:
                return

            # Spacer header row block for the right button column layout container alignment
            tk.Label(self.btn_sidebar, text="", bg="#1e1e1e", font=("Arial", 9, "bold"), height=1).pack()

            # 2. Iterate and append properties into structural row fields
            for path, keywords in filtered_config.items():
                filename = os.path.basename(path)
                
                # Determine loop properties cleanly once per absolute file element
                is_file_one_shot = any(kw.strip().startswith("!") for kw in keywords)
                type_tag = "💥 One-Shot" if is_file_one_shot else "🔄 Loop"
                
                # Strip helper chars from displayed keyword lists
                clean_keywords = [kw.strip().lstrip("!") for kw in keywords]
                keywords_str = ", ".join(clean_keywords)

                # Insert data record row cleanly into table columns
                self.tree.insert("", "end", values=(filename, type_tag, keywords_str))

                # Append corresponding delete button aligned perfectly to this entry row height
                del_btn = tk.Button(self.btn_sidebar, text="✕", fg="#ffffff", bg="#444444", activebackground="#d9534f",
                                    relief="flat", font=("Arial", 8, "bold"), height=1, width=3, bd=0,
                                    command=lambda p=path: self.delete_sound_asset(p))
                del_btn.pack(pady=1)

        except Exception as e:
            print(f"⚠️ Error drawing tabular library layout grid: {e}")

    def delete_sound_asset(self, target_path):
        if messagebox.askyesno("Delete Sound Asset", f"Permanently remove this audio track from your library?"):
            try:
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, "r") as f:
                        config_data = json.load(f)
                    if target_path in config_data:
                        del config_data[target_path]
                    with open(CONFIG_FILE, "w") as f:
                        json.dump(config_data, f, indent=2)

                if os.path.exists(target_path):
                    os.remove(target_path)

                reload_sound_configuration()
                self.update_library_inventory_gui()
                messagebox.showinfo("Deleted", "Sound track successfully cleared.")
            except Exception as e:
                messagebox.showerror("Purge Fault Error", f"Failed to fully delete asset properties:\n{e}")

studio_controller = SoundbankStudioController(tab_studio)

update_keyword_list_gui()
root.mainloop()

