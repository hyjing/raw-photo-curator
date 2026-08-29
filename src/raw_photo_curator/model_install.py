from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

from .optional_plugins import expression_model_path, face_model_path, nima_model_path

NIMA_URL = (
    "https://huggingface.co/cromsc/nima-mobilenet-aesthetic/resolve/"
    "9884a024f1f186109a52a111a99733f5b281e6d8/nima_mobilenet_aesthetic.onnx"
)
NIMA_SHA256 = "c58b0c39b5b8f752b1b0ebf10e07e48406780ce3bf9d4647f8c43898748fe69c"
FACE_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    "47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
FACE_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
EXPRESSION_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    "47534e27c9851bb1128ccc0102f1145e27f23f98/models/facial_expression_recognition/"
    "facial_expression_recognition_mobilefacenet_2022july.onnx"
)
EXPRESSION_SHA256 = "4f61307602fc089ce20488a31d4e4614e3c9753a7d6c41578c854858b183e1a9"


def install_model(model: str, destination: Path | None = None) -> Path:
    if model == "aesthetic":
        target, url, expected = destination or nima_model_path(), NIMA_URL, NIMA_SHA256
    elif model == "face":
        target, url, expected = destination or face_model_path(), FACE_URL, FACE_SHA256
    else:
        raise ValueError(f"unknown model: {model}")
    _download_verified(url, target, expected)
    if model == "face" and destination is None:
        _download_verified(EXPRESSION_URL, expression_model_path(), EXPRESSION_SHA256)
    return target


def _download_verified(url: str, target: Path, expected: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        urllib.request.urlretrieve(url, temporary)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if digest != expected:
            raise RuntimeError(f"model checksum mismatch: {target.name}")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def install_nima_model(destination: Path | None = None) -> Path:
    return install_model("aesthetic", destination)
