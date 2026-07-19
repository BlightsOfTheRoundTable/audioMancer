import os
import sys
from datetime import datetime

from dm_mixer.utils import USER_DATA_DIR

LOG_DIR = os.path.join(USER_DATA_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "dm-mixer.log")


def setup_frozen_stdio():
    """A frozen --windowed build has no console for the user to ever read, and this codebase
    prints emoji unconditionally at startup - Windows' legacy console codepage (cp1252) can't
    encode them, so whatever sys.stdout/sys.stderr actually are in a windowed build (not
    reliably a plain None across PyInstaller versions/configurations - confirmed via a real
    build: gating this on "sys.stdout is None" silently no-opped and let the original
    UnicodeEncodeError crash kill the app on the very first print with no visible error at
    all) will choke on the first emoji. Always redirect both streams to a UTF-8 log file
    whenever frozen, rather than trying to detect whether the existing stream is usable -
    this also gives a single reliable place to look when diagnosing a user's bug report with
    no console available."""
    if not getattr(sys, "frozen", False):
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    log_stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    log_stream.write(f"\n----- session started {datetime.now().isoformat(timespec='seconds')} -----\n")
    sys.stdout = log_stream
    sys.stderr = log_stream
