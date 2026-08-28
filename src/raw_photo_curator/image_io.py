from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

SUPPORTED = {".arw", ".cr2", ".cr3", ".nef", ".raf", ".dng", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
RAW_EXTENSIONS = {".arw", ".cr2", ".cr3", ".nef", ".raf", ".dng"}


def discover(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)


def load_preview(path: Path, max_size: int = 1600) -> Image.Image:
    if path.suffix.lower() in RAW_EXTENSIONS:
        try:
            import rawpy
        except ImportError as exc:
            raise RuntimeError("读取 RAW 需要安装 rawpy：pip install rawpy") from exc
        with rawpy.imread(str(path)) as raw:
            try:
                thumbnail = raw.extract_thumb()
                if thumbnail.format == rawpy.ThumbFormat.JPEG:
                    image = Image.open(BytesIO(thumbnail.data)).convert("RGB")
                else:
                    image = Image.fromarray(thumbnail.data).convert("RGB")
            except rawpy.LibRawError:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    half_size=True,
                    no_auto_bright=True,
                    output_bps=8,
                )
                image = Image.fromarray(rgb, "RGB")
    else:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return image


def as_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image, dtype=np.float32) / 255.0
