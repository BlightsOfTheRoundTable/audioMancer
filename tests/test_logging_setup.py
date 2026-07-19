import sys

from dm_mixer import logging_setup


def test_no_op_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    logging_setup.setup_frozen_stdio()

    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr


def test_redirects_to_log_file_when_frozen_and_stdio_is_none(tmp_path, monkeypatch):
    """The core scenario this module exists for: a --windowed PyInstaller build has no
    console, so bare print() calls elsewhere in the app would otherwise crash."""
    log_dir = tmp_path / "logs"
    log_file = log_dir / "dm-mixer.log"
    monkeypatch.setattr(logging_setup, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(logging_setup, "LOG_FILE", str(log_file))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    logging_setup.setup_frozen_stdio()

    assert log_dir.is_dir()
    assert sys.stdout is not None
    assert sys.stdout is sys.stderr  # both streams share the one log file
    sys.stdout.close()
    assert "session started" in log_file.read_text()


def test_redirects_even_when_stdio_is_not_none(tmp_path, monkeypatch):
    """Regression test: a real frozen --windowed build was observed NOT to expose sys.stdout
    as a plain None (contrary to the initial assumption). Gating the redirect on
    "sys.stdout is None" silently no-opped in that case, leaving the original stream in
    place - which then choked with UnicodeEncodeError on the app's first emoji print,
    crashing the whole app with no visible error and no log file. The redirect must apply
    unconditionally whenever frozen, regardless of what sys.stdout currently is."""
    log_dir = tmp_path / "logs"
    log_file = log_dir / "dm-mixer.log"
    monkeypatch.setattr(logging_setup, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(logging_setup, "LOG_FILE", str(log_file))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    logging_setup.setup_frozen_stdio()

    assert sys.stdout is not original_stdout
    assert sys.stderr is not original_stderr
    assert sys.stdout is sys.stderr
    sys.stdout.close()
