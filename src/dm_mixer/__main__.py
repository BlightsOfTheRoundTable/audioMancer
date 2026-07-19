import sys
from multiprocessing import freeze_support
from dm_mixer.logging_setup import setup_frozen_stdio
from dm_mixer.app import DMSoundApplication

def main():
    # Must run before anything prints - a frozen --windowed build has no console, and
    # sys.stdout/sys.stderr are None rather than a null stream in that case.
    setup_frozen_stdio()
    # FIX: Lock process generation for Windows platforms
    freeze_support()
    try:
        app = DMSoundApplication()
        app.run()
    except KeyboardInterrupt:
        print("\n🛑 Execution terminated via console interrupt.")
        sys.exit(0)

if __name__ == "__main__":
    main()