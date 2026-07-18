import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

from dm_mixer.utils import ensure_environment, load_keywords, CONFIG_FILE
from dm_mixer.audio import AudioManager
from dm_mixer.speech import TranscriptionEngine
from dm_mixer.studio import SoundbankStudioController

class DMSoundApplication:
    def __init__(self):
        # 1. Initialize data folders first
        ensure_environment()

        # 2. Initialize your sub-modules natively. AudioManager spawns its own sandboxed
        # worker process, which selects the right audio driver for the host platform.
        self.audio_manager = AudioManager()

        self.speech_engine = TranscriptionEngine(
            audio_manager=self.audio_manager,
            on_keyword_triggered_callback=self.trigger_ui_refresh
        )
        
        # 3. Assemble standard root window parameters
        self.root = tk.Tk()
        self.root.title("DM Sound Dashboard v2.1")
        self.root.geometry("620x540")
        self.root.configure(bg="#1e1e1e")
        
        # Configure global themed visual style settings
        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure("TNotebook", background="#1e1e1e", borderwidth=0)
        self.style.configure("TNotebook.Tab", background="#2d2d2d", fg="#ffffff", font=("Arial", 10, "bold"), padding=8)
        self.style.map("TNotebook.Tab", background=[("selected", "#ffcc00")], foreground=[("selected", "#1e1e1e")])
        self.style.configure("Horizontal.TProgressbar", thickness=6, troughcolor="#1e1e1e", background="#ffcc00")
        
        self.style.configure("Treeview", background="#2d2d2d", fieldbackground="#2d2d2d", foreground="#ffffff", rowheight=24)
        self.style.configure("Treeview.Heading", background="#444444", foreground="#ffffff", font=("Arial", 9, "bold"))

        # Construct Notebook Tab Navigation Frame Container Elements
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.tab_mixer = tk.Frame(self.notebook, bg="#1e1e1e")
        self.tab_studio = tk.Frame(self.notebook, bg="#1e1e1e")
        
        self.notebook.add(self.tab_mixer, text=" 🎛️ Live Scene Mixer ")
        self.notebook.add(self.tab_studio, text=" 🎵 Soundbank Studio ")
        
        self.is_recording = False
        
        # Build Sub-Panels
        self.build_live_mixer_tab()
        
        self.studio_panel = SoundbankStudioController(
            parent=self.tab_studio,
            on_config_changed_callback=self.sync_keyword_bank
        )
        self.studio_panel.pack(fill="both", expand=True)
        self.studio_panel.update_library_inventory_gui()
        
        self.sync_keyword_bank()
        self.animate_progress_clocks()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_live_mixer_tab(self):
        """Assembles Tab 1 user mixing interfaces with a dynamic scrolling canvas container."""
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
        
        # =====================================================================
        # NEW SCROLLABLE CANVAS CONTAINER ASSEMBLE
        # =====================================================================
        # 1. This outer solid frame sets the physical visual boundaries on your tab
        outer_container = tk.Frame(self.tab_mixer, bg="#2d2d2d", bd=1, relief="solid")
        outer_container.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        # 2. Create the internal canvas window that permits coordinate view sliding
        self.mixer_canvas = tk.Canvas(outer_container, bg="#2d2d2d", highlightthickness=0)
        
        # 3. Create the native vertical scrollbar and link it to the canvas view engine
        self.mixer_scrollbar = ttk.Scrollbar(outer_container, orient="vertical", command=self.mixer_canvas.yview)
        self.mixer_canvas.configure(yscrollcommand=self.mixer_scrollbar.set)
        
        # Pack canvas left, scrollbar right
        self.mixer_canvas.pack(side="left", fill="both", expand=True)
        self.mixer_scrollbar.pack(side="right", fill="y")
        
        # 4. This is the actual scrolling sub-frame where our track rows will be drawn
        self.list_frame = tk.Frame(self.mixer_canvas, bg="#2d2d2d")
        
        # Create a window inside the canvas to hold our sub-frame
        self.canvas_window_id = self.mixer_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        
        # Configure the canvas to stretch the sub-frame horizontally to match window width changes
        def _configure_canvas(event):
            self.mixer_canvas.itemconfig(self.canvas_window_id, width=event.width)
        self.mixer_canvas.bind("<Configure>", _configure_canvas)
        
        # Configure the scrollable ceiling boundary region whenever the sub-frame resizes
        def _configure_list_frame(event):
            self.mixer_canvas.configure(scrollregion=self.mixer_canvas.bbox("all"))
        self.list_frame.bind("<Configure>", _configure_list_frame)
        
        # 5. Bind mouse scroll gestures for ergonomics
        def _on_mixer_mousewheel(event):
            # Only scroll if the content overflows the frame viewport bounds
            if self.mixer_canvas.bbox("all")[3] > self.mixer_canvas.winfo_height():
                self.mixer_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                
        # Bind the wheel to the container elements uniformly
        self.mixer_canvas.bind("<MouseWheel>", _on_mixer_mousewheel)
        self.list_frame.bind("<MouseWheel>", _on_mixer_mousewheel)

        self.redraw_mixer_channels()
        
    def toggle_scene_state(self):
        if not self.speech_engine.is_running:
            started = self.speech_engine.start(active_keyword_map=self.current_keywords, root_window_widget=self.root)
            if started:
                self.action_btn.config(text="End Scene", bg="#d9534f", fg="white")
                self.status_label.config(text="🎤 Listening for Room Description...", fg="#5cb85c")
            else:
                self.status_label.config(text="⚠️ Microphone Error - Could Not Start", fg="#d9534f")
                messagebox.showerror(
                    "Microphone Error",
                    "Could not access the microphone. Check that one is connected and not already in use by another application."
                )
        else:
            self.action_btn.config(text="Set the Scene", bg="#5cb85c", fg="white")
            self.status_label.config(text="System: Idle (Audio Fading & Saving)", fg="#aaaaaa")
            self.speech_engine.stop(save_history_callback=self.redraw_mixer_channels)

    def sync_keyword_bank(self):
        self.current_keywords = load_keywords()

    def trigger_ui_refresh(self):
        self.root.after(0, self.redraw_mixer_channels)

    def redraw_mixer_channels(self):
        """Dynamically repopulates the vertical list panel rows inside the scrolling container."""
        # Wipe out all previous visual row elements safely
        for widget in self.list_frame.winfo_children():
            widget.destroy()
            
        active_snapshot = list(self.audio_manager.active_sounds.items())
            
        if not active_snapshot:
            # Hide the scrollbar frame if the soundboard mixer is completely empty
            self.mixer_scrollbar.pack_forget()
            
            # Draw an elegant placeholder label centered in the sub-frame
            lbl = tk.Label(self.list_frame, text="No background layers active", fg="#777777", bg="#2d2d2d", font=("Arial", 10, "italic"))
            lbl.pack(pady=40, fill="x", expand=True)
            return
            
        # Re-mount the vertical scrollbar element the moment an audio row joins the mix layout
        self.mixer_scrollbar.pack(side="right", fill="y")
        
        for keyword, sound_data in active_snapshot:
            row = tk.Frame(self.list_frame, bg="#2d2d2d", pady=6)
            row.pack(fill="x", padx=10)
            
            # Ensure the scrollwheel works even if the cursor is hovering directly over a specific row background
            def _forward_wheel(event):
                if self.mixer_canvas.bbox("all")[3] > self.mixer_canvas.winfo_height():
                    self.mixer_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            row.bind("<MouseWheel>", _forward_wheel)
            
            lbl = tk.Label(row, text=f"  {keyword.capitalize()}", fg="#ffffff", bg="#2d2d2d", font=("Arial", 11), width=12, anchor="w")
            lbl.pack(side="left")
            lbl.bind("<MouseWheel>", _forward_wheel)

            # Shows the actual word that adjusted this trigger's volume (e.g. "faint"), so the
            # DM can see WHY it sounds different from where the slider is sitting - the slider
            # itself deliberately keeps showing their manual baseline, not the one-time override.
            modifier_word = sound_data.get("context_modifier_word")
            if modifier_word:
                modifier_lbl = tk.Label(row, text=f"({modifier_word})", fg="#00bcff", bg="#2d2d2d", font=("Arial", 9, "italic"))
                modifier_lbl.pack(side="left", padx=(0, 5))
                modifier_lbl.bind("<MouseWheel>", _forward_wheel)

            canvas = tk.Canvas(row, width=20, height=20, bg="#2d2d2d", highlightthickness=0)
            canvas.pack(side="left", padx=(5, 5))
            sound_data["canvas_widget"] = canvas
            canvas.bind("<MouseWheel>", _forward_wheel)
            
            current_vol_percentage = int(sound_data["base_volume"] * 100)
            slider = tk.Scale(
                row, from_=0, to=100, orient="horizontal", showvalue=False,
                bg="#2d2d2d", fg="#ffffff", highlightthickness=0, troughcolor="#1e1e1e", activebackground="#5cb85c",
            )
            # Tkinter gotcha: Scale.set() fires its `command` callback once the widget is part
            # of a visible window - attaching `command` only AFTER set() (instead of at
            # construction) stops that initial programmatic set from being mistaken for a real
            # user drag, which was silently resetting context_volume_multiplier back to 1.0 and
            # overwriting the just-applied volume within milliseconds of every trigger.
            slider.set(current_vol_percentage)
            slider.config(command=lambda val, kw=keyword: self.audio_manager.update_individual_volume(kw, val))
            slider.pack(side="left", fill="x", expand=True, padx=10)
            sound_data["slider_widget"] = slider
            slider.bind("<MouseWheel>", _forward_wheel)
            
            effective_volume = sound_data["base_volume"] * sound_data.get("context_volume_multiplier", 1.0)
            scaled_percentage = int(max(0.0, min(1.0, effective_volume)) * self.audio_manager.master_scale * 100)
            progress_bar = ttk.Progressbar(row, orient="horizontal", length=60, mode="determinate", style="Horizontal.TProgressbar")
            progress_bar.config(maximum=100, value=scaled_percentage)
            progress_bar.pack(side="left", padx=(5, 10))
            sound_data["visual_bar_widget"] = progress_bar
            
            tk.Button(
                row, text="✕", fg="#ffffff", bg="#d9534f", activebackground="#c9302c", relief="flat", font=("Arial", 9, "bold"),
                command=lambda kw=keyword: self.audio_manager.stop_track_with_gui_sync(kw, callback=self.redraw_mixer_channels)
            ).pack(side="right", padx=(5, 0))

    def animate_progress_clocks(self):
        """Runs every 100ms to redraw progress circles with unique periodic colors."""
        current_time = time.time()
        
        for keyword, data in list(self.audio_manager.active_sounds.items()):
            canvas = data.get("canvas_widget")
            if not canvas or not os.path.exists(CONFIG_FILE):
                continue
                
            duration = data["duration"]
            start_time = data["start_time"]
            
            if duration <= 0:
                continue
                
            elapsed = current_time - start_time
            
            if data.get("is_one_shot", False):
                percentage_rem = max(0.0, 1.0 - (elapsed / duration))
            elif data.get("is_periodic", False):
                # Periodic loops count down their specific duration block interval smoothly
                percentage_rem = max(0.0, 1.0 - (elapsed / duration))
            else:
                # Ambient loops wrap mathematically
                loop_elapsed = elapsed % duration
                percentage_rem = max(0.0, 1.0 - (loop_elapsed / duration))
                
            extent_angle = int(percentage_rem * 360)
            canvas.delete("all")
            
            # --- FIX: ASSIGN THEMATIC COLOR BASED ON FUNCTION TYPE ---
            if data.get("is_periodic", False):
                arc_color = "#00bcff"     # Sharp Cyan Blue for recurring timers ⏱️
            elif data.get("is_one_shot", False):
                arc_color = "#ffcc00"     # Solid Gold for immediate effects 💥
            else:
                arc_color = "#5cb85c"     # Forest Green for continuous loops 🔄
                
            canvas.create_arc(
                2, 2, 18, 18, 
                start=90, extent=extent_angle, 
                fill=arc_color, outline="", style="pieslice"
            )
            
        self.root.after(100, self.animate_progress_clocks)

    def on_close(self):
        """Ensures the mic stream and the sandboxed audio worker process both shut down cleanly."""
        if self.speech_engine.is_running:
            self.speech_engine.stop(save_history_callback=None)
        self.audio_manager.shutdown()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
