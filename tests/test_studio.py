import json
import tkinter as tk

import pytest

from dm_mixer import studio as studio_module
from dm_mixer.studio import SoundbankStudioController


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as exc:
        pytest.skip(f"No Tk display available in this environment: {exc}")
        return
    yield root
    root.destroy()


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    """Studio methods pop real modal message boxes - stub them so tests never hang
    waiting on a click, and can inspect what would have been shown."""
    calls = {"showinfo": [], "showwarning": [], "showerror": []}
    monkeypatch.setattr(studio_module.messagebox, "showinfo", lambda title, msg: calls["showinfo"].append((title, msg)))
    monkeypatch.setattr(studio_module.messagebox, "showwarning", lambda title, msg: calls["showwarning"].append((title, msg)))
    monkeypatch.setattr(studio_module.messagebox, "showerror", lambda title, msg: calls["showerror"].append((title, msg)))
    monkeypatch.setattr(studio_module.messagebox, "askyesno", lambda title, msg: True)
    return calls


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    sounds_dir = tmp_path / "custom_sounds"
    sounds_dir.mkdir()
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(studio_module, "USER_SOUNDS_DIR", str(sounds_dir))
    monkeypatch.setattr(studio_module, "CONFIG_FILE", str(config_path))
    return sounds_dir, config_path


@pytest.fixture
def controller(tk_root, isolated_paths):
    callback_calls = []
    ctrl = SoundbankStudioController(parent=tk_root, on_config_changed_callback=lambda: callback_calls.append(True))
    ctrl.callback_calls = callback_calls
    yield ctrl
    ctrl.destroy()


def _set_keywords(controller, text):
    controller.kw_entry.delete(0, tk.END)
    controller.kw_entry.insert(0, text)


def _read_config(config_path):
    return json.loads(config_path.read_text())


# ---------------------------------------------------------------------------
# save() - new asset creation
# ---------------------------------------------------------------------------

def test_save_creates_new_loop_asset(controller, isolated_paths, tmp_path):
    sounds_dir, config_path = isolated_paths
    source = tmp_path / "rain.wav"
    source.write_bytes(b"fake audio bytes")

    controller.selected_file_path = str(source)
    _set_keywords(controller, "rain, storm")
    controller.loop_var.set(True)

    controller.save()

    dest_path = str(sounds_dir / "rain.wav")
    config = _read_config(config_path)
    assert config[dest_path] == ["rain", "storm"]
    assert (sounds_dir / "rain.wav").exists()
    assert len(controller.callback_calls) == 1
    # form should reset back to a clean creation state
    assert controller.editing_path is None
    assert controller.kw_entry.get() == ""


def test_save_creates_new_one_shot_asset_with_bang_prefix(controller, isolated_paths, tmp_path):
    sounds_dir, config_path = isolated_paths
    source = tmp_path / "boom.wav"
    source.write_bytes(b"fake audio bytes")

    controller.selected_file_path = str(source)
    _set_keywords(controller, "explosion")
    controller.loop_var.set(False)

    controller.save()

    dest_path = str(sounds_dir / "boom.wav")
    config = _read_config(config_path)
    assert config[dest_path] == ["!explosion"]


def test_save_warns_when_no_file_selected(controller, no_real_dialogs):
    _set_keywords(controller, "rain")
    controller.selected_file_path = None

    controller.save()

    assert len(no_real_dialogs["showwarning"]) == 1


def test_save_warns_when_no_keywords_entered(controller, no_real_dialogs, tmp_path):
    source = tmp_path / "rain.wav"
    source.write_bytes(b"fake audio bytes")
    controller.selected_file_path = str(source)
    _set_keywords(controller, "   ")  # blank after strip

    controller.save()

    assert len(no_real_dialogs["showwarning"]) == 1


# ---------------------------------------------------------------------------
# load_asset_into_form() / cancel_edit_state()
# ---------------------------------------------------------------------------

