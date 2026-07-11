import os
import sys
import tkinter as tk
from tkinter import ttk

# Import our customized sub-modules from the package loop
from dm_mixer.utils import ensure_environment, load_keywords
from dm_mixer.audio import AudioManager
from dm_mixer.speech import TranscriptionEngine
from dm_mixer.studio import SoundbankStudioController

class DMSoundApplication:
    def __init__(self):
        # 1. Initialize data folders and structural subsystems
        ensure_environment()
        self.audio_manager = AudioManager()
        
        # 2. Instantiate the Whisper thread engine with explicit cross-callbacks
        self.speech_engine = TranscriptionEngine(
            audio_manager=self.audio_manager,
            on_keyword_triggered_callback=self.trigger_ui_refresh
        )
        
        # 3. Assemble root window parameters
        self.root = tk.Tk()
        self.root.title("DM Sound Dashboard v2.0")
        self.root.geometry("580x540")
        self.root.configure(bg="#1e1e1e")
        
        # Configure global themed visual style settings
        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        self.style.configure("TNotebook.Tab", background="#2d2d2d", fg="#ffffff", font=("Arial", 10, "bold"), padding=8)
        self.style.map("TNotebook.Tab", background=[("selected", "#ffcc00")], foreground=[("selected", "#1e1e1e")])
        self.style.configure("Horizontal.TProgressbar", thickness=6, troughcolor="#1e1e1e", background="#ffcc00")
        
        # Place the Treeview styles cleanly inside the master app thread scope
        self.style.configure("Treeview", background="#2d2d2d", fieldbackground="#2d2d2d", foreground="#ffffff", rowheight=24)
        self.style.configure("Treeview.Heading", background="#444444", foreground="#ffffff", font=("Arial", 9, "bold"))

        # 4. Construct Notebook Tab Navigation Frame Container Elements
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.tab_mixer = tk.Frame(self.notebook, bg="#1e1e1e")
        self.tab_studio = tk.Frame(self.notebook, bg="#1e1e1e")
        
        # FIXED: Cleaned URL encoded text labels here
        self.notebook.add(self.tab_mixer, text=" 🎛️ Live Scene Mixer ")
        self.notebook.add(self.tab_studio, text=" 🎵 Soundbank Studio ")
        
        # 5. Build Sub-Panels
        self.build_live_mixer_tab()
        
        # Inject Tab 2 Studio Controller module, passing a configuration hot-reload callback hook
        self.studio_panel = SoundbankStudioController(
            parent=self.tab_studio,
            on_config_changed_callback=self.sync_keyword_bank
        )
        
        # FIX: Pack the studio panel so it stretches to fill the tab window space!
        self.studio_panel.pack(fill="both", expand=True)
        
        # Force the Studio panel table grid to draw itself immediately upon application launch
        self.studio_panel.update_library_inventory_gui()
        
        # Initial run configuration sync load
        self.sync_keyword_bank()

    def build_live_mixer_tab(self):
        """Assembles Tab 1 user mixing interfaces."""
        self.status_label = tk.Label(self.tab_mixer, text="System: Idle", fg="#aaaaaa", bg="#1e1e1e", font=("Arial", 12))
        self.status_label.pack(pady=10)
        
        self.action_btn = tk.Button(self.tab_mixer, text="Set the Scene", font=("Arial", 14, "bold"), 
                                    bg="#5cb85c", fg="white", activebackground="#4cae4c", 
                                    command=self.toggle_scene_state, relief="flat", padx=10, pady=5)
        self.action_btn.pack(pady=5)
        
        # Master group relative mixer fader frame
        master_frame = tk.Frame(self.tab_mixer, bg="#1e1e1e", pady=5)
        master_frame.pack(fill="x", padx=20)
        
        tk.Label(master_frame, text="🎛️ MASTER VOLUME", fg="#ffcc00", bg="#1e1e1e", font=("Arial", 10, "bold"), width=15, anchor="w").pack(side="left")
        
        self.master_slider = tk.Scale(
            master_frame, from_=0, to=100, orient="horizontal", showvalue=True,
            bg="#1e1e1e", fg="#ffffff", highlightthickness=0, troughcolor="#2d2d2d", activebackground="#ffcc00",
            command=self.audio_manager.update_master_volume
        )
        self.master_slider.set(100)
        self.master_slider.pack(side="left", fill="x", expand=True, padx=10)
        
        tk.Label(self.tab_mixer, text="Active Channels (Mix Ratio | Live Output):", fg="#ffffff", bg="#1e1e1e", font=("Arial", 10, "bold")).pack(pady=(15, 2), anchor="w", padx=20)
        
        self.list_frame = tk.Frame(self.tab_mixer, bg="#2d2d2d", bd=1, relief="solid")
        self.list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        self.redraw_mixer_channels()

    def toggle_scene_state(self):
        """Manages the toggle tracking transitions of the microphone loop thread stream."""
        if not self.speech_engine.is_running:
            self.speech_engine.start(active_keyword_map=self.current_keywords)
            self.action_btn.config(text="End Scene", bg="#d9534f", fg="white")
            self.status_label.config(text=" Listening...", fg="#5cb85c")
        else:
            self.action_btn.config(text="Set the Scene", bg="#5cb85c", fg="white")
            self.status_label.config(text="System: Idle (Audio Fading & Saving)", fg="#aaaaaa")
            
            # Hand over the frame display re-draw function directly
            self.speech_engine.stop(save_history_callback=self.redraw_mixer_channels)

    def sync_keyword_bank(self):
        """Hot-reloads local configurations using utility layer parsers."""
        self.current_keywords = load_keywords()

    def trigger_ui_refresh(self):
        """Thread-safe UI grid display proxy loop hook."""
        self.root.after(0, self.redraw_mixer_channels)

    def redraw_mixer_channels(self):
        """Dynamically repopulates the vertical list panel rows inside Tab 1."""
        for widget in self.list_frame.winfo_children():
            widget.destroy()
            
        if not self.audio_manager.active_sounds:
            tk.Label(self.list_frame, text="No background layers active", fg="#777777", bg="#2d2d2d", font=("Arial", 10, "italic")).pack(pady=20)
            return
            
        for keyword, sound_data in self.audio_manager.active_sounds.items():
            row = tk.Frame(self.list_frame, bg="#2d2d2d", pady=6)
            row.pack(fill="x", padx=10)
            
            tk.Label(row, text=f"  {keyword.capitalize()}", fg="#ffffff", bg="#2d2d2d", font=("Arial", 11), width=12, anchor="w").pack(side="left")
            
            current_vol_percentage = int(sound_data["base_volume"] * 100)
            slider = tk.Scale(
                row, from_=0, to=100, orient="horizontal", showvalue=False,
                bg="#2d2d2d", fg="#ffffff", highlightthickness=0, troughcolor="#1e1e1e", activebackground="#5cb85c",
                command=lambda val, kw=keyword: self.audio_manager.update_individual_volume(kw, val)
            )
            slider.set(current_vol_percentage)
            slider.pack(side="left", fill="x", expand=True, padx=10)
            sound_data["slider_widget"] = slider
            
            scaled_percentage = int(sound_data["base_volume"] * self.audio_manager.master_scale * 100)
            progress_bar = ttk.Progressbar(row, orient="horizontal", length=60, mode="determinate", style="Horizontal.TProgressbar")
            progress_bar.config(maximum=100, value=scaled_percentage)
            progress_bar.pack(side="left", padx=(5, 10))
            sound_data["visual_bar_widget"] = progress_bar
            
            tk.Button(
                row, text="✕", fg="#ffffff", bg="#d9534f", activebackground="#c9302c", relief="flat", font=("Arial", 9, "bold"),
                command=lambda kw=keyword: self.audio_manager.stop_track_with_gui_sync(kw, callback=self.redraw_mixer_channels)
            ).pack(side="right", padx=(5, 0))

    def run(self):
        self.root.mainloop()
