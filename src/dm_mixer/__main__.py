import sys
from dm_mixer.app import DMSoundApplication

def main():
    """Application package deployment script execution vector."""
    try:
        app = DMSoundApplication()
        app.run()
    except KeyboardInterrupt:
        print("\n🛑 Execution terminated via console interrupt.")
        sys.exit(0)

if __name__ == "__main__":
    main()
