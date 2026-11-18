"""Deterministic preview-only icons. Requires Pillow. No proprietary assets."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "src-tauri" / "icons"
out.mkdir(parents=True, exist_ok=True)
size = 1024
image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((60, 60, 964, 964), radius=225, fill="#123A55")
draw.rounded_rectangle((105, 105, 919, 919), radius=185, outline="#4F91A8", width=17)
draw.line((257, 400, 767, 400), fill="#D8F1FB", width=57)
draw.line((335, 535, 685, 535), fill="#D8F1FB", width=57)
draw.line((407, 670, 615, 670), fill="#D8F1FB", width=57)
for px, name in [(32, "32x32.png"), (128, "128x128.png"), (256, "128x128@2x.png")]:
    image.resize((px, px), Image.Resampling.LANCZOS).save(out / name, "PNG")
image.save(out / "icon.ico", format="ICO", sizes=[(16, 16), (24, 24), (32, 32),
                                                    (48, 48), (64, 64), (128, 128), (256, 256)])
image.save(out / "icon.icns", format="ICNS")
print("Generated cross-platform preview icons in", out)
