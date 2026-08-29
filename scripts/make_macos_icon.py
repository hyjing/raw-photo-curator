from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
ICONSET = BUILD / "RAWPhotoCurator.iconset"


def artwork(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (40, 40, size - 40, size - 40), radius=220, fill=255
    )
    background = Image.new("RGBA", image.size)
    pixels = background.load()
    for y in range(size):
        for x in range(size):
            mix = (x + y) / (2 * size)
            pixels[x, y] = (
                int(20 + 35 * mix),
                int(30 + 75 * mix),
                int(45 + 80 * mix),
                255,
            )
    image.paste(background, mask=mask)
    draw = ImageDraw.Draw(image)
    mint = (124, 243, 173, 255)
    blue = (88, 190, 255, 255)
    draw.ellipse((205, 205, 819, 819), outline=mint, width=54)
    draw.ellipse((330, 330, 694, 694), outline=blue, width=36)
    draw.line((395, 720, 395, 315, 565, 315), fill=(245, 248, 251, 255), width=72)
    draw.arc((425, 300, 700, 570), -90, 90, fill=(245, 248, 251, 255), width=72)
    draw.line((535, 540, 700, 725), fill=(245, 248, 251, 255), width=72)
    return image


def main() -> None:
    ICONSET.mkdir(parents=True, exist_ok=True)
    source = artwork()
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = points * scale
            suffix = "@2x" if scale == 2 else ""
            target = ICONSET / f"icon_{points}x{points}{suffix}.png"
            source.resize((pixels, pixels), Image.Resampling.LANCZOS).save(target)
    source.save(BUILD / "RAWPhotoCurator.icns", format="ICNS")


if __name__ == "__main__":
    main()
