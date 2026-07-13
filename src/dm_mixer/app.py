import os
import sys
import time
import tkinter as tk
from tkinter import ttk

from dm_mixer.utils import ensure_environment, load_keywords, CONFIG_FILE
from dm_mixer.audio import AudioManager
from dm_mixer.speech import TranscriptionEngine
from dm_mixer.studio import SoundbankStudioController

class DMSoundApplication:
    def __init__(self):
        ensure_environment()
        self.audio_manager = AudioManager()
        
        self.speech_engine = TranscriptionEngine(
            audio_manager=self.audio_manager,
            on_keyword_triggered_callback=self.trigger_ui_refresh
        )
        
        self.root = tk.Tk()
        self.root.title("DM Sound Dashboard v2.1")
        self.root.geometry("620x540") # Slightly widened to comfortably mount the clock wheels
        self.root.configure(bg="#1e1e1e")
        
        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        self.style.configure("TNotebook.Tab", background="#2d2d2d", fg="#ffffff", font=("Arial", 10, "bold"), padding=8)
        self.style.map("TNotebook.Tab", background=[("selected", "#ffcc00")], foreground=[("selected", "#1e1e1e")])
        self.style.configure("Horizontal.TProgressbar", thickness=6, troughcolor="#1e1e1e", background="#ffcc00")
        
        self.style.configure("Treeview", background="#2d2d2d", fieldbackground="#2d2d2d", foreground="#ffffff", rowheight=24)
        self.style.configure("Treeview.Heading", background="#444444", foreground="#ffffff", font=("Arial", 9, "bold"))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.tab_mixer = tk.Frame(self.notebook, bg="#1e1e1e")
        self.tab_studio = tk.Frame(self.notebook, bg="#1e1e1e")
        
        self.notebook.add(self.tab_mixer, text=" 🎛️ Live Scene Mixer ")
        self.notebook.add(self.tab_studio, text=" 🎵 Soundbank Studio ")
        
        self.build_live_mixer_tab()
        
        self.studio_panel = SoundbankStudioController(
            parent=self.tab_studio,
            on_config_changed_callback=self.sync_keyword_bank
        )
        self.studio_panel.pack(fill="both", expand=True)
        self.studio_panel.update_library_inventory_gui()
        
        self.sync_keyword_bank()
        
        # Start the background UI visual update scheduler engine loop immediately
        self.animate_progress_clocks()

    def build_live_mixer_tab(self):
        self.status_label = tk.Label(self.tab_mixer, text="System: Idle", fg="#aaaaaa", bg="#1e1e1e", font=("Arial", 12))
        self.status_label.pack(pady=10)
        
        self.action_btn = tk.Button(self.tab_mixer, text="Set the Scene", font=("Arial", 14, "bold"), 
                                    bg="#5cb85c", fg="white", activebackground="#4cae4c", 
                                    command=self.toggle_scene_state, relief="flat", padx=10, pady=5)
        self.action_btn.pack(pady=5)
        
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
        if not self.speech_engine.is_running:
            self.speech_engine.start(active_keyword_map=self.current_keywords, root_window_widget=self.root)
            self.action_btn.config(text="End Scene", bg="#d9534f", fg="white")
            self.status_label.config(text="🎤 Listening for Room Description...", fg="#5cb85c")
        else:
            self.action_btn.config(text="Set the Scene", bg="#5cb85c", fg="white")
            self.status_label.config(text="System: Idle (Audio Fading & Saving)", fg="#aaaaaa")
            self.speech_engine.stop(save_history_callback=self.redraw_mixer_channels)

    def sync_keyword_bank(self):
        self.current_keywords = load_keywords()

    def trigger_ui_refresh(self):
        self.root.after(0, self.redraw_mixer_channels)

    def redraw_mixer_channels(self):
        """Dynamically repopulates the vertical list panel rows inside Tab 1."""
        for widget in self.list_frame.winfo_children():
            widget.destroy()
            
        # FIX: Wrap active_sounds in list() snapshot call to prevent thread size modification crashes
        active_snapshot = list(self.audio_manager.active_sounds.items())
            
        if not active_snapshot:
            tk.Label(self.list_frame, text="No background layers active", fg="#777777", bg="#2d2d2d", font=("Arial", 10, "italic")).pack(pady=20)
            return
            
        for keyword, sound_data in active_snapshot:
            row = tk.Frame(self.list_frame, bg="#2d2d2d", pady=6)
            row.pack(fill="x", padx=10)
            
            tk.Label(row, text=f"  {keyword.capitalize()}", fg="#ffffff", bg="#2d2d2d", font=("Arial", 11), width=12, anchor="w").pack(side="left")
            
            canvas = tk.Canvas(row, width=20, height=20, bg="#2d2d2d", highlightthickness=0)
            canvas.pack(side="left", padx=(5, 5))
            sound_data["canvas_widget"] = canvas
            
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

    def animate_progress_clocks(self):
        """Runs every 100ms to redraw the visual circular countdown arcs."""
        current_time = time.time()
        
        # Loop through any active audio items on screen
        for keyword, data in list(self.audio_manager.active_sounds.items()):
            canvas = data.get("canvas_widget")
            if not canvas or not os.path.exists(CONFIG_FILE):
                continue
                
            duration = data["duration"]
            start_time = data["start_time"]
            
            if duration <= 0:
                continue
                
            # Calculate running position elapsed time coordinates
            elapsed = current_time - start_time
            
            if data["is_one_shot"]:
                # One-shots empty down over their specific lifetime duration bounds
                percentage_rem = max(0.0, 1.0 - (elapsed / duration))
            else:
                # Loops wrap mathematically using modulo calculations to trace file wrap seams
                loop_elapsed = elapsed % duration
                percentage_rem = max(0.0, 1.0 - (loop_elapsed / duration))
                
            # Convert percentage into circles (360 degrees rotation angles)
            extent_angle = int(percentage_rem * 360)
            
            # Redraw the custom vector shape coordinates inside the row canvas container
            canvas.delete("all")
            
            # Select color styling based on whether it is an effect or a looping layer
            arc_color = "#ffcc00" if data["is_one_shot"] else "#5cb85c"  # Gold for FX, Green for Loops
            
            # Render the vector pie slice piece shape layout
            canvas.create_arc(
                2, 2, 18, 18, 
                start=90, extent=extent_angle, 
                fill=arc_color, outline="", style="pieslice"
            )
            
        # Reschedule this exact loop worker to fire again in 100 milliseconds
        self.root.after(100, self.animate_progress_clocks)

    def run(self):
        self.root.mainloop()
