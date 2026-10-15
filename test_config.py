import json

import config


def test_overrides_merge_and_save_keeps_other_keys(tmp_path):
    user = tmp_path / "config.json"
    user.write_text(json.dumps({"brain": {"use": "ollama"}}))
    cfg = config.load(user)
    assert cfg["brain"]["use"] == "ollama" and cfg["brain"]["claude"]["fast_model"] == "haiku"  # default kept
    assert "open_browser" in cfg["presets"] and "main" in cfg["ui"]
    config.save({"ui": {"font": "X"}}, user)
    assert json.loads(user.read_text()) == {"brain": {"use": "ollama"}, "ui": {"font": "X"}}


def test_broken_user_file_falls_back_to_defaults(tmp_path):
    user = tmp_path / "config.json"
    user.write_text("{not json")
    assert config.load(user)["brain"]["use"] == "auto"
