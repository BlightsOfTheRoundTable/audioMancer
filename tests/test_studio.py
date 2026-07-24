import json
import tkinter as tk

import pytest

from dm_mixer import studio as studio_module
from dm_mixer.studio import (
    SoundbankStudioController,
    _copy_into_sounds_library,
    _find_keyword_conflicts,
    _unique_destination_path,
)


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
# _unique_destination_path / _copy_into_sounds_library / _find_keyword_conflicts
# ---------------------------------------------------------------------------

def test_unique_destination_path_returns_natural_name_when_free(tmp_path):
    assert _unique_destination_path(str(tmp_path), "rain.wav") == str(tmp_path / "rain.wav")


def test_unique_destination_path_appends_suffix_on_collision(tmp_path):
    (tmp_path / "rain.wav").write_bytes(b"existing")

    result = _unique_destination_path(str(tmp_path), "rain.wav")

    assert result == str(tmp_path / "rain (1).wav")


def test_unique_destination_path_increments_past_multiple_collisions(tmp_path):
    (tmp_path / "rain.wav").write_bytes(b"existing")
    (tmp_path / "rain (1).wav").write_bytes(b"existing too")

    result = _unique_destination_path(str(tmp_path), "rain.wav")

    assert result == str(tmp_path / "rain (2).wav")


def test_copy_into_sounds_library_renames_on_collision(tmp_path):
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    (sounds_dir / "boom.wav").write_bytes(b"an unrelated existing asset")
    new_source = tmp_path / "external" / "boom.wav"
    new_source.parent.mkdir()
    new_source.write_bytes(b"a totally different sound")

    import dm_mixer.studio as studio_mod
    original_dir = studio_mod.USER_SOUNDS_DIR
    studio_mod.USER_SOUNDS_DIR = str(sounds_dir)
    try:
        dest = _copy_into_sounds_library(str(new_source))
    finally:
        studio_mod.USER_SOUNDS_DIR = original_dir

    assert dest == str(sounds_dir / "boom (1).wav")
    assert (sounds_dir / "boom.wav").read_bytes() == b"an unrelated existing asset"  # untouched
    assert (sounds_dir / "boom (1).wav").read_bytes() == b"a totally different sound"


def test_copy_into_sounds_library_overwrites_in_place_when_allowed(tmp_path):
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    existing = sounds_dir / "boom.wav"
    existing.write_bytes(b"old audio")
    new_source = tmp_path / "external" / "boom.wav"
    new_source.parent.mkdir()
    new_source.write_bytes(b"new audio")

    import dm_mixer.studio as studio_mod
    original_dir = studio_mod.USER_SOUNDS_DIR
    studio_mod.USER_SOUNDS_DIR = str(sounds_dir)
    try:
        dest = _copy_into_sounds_library(str(new_source), allow_overwrite_path=str(existing))
    finally:
        studio_mod.USER_SOUNDS_DIR = original_dir

    assert dest == str(existing)
    assert existing.read_bytes() == b"new audio"


def test_copy_into_sounds_library_skips_copy_when_source_already_at_destination(tmp_path):
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    already_there = sounds_dir / "boom.wav"
    already_there.write_bytes(b"original content")

    import dm_mixer.studio as studio_mod
    original_dir = studio_mod.USER_SOUNDS_DIR
    studio_mod.USER_SOUNDS_DIR = str(sounds_dir)
    try:
        dest = _copy_into_sounds_library(str(already_there))
    finally:
        studio_mod.USER_SOUNDS_DIR = original_dir

    assert dest == str(already_there)
    assert already_there.read_bytes() == b"original content"


def test_find_keyword_conflicts_detects_collision_with_another_file():
    current_config = {
        "/library/rain.wav": ["rain", "storm"],
        "/library/boom.wav": ["!explosion"],
    }

    conflicts = _find_keyword_conflicts(["storm", "thunder"], current_config, exclude_path=None)

    assert conflicts == {"storm": "rain.wav"}


def test_find_keyword_conflicts_ignores_the_excluded_path():
    current_config = {"/library/rain.wav": ["rain", "storm"]}

    conflicts = _find_keyword_conflicts(["storm"], current_config, exclude_path="/library/rain.wav")

    assert conflicts == {}


