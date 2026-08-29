from pathlib import Path

from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
source = Image.open(root / "docs/assets/top5.png").convert("RGB")
frames = []
for index in range(12):
    progress = index / 11
    crop_width = int(source.width * (1 - 0.08 * progress))
    crop_height = int(source.height * (1 - 0.08 * progress))
    left = int((source.width - crop_width) * 0.35)
    top = int((source.height - crop_height) * 0.55)
    frame = source.crop((left, top, left + crop_width, top + crop_height)).resize(
        (source.width, source.height), Image.Resampling.LANCZOS
    )
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((24, 22, 330, 58), radius=10, fill=(5, 8, 12, 210))
    draw.text((40, 32), "355 Sony ARW · local cache · Top 5", fill=(124, 243, 173))
    frames.append(frame.quantize(colors=128))
frames[0].save(
    root / "docs/assets/demo.gif", save_all=True, append_images=frames[1:],
    duration=140, loop=0, optimize=True,
)
