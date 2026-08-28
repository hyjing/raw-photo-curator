import numpy as np

from .models import Metrics


def _clamp(value: float) -> float:
    # NumPy scalars otherwise leak into dataclasses and are not JSON serializable.
    return round(float(max(0.0, min(100.0, value))), 1)


def measure(rgb: np.ndarray) -> Metrics:
    gray = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    gx = np.abs(np.diff(gray, axis=1)).mean()
    gy = np.abs(np.diff(gray, axis=0)).mean()
    sharpness = _clamp((gx + gy) * 850)

    mean = float(gray.mean())
    exposure = _clamp(100 - abs(mean - 0.48) * 210)
    highlights = _clamp(100 * (1 - np.mean(gray > 0.98) * 7))
    shadows = _clamp(100 * (1 - np.mean(gray < 0.02) * 7))

    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    color = _clamp(45 + float(saturation.mean()) * 150 + float(saturation.std()) * 60)

    h, w = gray.shape
    center = gray[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4]
    border = np.concatenate((gray[: h // 10].ravel(), gray[-h // 10 :].ravel(), gray[:, : w // 10].ravel(), gray[:, -w // 10 :].ravel()))
    center_detail = float(np.std(center))
    border_noise = float(np.std(border))
    composition = _clamp(58 + center_detail * 120 - max(0.0, border_noise - center_detail) * 80)

    return Metrics(sharpness, exposure, highlights, shadows, color, composition)


def scores(m: Metrics, uniqueness: float = 100.0) -> tuple[float, float]:
    keep = (
        m.sharpness * 0.30
        + m.composition * 0.25
        + m.exposure * 0.15
        + ((m.highlights + m.shadows) / 2) * 0.15
        + m.color * 0.05
        + uniqueness * 0.10
    )
    edit = (
        m.highlights * 0.25
        + m.shadows * 0.25
        + m.sharpness * 0.20
        + m.composition * 0.15
        + m.color * 0.15
    )
    return _clamp(keep), _clamp(edit)


def explain(m: Metrics) -> tuple[str, ...]:
    notes: list[str] = []
    if m.sharpness >= 70:
        notes.append("细节清晰")
    elif m.sharpness < 35:
        notes.append("可能失焦或手抖")
    if m.highlights < 65:
        notes.append("高光存在明显溢出")
    if m.shadows < 65:
        notes.append("暗部存在明显剪裁")
    if m.exposure < 55:
        notes.append("整体曝光偏离中间调")
    if m.composition >= 70:
        notes.append("主体区域信息集中")
    if not notes:
        notes.append("技术指标较均衡")
    return tuple(notes)
