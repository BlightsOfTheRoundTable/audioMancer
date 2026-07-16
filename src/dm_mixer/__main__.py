import sys
from multiprocessing import freeze_support
from dm_mixer.app import DMSoundApplication

def main():
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