def test_find_keyword_conflicts_matches_regardless_of_bang_prefix():
    current_config = {"/library/boom.wav": ["!explosion"]}

    conflicts = _find_keyword_conflicts(["explosion"], current_config, exclude_path=None)

    assert conflicts == {"explosion": "boom.wav"}


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
    assert controller.clear_btn.cget("text") == "✕ Clear"


def test_load_asset_into_form_relabels_clear_button_to_cancel_edit(controller):
    controller.load_asset_into_form("/library/rain.wav", ["rain"])

    assert controller.clear_btn.cget("text") == "✕ Cancel Edit"


def test_clear_button_unstages_a_picked_file_before_saving(controller, monkeypatch):
    """The originally reported gap: browsing to a file with no way to back out of it
    before saving. Clicking Clear (cancel_edit_state) must un-stage it."""
    monkeypatch.setattr(studio_module.filedialog, "askopenfilename", lambda **kwargs: "/some/dir/rain.wav")
    controller.browse()
    assert controller.selected_file_path == "/some/dir/rain.wav"

    controller.cancel_edit_state()

    assert controller.selected_file_path is None
    assert controller.file_label.cget("text") == "No Audio Track Picked..."


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


def test_save_edit_flow_survives_and_logs_when_old_file_cannot_be_removed(controller, isolated_paths, tmp_path, capsys):
    """Regression test: the old-file cleanup during a replace-underlying-file edit used to
    swallow any failure with a bare `except: pass` - no log, no trace. The new file has
    already copied successfully at that point, so the edit must still go through (no "Save
    Failure" dialog), but the failure must now be logged instead of vanishing silently."""
    sounds_dir, config_path = isolated_paths
    old_file = sounds_dir / "old_rain"
    old_file.mkdir()  # a directory in place of the expected file - os.remove() will raise
    config_path.write_text(json.dumps({str(old_file): ["rain"]}))

    new_source = tmp_path / "new_storm.wav"
    new_source.write_bytes(b"new audio bytes")

    controller.load_asset_into_form(str(old_file), ["rain"])
    controller.selected_file_path = str(new_source)
    _set_keywords(controller, "storm")
    controller.loop_var.set(True)

    controller.save()  # must not raise

    config = _read_config(config_path)
    assert config == {str(sounds_dir / "new_storm.wav"): ["storm"]}  # edit still succeeded
    assert old_file.is_dir()  # cleanup failed, left behind

    captured = capsys.readouterr()
    assert "[ERROR-STUDIO-CLEANUP]" in captured.err


# ---------------------------------------------------------------------------
# save() - filename collisions
# ---------------------------------------------------------------------------

def test_save_renames_new_asset_on_filename_collision(controller, isolated_paths, tmp_path):
    """Regression test: importing two different files that happen to share a basename used
    to silently overwrite the first one's audio on disk. The second import must now get a
    unique filename instead."""
    sounds_dir, config_path = isolated_paths
    existing = sounds_dir / "boom.wav"
    existing.write_bytes(b"the original explosion sound")

    new_source = tmp_path / "external" / "boom.wav"
    new_source.parent.mkdir()
    new_source.write_bytes(b"a completely different sound")

    controller.selected_file_path = str(new_source)
    _set_keywords(controller, "crash")
    controller.loop_var.set(False)

    controller.save()

    config = _read_config(config_path)
    renamed_dest = str(sounds_dir / "boom (1).wav")
    assert config == {renamed_dest: ["!crash"]}
    assert existing.read_bytes() == b"the original explosion sound"  # untouched
    assert (sounds_dir / "boom (1).wav").read_bytes() == b"a completely different sound"


def test_save_edit_flow_renames_on_collision_with_unrelated_asset(controller, isolated_paths, tmp_path):
    """Same collision protection during an edit's 'replace the underlying file' flow."""
    sounds_dir, config_path = isolated_paths
    unrelated = sounds_dir / "boom.wav"
    unrelated.write_bytes(b"an unrelated existing asset")
    editing_target = sounds_dir / "old_rain.wav"
    editing_target.write_bytes(b"old rain audio")
    config_path.write_text(json.dumps({
        str(unrelated): ["!explosion"],
        str(editing_target): ["rain"],
    }))

    new_source = tmp_path / "external" / "boom.wav"
    new_source.parent.mkdir()
    new_source.write_bytes(b"replacement rain audio, named boom by coincidence")

    controller.load_asset_into_form(str(editing_target), ["rain"])
    controller.selected_file_path = str(new_source)
    _set_keywords(controller, "rain")
    controller.loop_var.set(True)

    controller.save()

    config = _read_config(config_path)
    renamed_dest = str(sounds_dir / "boom (1).wav")
    assert config[str(unrelated)] == ["!explosion"]  # unrelated entry untouched
    assert config[renamed_dest] == ["rain"]
    assert not editing_target.exists()  # old file for this entry is gone
    assert unrelated.read_bytes() == b"an unrelated existing asset"  # unrelated file untouched


