from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/assets/top5.png"
OUTPUT = ROOT / "docs/assets/social-preview.png"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    return ImageFont.truetype(f"/System/Library/Fonts/Supplemental/{name}", size)


def main() -> None:
    width, height = 1280, 640
    source = Image.open(SOURCE).convert("RGB")
    scale = max(width / source.width, height / source.height)
    resized = source.resize(
        (round(source.width * scale), round(source.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    canvas = resized.crop((left, 0, left + width, height)).filter(ImageFilter.GaussianBlur(1.5))
    canvas = ImageEnhance.Brightness(canvas).enhance(0.28).convert("RGBA")
    overlay = Image.new("RGBA", (width, height), (5, 8, 12, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, 800, height), fill=(5, 8, 12, 232))
    for index in range(240):
        alpha = round(232 * (1 - index / 240))
        draw.line((800 + index, 0, 800 + index, height), fill=(5, 8, 12, alpha))
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((72, 68, 142, 138), radius=20, fill=(124, 243, 173, 255))
    draw.text((96, 79), "R", font=font(38, True), fill=(5, 16, 11, 255))
    draw.text((72, 178), "RAW Photo Curator", font=font(57, True), fill="white")
    draw.text((72, 255), "Offline, personalized RAW photo culling", font=font(30), fill=(202, 211, 221))
    draw.text((72, 326), "Sony ARW  ·  Explainable ranking  ·  Lightroom XMP", font=font(22), fill=(124, 243, 173))
    draw.text((72, 508), "Open source  •  Local-first  •  Your photos never leave your Mac", font=font(20), fill=(166, 177, 190))
    canvas.convert("RGB").save(OUTPUT, format="PNG", optimize=True)


if __name__ == "__main__":
    main()
