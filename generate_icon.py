"""
Generate icon.ico from cartoon_dock_icon_transparent_2048.png.
Passes the full-res source to Pillow and lets it downsample to each ICO size.
"""
from pathlib import Path
from PIL import Image

for candidate in ("cartoon_dock_icon_transparent_2048.png", "icon_v2.png"):
    src = Path(candidate)
    if src.exists():
        break

img = Image.open(src).convert("RGBA")

sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save("icon.ico", format="ICO", sizes=sizes)

# Verify
verify = Image.open("icon.ico")
print(f"icon.ico saved — source: {src.name}  frames: {verify.info.get('sizes')}")
