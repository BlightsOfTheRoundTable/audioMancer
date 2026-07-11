from dm_mixer.utils import calculate_gains, load_keywords
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
