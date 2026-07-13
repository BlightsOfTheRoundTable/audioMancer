import os
import json
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from dm_mixer.utils import USER_SOUNDS_DIR, CONFIG_FILE

class SoundbankStudioController(tk.Frame):
    def __init__(self, parent, on_config_changed_callback):
        """
        Tab 2 Dashboard: Handles importing, viewing, and seamlessly editing
        saved audio tracks and their matching voice trigger keywords.
        """
        super().__init__(parent, bg="#1e1e1e")
        self.on_config_changed_callback = on_config_changed_callback
        self.selected_file_path = None
        
        # State tracker to distinguish between a new creation vs an asset edit
        self.editing_path = None
        
        # 1. IMPORT & EDIT CONTAINER FORM BLOCK
        self.form_title = tk.Label(self, text="⚙️ IMPORT CUSTOM ATMOSPHERE ASSETS", fg="#ffcc00", bg="#1e1e1e", font=("Arial", 11, "bold"))
        self.form_title.pack(pady=(10, 5))
        
        box = tk.Frame(self, bg="#2d2d2d", bd=1, relief="solid", pady=10, padx=15)
        box.pack(fill="x", padx=25)
        
        # Row A: Browse Selector
        row_a = tk.Frame(box, bg="#2d2d2d")
        row_a.pack(fill="x", pady=2)
        self.file_label = tk.Label(row_a, text="No Audio Track Picked...", fg="#888888", bg="#2d2d2d", width=32, anchor="w", font=("Arial", 10, "italic"))
        self.file_label.pack(side="left", padx=5)
        self.browse_btn = tk.Button(row_a, text="Browse Audio File", font=("Arial", 9, "bold"), bg="#ffcc00", fg="#1e1e1e", command=self.browse)
        self.browse_btn.pack(side="right")
        
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
        
        # Row D: Unified Action Trigger Button
        self.save_btn = tk.Button(self, text="➕ Add Sound to Campaign Library", font=("Arial", 11, "bold"), bg="#5cb85c", fg="white", activebackground="#4cae4c", relief="flat", padx=10, pady=4, command=self.save)
        self.save_btn.pack(pady=10)

        # 2. TABULAR INVENTORY VIEW BLOCK
        library_title = tk.Label(self, text="📜 CAMPAIGN SOUND LIBRARY INVENTORY", fg="#ffffff", bg="#1e1e1e", font=("Arial", 10, "bold"))
        library_title.pack(anchor="w", padx=25, pady=(10, 2))

        self.table_container = tk.Frame(self, bg="#1e1e1e")
        self.table_container.pack(fill="both", expand=True, padx=25, pady=(0, 15))
        
        self.tree = ttk.Treeview(self.table_container, columns=("File", "Type", "Keywords"), show="headings", selectmode="none")
        self.tree.heading("File", text=" File Name", anchor="w")
        self.tree.heading("Type", text="Playback Type", anchor="center")
        self.tree.heading("Keywords", text=" Trigger Keywords", anchor="w")
        
        self.tree.column("File", width=140, minwidth=100, anchor="w")
        self.tree.column("Type", width=90, minwidth=80, anchor="center")
        self.tree.column("Keywords", width=180, minwidth=120, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        self.btn_sidebar = tk.Frame(self.table_container, bg="#1e1e1e")
        self.btn_sidebar.pack(side="right", fill="y", padx=(5, 0))

    def browse(self):
        fp = filedialog.askopenfilename(title="Select Atmosphere Sound Track", filetypes=[("Audio Files Files", "*.mp3 *.wav *.ogg")])
        if fp:
            self.selected_file_path = fp
            self.file_label.config(text=os.path.basename(fp), fg="#5cb85c", font=("Arial", 10, "bold"))

    def load_asset_into_form(self, target_path, keywords):
        """Populates the input form fields with an existing item's values for inline editing."""
        self.editing_path = target_path
        self.selected_file_path = target_path
        
        # 1. Update Title and Form Visual Cues
        self.form_title.config(text="⚙️ EDIT CAMPAIGN ASSET PROPERTIES", fg="#ffcc00")
        self.file_label.config(text=os.path.basename(target_path), fg="#ffcc00", font=("Arial", 10, "bold"))
        
        # 2. Extract and format the raw keywords, stripping trigger prefixes
        clean_kws = [kw.lstrip("!") for kw in keywords]
        self.kw_entry.delete(0, tk.END)
        self.kw_entry.insert(0, ", ".join(clean_kws))
        
        # 3. Match the looping property state checkmark
        is_loop = not any(kw.startswith("!") for kw in keywords)
        self.loop_var.set(is_loop)
        
        # 4. Morph the action button into an absolute update command
        self.save_btn.config(text="💾 Update Campaign Asset", bg="#ffcc00", fg="#1e1e1e", activebackground="#e6b800")
        
    def cancel_edit_state(self):
        """Resets the input form back to a clean creation layout state."""
        self.editing_path = None
        self.selected_file_path = None
        self.form_title.config(text="⚙️ IMPORT CUSTOM ATMOSPHERE ASSETS", fg="#ffcc00")
        self.file_label.config(text="No Audio Track Picked...", fg="#888888", font=("Arial", 10, "italic"))
        self.kw_entry.delete(0, tk.END)
        self.loop_var.set(value=False)
        self.save_btn.config(text="➕ Add Sound to Campaign Library", bg="#5cb85c", fg="white", activebackground="#4cae4c")

    def save(self):
        if not self.selected_file_path or not self.kw_entry.get().strip():
            messagebox.showwarning("Incomplete Data", "Please link an audio file and supply trigger words first!")
            return
            
        raw_kws = [kw.lower().strip() for kw in self.kw_entry.get().split(",") if kw.strip()]
        new_kws = [kw if self.loop_var.get() else f"!{kw}" for kw in raw_kws]
        
        try:
            current_config = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    current_config = json.load(f)

            if self.editing_path:
                # --- UPDATE EXISTING PATH FLOW ---
                if self.selected_file_path != self.editing_path:
                    fn = os.path.basename(self.selected_file_path)
                    dest_path = os.path.join(USER_SOUNDS_DIR, fn)
                    shutil.copy(self.selected_file_path, dest_path)
                    
                    if self.editing_path in current_config:
                        del current_config[self.editing_path]
                    try:
                        if os.path.exists(self.editing_path):
                            os.remove(self.editing_path)
                    except Exception:
                        pass
                    final_path = dest_path
                else:
                    final_path = self.editing_path
                
                current_config[final_path] = new_kws
                messagebox.showinfo("Asset Updated", "Campaign asset configurations modified successfully!")
            else:
                # --- FRESH INITIAL CREATION FLOW ---
                fn = os.path.basename(self.selected_file_path)
                dest_path = os.path.join(USER_SOUNDS_DIR, fn)
                shutil.copy(self.selected_file_path, dest_path)
                current_config[dest_path] = new_kws
                messagebox.showinfo("Success!", f"Successfully mapped '{fn}' to your library database.")
            
            with open(CONFIG_FILE, "w") as f:
                json.dump(current_config, f, indent=2)
                
            self.cancel_edit_state()
            self.on_config_changed_callback()
            self.update_library_inventory_gui()
            
        except Exception as e:
            messagebox.showerror("Save Failure", f"Could not process asset update request:\n{e}")
            
    def update_library_inventory_gui(self):
        """Wipes and re-renders the clean data rows inside the matrix table grid."""
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

            # Alignment padding label matching heading grid thickness
            tk.Label(self.btn_sidebar, text="", bg="#1e1e1e", font=("Arial", 10, "bold"), height=1).pack()

            for path, keywords in filtered_config.items():
                filename = os.path.basename(path)
                is_file_one_shot = any(kw.strip().startswith("!") for kw in keywords)
                type_tag = "💥 One-Shot" if is_file_one_shot else "🔄 Loop"
                clean_keywords = [kw.strip().lstrip("!") for kw in keywords]
                
                self.tree.insert("", "end", values=(filename, type_tag, ", ".join(clean_keywords)))

                # Create an explicit isolated sub-frame row container for the control button pairs
                control_row = tk.Frame(self.btn_sidebar, bg="#1e1e1e")
                control_row.pack(pady=1)

                # 1. Edit Parameter Button (⚙️)
                edit_btn = tk.Button(control_row, text="⚙️", fg="#ffffff", bg="#444444", activebackground="#ffcc00",
                                     relief="flat", font=("Arial", 8), height=1, width=3, bd=0,
                                     command=lambda p=path, k=keywords: self.load_asset_into_form(p, k))
                edit_btn.pack(side="left", padx=(0, 2))

                # 2. Delete Asset Button (✕)
                del_btn = tk.Button(control_row, text="✕", fg="#ffffff", bg="#444444", activebackground="#d9534f",
                                    relief="flat", font=("Arial", 8, "bold"), height=1, width=3, bd=0,
                                    command=lambda p=path: self.delete_sound_asset(p))
                del_btn.pack(side="left")
                
        except Exception as e:
            print(f"⚠️ Error drawing grid: {e}")

    def delete_sound_asset(self, target_path):
        if messagebox.askyesno("Delete Sound Asset", f"Permanently remove this audio track?"):
            try:
                # If they delete the item they are currently actively editing, clear out the form inputs
                if self.editing_path == target_path:
                    self.cancel_edit_state()

                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, "r") as f:
                        config_data = json.load(f)
                    if target_path in config_data:
                        del config_data[target_path]
                    with open(CONFIG_FILE, "w") as f:
                        json.dump(config_data, f, indent=2)

                if os.path.exists(target_path):
                    os.remove(target_path)

                self.on_config_changed_callback()
                self.update_library_inventory_gui()
            except Exception as e:
                messagebox.showerror("Purge Error", f"Failed to fully delete asset properties:\n{e}")
