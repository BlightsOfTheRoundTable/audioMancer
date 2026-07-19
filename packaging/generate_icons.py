"""One-off script: generates a placeholder app icon and converts it to the platform-specific
formats PyInstaller needs. Run manually whenever branding changes:

    uv run --group build python packaging/generate_icons.py

If packaging/icon_source.png doesn't exist yet, this draws a simple placeholder and saves it
there first. Swap that file for real branding art before a public release, then re-run this
script to regenerate the .ico/.icns from it.
"""

from pathlib import Path

from PIL import Image, ImageDraw

PACKAGING_DIR = Path(__file__).parent
SOURCE_PNG = PACKAGING_DIR / "icon_source.png"
WINDOWS_ICO = PACKAGING_DIR / "windows" / "icon.ico"
MACOS_ICNS = PACKAGING_DIR / "macos" / "icon.icns"

ICON_SIZE = 1024
BACKGROUND = (30, 30, 46, 255)  # dark slate, matches the app's dark UI theme
ACCENT = (92, 184, 92, 255)  # matches app.py's slider activebackground green


def draw_placeholder_source():
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    margin = ICON_SIZE // 10
    draw.rounded_rectangle(
        [margin, margin, ICON_SIZE - margin, ICON_SIZE - margin],
        radius=ICON_SIZE // 6, fill=BACKGROUND,
    )

    # A simple sound-wave glyph: three bars of varying height, centered.
    bar_width = ICON_SIZE // 10
    gap = bar_width // 2
    heights = [0.35, 0.65, 0.45]
    total_width = bar_width * len(heights) + gap * (len(heights) - 1)
    start_x = (ICON_SIZE - total_width) // 2
    for i, h in enumerate(heights):
        bar_height = int(ICON_SIZE * h)
        x0 = start_x + i * (bar_width + gap)
        x1 = x0 + bar_width
        y0 = (ICON_SIZE - bar_height) // 2
        y1 = y0 + bar_height
        draw.rounded_rectangle([x0, y0, x1, y1], radius=bar_width // 2, fill=ACCENT)

    return image


def main():
    WINDOWS_ICO.parent.mkdir(parents=True, exist_ok=True)
    MACOS_ICNS.parent.mkdir(parents=True, exist_ok=True)

    if SOURCE_PNG.exists():
        source = Image.open(SOURCE_PNG).convert("RGBA")
        print(f"Using existing source art: {SOURCE_PNG}")
    else:
        source = draw_placeholder_source()
        source.save(SOURCE_PNG)
        print(f"No source art found - generated a placeholder at {SOURCE_PNG}")
        print("Swap this file for real branding art before a public release, then re-run this script.")

    source.save(WINDOWS_ICO, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    print(f"Wrote {WINDOWS_ICO}")

    source.save(MACOS_ICNS, format="ICNS")
    print(f"Wrote {MACOS_ICNS}")


if __name__ == "__main__":
    main()
