from dm_mixer.utils import calculate_gains, load_keywords, ensure_environment
import json
import os

def test_relative_volume_calculation():
    """Ensures our relative master scaling multiplier returns correct balances."""
    assert calculate_gains(1.0, 0.5) == 0.50
    assert calculate_gains(0.8, 0.5) == 0.40
    assert calculate_gains(0.5, 0.0) == 0.00

def test_config_parsing_logic(tmp_path, monkeypatch):
    """Mocks a config file to verify loop vs one-shot classifications work."""
    mock_config = tmp_path / "config.json"
    data = {
        "sounds/rain.mp3": ["rain"],
        "sounds/boom.wav": ["!explosion"]
    }
    mock_config.write_text(json.dumps(data))
    
    # Force utils path pointer to look at our temporary safe file test target
    monkeypatch.setattr("dm_mixer.utils.CONFIG_FILE", str(mock_config))
    
    mapping = load_keywords()
    
    # Confirm the rain keyword is parsed as an active background loop
    assert mapping["rain"]["one_shot"] is False
    # Confirm explosion keyword lstrips the syntax identifier flag but catches the rule state
    assert mapping["explosion"]["one_shot"] is True
    assert mapping["explosion"]["file_path"] == "sounds/boom.wav"

def test_load_keywords_missing_config_file(tmp_path, monkeypatch):
    """A CONFIG_FILE that doesn't exist yet should yield an empty mapping, not a crash."""
    missing_path = tmp_path / "does_not_exist.json"
    monkeypatch.setattr("dm_mixer.utils.CONFIG_FILE", str(missing_path))

    assert load_keywords() == {}

def test_load_keywords_malformed_json(tmp_path, monkeypatch):
    """Corrupted JSON should be swallowed, returning an empty mapping instead of raising."""
    bad_config = tmp_path / "config.json"
    bad_config.write_text("{ this is not valid json")
    monkeypatch.setattr("dm_mixer.utils.CONFIG_FILE", str(bad_config))

    assert load_keywords() == {}

def test_load_keywords_accepts_single_string_keyword(tmp_path, monkeypatch):
    """A keywords value that's a bare string (not a list) should still be parsed."""
    mock_config = tmp_path / "config.json"
    mock_config.write_text(json.dumps({"sounds/gong.wav": "!gong"}))
    monkeypatch.setattr("dm_mixer.utils.CONFIG_FILE", str(mock_config))

    mapping = load_keywords()

    assert mapping["gong"]["one_shot"] is True
    assert mapping["gong"]["file_path"] == "sounds/gong.wav"

def test_ensure_environment_creates_sounds_dir_and_default_config(tmp_path, monkeypatch):
    """First run should create the user sounds folder and seed an empty config file."""
    sounds_dir = tmp_path / "custom_sounds"
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("dm_mixer.utils.USER_SOUNDS_DIR", str(sounds_dir))
    monkeypatch.setattr("dm_mixer.utils.CONFIG_FILE", str(config_path))

    ensure_environment()

    assert sounds_dir.is_dir()
    assert json.loads(config_path.read_text()) == {}

def test_ensure_environment_does_not_clobber_existing_config(tmp_path, monkeypatch):
    """Re-running on a later launch must not wipe out an already-populated config file."""
    sounds_dir = tmp_path / "custom_sounds"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"sounds/rain.wav": ["rain"]}))
    monkeypatch.setattr("dm_mixer.utils.USER_SOUNDS_DIR", str(sounds_dir))
    monkeypatch.setattr("dm_mixer.utils.CONFIG_FILE", str(config_path))

    ensure_environment()

    assert json.loads(config_path.read_text()) == {"sounds/rain.wav": ["rain"]}
