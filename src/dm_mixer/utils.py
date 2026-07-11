import os
import json

USER_DATA_DIR = os.path.join(os.path.expanduser("~"), ".dm_sound_mixer")
USER_SOUNDS_DIR = os.path.join(USER_DATA_DIR, "custom_sounds")
CONFIG_FILE = os.path.join(USER_DATA_DIR, "config.json")
HISTORY_FILE = os.path.join(USER_DATA_DIR, "volume_history.json")

def ensure_environment():
    """Builds hidden home folders and fallback configurations automatically."""
    os.makedirs(USER_SOUNDS_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump({}, f, indent=2)

def load_keywords():
    """Parses JSON data to build a flat dictionary with looping metadata."""
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
    """Encapsulates relative volume trimming calculation math for safety validation."""
    return round(float(base_volume) * float(master_scale), 2)