def test_save_shows_error_when_copy_fails(controller, no_real_dialogs, tmp_path, monkeypatch):
    source = tmp_path / "rain.wav"
    source.write_bytes(b"fake audio bytes")
    controller.selected_file_path = str(source)
    _set_keywords(controller, "rain")

    def exploding_copy(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(studio_module.shutil, "copy", exploding_copy)

    controller.save()  # must not raise

    assert len(no_real_dialogs["showerror"]) == 1


def test_load_asset_into_form_strips_bang_prefix_and_detects_one_shot(controller):
    controller.load_asset_into_form("/library/boom.wav", ["!explosion", "!crash"])

    assert controller.editing_path == "/library/boom.wav"
    assert controller.kw_entry.get() == "explosion, crash"
    assert controller.loop_var.get() is False
    assert controller.save_btn.cget("text") == "💾 Update Campaign Asset"


def test_load_asset_into_form_detects_loop(controller):
    controller.load_asset_into_form("/library/rain.wav", ["rain", "storm"])

    assert controller.kw_entry.get() == "rain, storm"
    assert controller.loop_var.get() is True


def test_cancel_edit_state_resets_form(controller):
    controller.load_asset_into_form("/library/rain.wav", ["rain"])

    controller.cancel_edit_state()

    assert controller.editing_path is None
    assert controller.selected_file_path is None
    assert controller.kw_entry.get() == ""
    assert controller.loop_var.get() is False
    assert controller.save_btn.cget("text") == "➕ Add Sound to Campaign Library"


# ---------------------------------------------------------------------------
# save() - editing an existing asset
# ---------------------------------------------------------------------------

def test_save_edit_flow_updates_keywords_for_same_file(controller, isolated_paths):
    sounds_dir, config_path = isolated_paths
    existing = sounds_dir / "rain.wav"
    existing.write_bytes(b"fake audio bytes")
    config_path.write_text(json.dumps({str(existing): ["rain"]}))

    controller.load_asset_into_form(str(existing), ["rain"])
    _set_keywords(controller, "rain, storm")
    controller.loop_var.set(True)

    controller.save()

    config = _read_config(config_path)
    assert config == {str(existing): ["rain", "storm"]}
    assert existing.exists()  # same-file edit must not touch the underlying file


def test_save_edit_flow_replaces_underlying_file(controller, isolated_paths, tmp_path):
    sounds_dir, config_path = isolated_paths
    old_file = sounds_dir / "old_rain.wav"
    old_file.write_bytes(b"old audio bytes")
    config_path.write_text(json.dumps({str(old_file): ["rain"]}))

    new_source = tmp_path / "new_storm.wav"
    new_source.write_bytes(b"new audio bytes")

    controller.load_asset_into_form(str(old_file), ["rain"])
    controller.selected_file_path = str(new_source)  # user picked a different file mid-edit
    _set_keywords(controller, "storm")
    controller.loop_var.set(True)

    controller.save()

    config = _read_config(config_path)
    new_dest = str(sounds_dir / "new_storm.wav")
    assert config == {new_dest: ["storm"]}
    assert not old_file.exists()  # old file removed
    assert (sounds_dir / "new_storm.wav").exists()  # new file copied in


# ---------------------------------------------------------------------------
# delete_sound_asset()
# ---------------------------------------------------------------------------

def test_delete_sound_asset_removes_entry_and_file(controller, isolated_paths):
    sounds_dir, config_path = isolated_paths
    target = sounds_dir / "boom.wav"
    target.write_bytes(b"fake audio bytes")
    config_path.write_text(json.dumps({str(target): ["!explosion"]}))

    controller.delete_sound_asset(str(target))

    assert _read_config(config_path) == {}
    assert not target.exists()
    assert len(controller.callback_calls) == 1


def test_delete_sound_asset_cancelled_leaves_everything_untouched(controller, isolated_paths, monkeypatch):
    sounds_dir, config_path = isolated_paths
    target = sounds_dir / "boom.wav"
    target.write_bytes(b"fake audio bytes")
    config_path.write_text(json.dumps({str(target): ["!explosion"]}))
    monkeypatch.setattr(studio_module.messagebox, "askyesno", lambda title, msg: False)

    controller.delete_sound_asset(str(target))

    assert _read_config(config_path) == {str(target): ["!explosion"]}
    assert target.exists()
    assert controller.callback_calls == []


def test_delete_sound_asset_clears_form_if_currently_being_edited(controller, isolated_paths):
    sounds_dir, config_path = isolated_paths
    target = sounds_dir / "boom.wav"
    target.write_bytes(b"fake audio bytes")
    config_path.write_text(json.dumps({str(target): ["!explosion"]}))
    controller.load_asset_into_form(str(target), ["!explosion"])

    controller.delete_sound_asset(str(target))

    assert controller.editing_path is None
    assert controller.kw_entry.get() == ""


# ---------------------------------------------------------------------------
# update_library_inventory_gui()
# ---------------------------------------------------------------------------

def test_delete_sound_asset_shows_error_when_removal_fails(controller, isolated_paths, no_real_dialogs, monkeypatch):
    sounds_dir, config_path = isolated_paths
    target = sounds_dir / "boom.wav"
    target.write_bytes(b"fake audio bytes")
    config_path.write_text(json.dumps({str(target): ["!explosion"]}))

    def exploding_remove(_path):
        raise OSError("file is locked")

    monkeypatch.setattr(studio_module.os, "remove", exploding_remove)

    controller.delete_sound_asset(str(target))  # must not raise

    assert len(no_real_dialogs["showerror"]) == 1


def test_inventory_gui_populates_tree_and_filters_placeholder(controller, isolated_paths):
    sounds_dir, config_path = isolated_paths
    config_path.write_text(json.dumps({
        str(sounds_dir / "rain.wav"): ["rain", "storm"],
        str(sounds_dir / "boom.wav"): ["!explosion"],
        str(sounds_dir / "example_placeholder.mp3"): ["example"],
    }))

    controller.update_library_inventory_gui()

    rows = [controller.tree.item(item)["values"] for item in controller.tree.get_children()]
    assert len(rows) == 2
    assert ["rain.wav", "🔄 Loop", "rain, storm"] in [list(r) for r in rows]
    assert ["boom.wav", "💥 One-Shot", "explosion"] in [list(r) for r in rows]


def test_inventory_gui_clears_previous_rows_on_refresh(controller, isolated_paths):
    sounds_dir, config_path = isolated_paths
    config_path.write_text(json.dumps({
        str(sounds_dir / "rain.wav"): ["rain"],
        str(sounds_dir / "boom.wav"): ["!explosion"],
    }))
    controller.update_library_inventory_gui()
    assert len(controller.tree.get_children()) == 2

    config_path.write_text(json.dumps({str(sounds_dir / "rain.wav"): ["rain"]}))
    controller.update_library_inventory_gui()

    assert len(controller.tree.get_children()) == 1  # old rows wiped, not accumulated


def test_inventory_gui_handles_missing_config_file(controller, isolated_paths):
    controller.update_library_inventory_gui()  # CONFIG_FILE doesn't exist yet - must not raise

    assert controller.tree.get_children() == ()


def test_inventory_gui_handles_corrupt_config_file(controller, isolated_paths):
    _sounds_dir, config_path = isolated_paths
    config_path.write_text("{ not valid json")

    controller.update_library_inventory_gui()  # must not raise

    assert controller.tree.get_children() == ()


# ---------------------------------------------------------------------------
# browse()
# ---------------------------------------------------------------------------

def test_browse_sets_selected_file_and_label(controller, monkeypatch):
    monkeypatch.setattr(studio_module.filedialog, "askopenfilename", lambda **kwargs: "/some/dir/rain.wav")

    controller.browse()

    assert controller.selected_file_path == "/some/dir/rain.wav"
    assert controller.file_label.cget("text") == "rain.wav"


def test_browse_cancelled_leaves_selection_unchanged(controller, monkeypatch):
    monkeypatch.setattr(studio_module.filedialog, "askopenfilename", lambda **kwargs: "")

    controller.browse()

    assert controller.selected_file_path is None
