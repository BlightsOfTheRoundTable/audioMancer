import os
import json

# Permanent user profile folder workspace locations
USER_DATA_DIR = os.path.join(os.path.expanduser("~"), ".dm_sound_mixer")
USER_SOUNDS_DIR = os.path.join(USER_DATA_DIR, "custom_sounds")
CONFIG_FILE = os.path.join(USER_DATA_DIR, "config.json")
HISTORY_FILE = os.path.join(USER_DATA_DIR, "volume_history.json")

def ensure_environment():
    """Builds hidden home folders and fallback configurations automatically on system boot."""
    os.makedirs(USER_SOUNDS_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump({}, f, indent=2)

def load_keywords():
    """Parses JSON data to build a flat dictionary with looping metadata mappings."""
    mapping = {}
    if not os.path.exists(CONFIG_FILE):
        return mapping
    with open(CONFIG_FILE, "r") as f:
        try:
            raw_data = json.load(f)
            for path, keywords in raw_data.items():
                for kw in (keywords if isinstance(keywords, list) else [keywords]):
                    kw_clean = kw.lower().strip()
                    mapping[kw_clean.lstrip("!")] = {
                        "file_path": path,
                        "one_shot": kw_clean.startswith("!")
                    }
        except json.JSONDecodeError:
            pass
    return mapping

def calculate_gains(base_volume, master_scale):
    """Encapsulates relative volume trimming calculation math for safety validation operations."""
    return round(float(base_volume) * float(master_scale), 2)

def effective_volume(base_volume, context_volume_multiplier=1.0):
    """Clamped base_volume x context_volume_multiplier - the single source of truth for "what
    should the DM actually hear right now". Used everywhere a sound's real playback level needs
    computing: dispatch to the audio worker, periodic re-fires, and the UI's progress bars. Kept
    in one place so a manual slider drag, a "faint"/"massive" auto-adjustment, and the on-screen
    meter can never quietly disagree about the same number."""
    return max(0.0, min(1.0, float(base_volume) * float(context_volume_multiplier)))
