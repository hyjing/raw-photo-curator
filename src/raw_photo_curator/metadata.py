import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from .image_io import RAW_EXTENSIONS


@dataclass(frozen=True)
class PhotoMetadata:
    capture_time: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens: str | None = None
    focal_length: float | None = None
    aperture: float | None = None
    shutter_speed: float | None = None
    iso: int | None = None
    sequence: int | None = None
    raw_highlight_headroom: float | None = None
    raw_shadow_recovery: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _sequence(path: Path) -> int | None:
    match = re.search(r"(\d+)(?!.*\d)", path.stem)
    return int(match.group(1)) if match else None


def metadata_from_image(image: Image.Image, path: Path) -> PhotoMetadata:
    exif = image.getexif()
    detail = exif.get_ifd(34665) if exif and 34665 in exif else {}
    captured = exif.get(36867) or detail.get(36867) or exif.get(306)
    capture_time = None
    if captured:
        try:
            capture_time = datetime.strptime(
                str(captured), "%Y:%m:%d %H:%M:%S"
            ).replace(tzinfo=UTC).isoformat()
        except ValueError:
            capture_time = str(captured)
    iso_value = detail.get(34855)
    return PhotoMetadata(
        capture_time=capture_time,
        camera_make=str(exif.get(271)).strip() if exif.get(271) else None,
        camera_model=str(exif.get(272)).strip() if exif.get(272) else None,
        lens=str(detail.get(42036)).strip() if detail.get(42036) else None,
        focal_length=_number(detail.get(37386)),
        aperture=_number(detail.get(33437)),
        shutter_speed=_number(detail.get(33434)),
        iso=int(iso_value) if iso_value is not None else None,
        sequence=_sequence(path),
    )


def extract_raw_metrics(path: Path) -> tuple[float, float]:
    import rawpy

    with rawpy.imread(str(path)) as raw:
        visible = raw.raw_image_visible[::8, ::8].astype("float32")
        black = float(sum(raw.black_level_per_channel) / 4)
        white = float(raw.white_level)
        normalized = (visible - black) / max(1.0, white - black)
        highlight = max(0.0, round(float(100 * (1 - (normalized >= 0.995).mean() * 8)), 1))
        shadow = max(0.0, round(float(100 * (1 - (normalized <= 0.01).mean() * 5)), 1))
        return highlight, shadow


def extract_metadata(path: Path, include_raw_metrics: bool = False) -> PhotoMetadata:
    if path.suffix.lower() in RAW_EXTENSIONS:
        import rawpy

        with rawpy.imread(str(path)) as raw:
            highlight_headroom = shadow_recovery = None
            if include_raw_metrics:
                visible = raw.raw_image_visible[::8, ::8].astype("float32")
                black = float(sum(raw.black_level_per_channel) / 4)
                white = float(raw.white_level)
                normalized = (visible - black) / max(1.0, white - black)
                highlight_headroom = max(
                    0.0, round(float(100 * (1 - (normalized >= 0.995).mean() * 8)), 1)
                )
                shadow_recovery = max(
                    0.0, round(float(100 * (1 - (normalized <= 0.01).mean() * 5)), 1)
                )
            thumbnail = raw.extract_thumb()
            if thumbnail.format != rawpy.ThumbFormat.JPEG:
                return PhotoMetadata(
                    sequence=_sequence(path),
                    raw_highlight_headroom=highlight_headroom,
                    raw_shadow_recovery=shadow_recovery,
                )
            with Image.open(BytesIO(thumbnail.data)) as image:
                metadata = metadata_from_image(image, path)
                return replace(
                    metadata,
                    raw_highlight_headroom=highlight_headroom,
                    raw_shadow_recovery=shadow_recovery,
                )
    with Image.open(path) as image:
        return metadata_from_image(image, path)


def perceptual_hash(image: Image.Image) -> str:
    gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(gray.get_flattened_data())
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | (
                pixels[row * 9 + column] > pixels[row * 9 + column + 1]
            )
    return f"{bits:016x}"


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()