def test_save_edit_flow_overwrites_in_place_when_replacement_shares_editing_filename(controller, isolated_paths, tmp_path):
    """If the replacement file happens to share the SAME filename as the entry being edited,
    it should overwrite that file in place, not get treated as a collision with itself."""
    sounds_dir, config_path = isolated_paths
    editing_target = sounds_dir / "boom.wav"
    editing_target.write_bytes(b"old boom audio")
    config_path.write_text(json.dumps({str(editing_target): ["!explosion"]}))

    new_source = tmp_path / "external" / "boom.wav"
    new_source.parent.mkdir()
    new_source.write_bytes(b"new boom audio")

    controller.load_asset_into_form(str(editing_target), ["!explosion"])
    controller.selected_file_path = str(new_source)
    _set_keywords(controller, "explosion")
    controller.loop_var.set(False)

    controller.save()

    config = _read_config(config_path)
    assert config == {str(editing_target): ["!explosion"]}
    assert editing_target.exists()
    assert editing_target.read_bytes() == b"new boom audio"  # overwritten, not deleted


# ---------------------------------------------------------------------------
# save() - duplicate keyword validation
# ---------------------------------------------------------------------------

def test_save_blocks_when_keyword_already_used_by_another_file(controller, isolated_paths, no_real_dialogs, tmp_path):
    sounds_dir, config_path = isolated_paths
    existing = sounds_dir / "rain.wav"
    existing.write_bytes(b"rain audio")
    config_path.write_text(json.dumps({str(existing): ["rain", "storm"]}))

    new_source = tmp_path / "thunder.wav"
    new_source.write_bytes(b"thunder audio")
    controller.selected_file_path = str(new_source)
    _set_keywords(controller, "thunder, storm")  # "storm" collides with the existing entry
    controller.loop_var.set(True)

    controller.save()

    assert len(no_real_dialogs["showwarning"]) == 1
    assert "storm" in no_real_dialogs["showwarning"][0][1]
    assert _read_config(config_path) == {str(existing): ["rain", "storm"]}  # unchanged
    assert not (sounds_dir / "thunder.wav").exists()  # nothing copied in


def test_save_blocks_duplicate_regardless_of_bang_prefix(controller, isolated_paths, no_real_dialogs, tmp_path):
    """A keyword collision must be detected even if one side is a one-shot ("!explosion")
    and the other is typed as a plain loop keyword - they resolve to the same trigger word
    at runtime in utils.load_keywords()."""
    sounds_dir, config_path = isolated_paths
    existing = sounds_dir / "boom.wav"
    existing.write_bytes(b"boom audio")
    config_path.write_text(json.dumps({str(existing): ["!explosion"]}))

    new_source = tmp_path / "crash.wav"
    new_source.write_bytes(b"crash audio")
    controller.selected_file_path = str(new_source)
    _set_keywords(controller, "explosion")
    controller.loop_var.set(True)  # loop this time, but same base keyword text

    controller.save()

    assert len(no_real_dialogs["showwarning"]) == 1
    assert _read_config(config_path) == {str(existing): ["!explosion"]}


def test_save_allows_editing_an_entry_to_keep_its_own_keyword(controller, isolated_paths):
    """Editing an entry and keeping (or re-typing) its own existing keyword must not be
    flagged as a conflict with itself."""
    sounds_dir, config_path = isolated_paths
    existing = sounds_dir / "rain.wav"
    existing.write_bytes(b"rain audio")
    config_path.write_text(json.dumps({str(existing): ["rain"]}))

    controller.load_asset_into_form(str(existing), ["rain"])
    _set_keywords(controller, "rain, storm")
    controller.loop_var.set(True)

    controller.save()

    assert _read_config(config_path) == {str(existing): ["rain", "storm"]}


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
