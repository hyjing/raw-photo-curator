from typing import Protocol

import numpy as np
from PIL import Image


class EmbeddingProvider(Protocol):
    id: str
    version: str
    dimensions: int

    def embed(self, image: Image.Image) -> tuple[float, ...]: ...


class ColorGridEmbedding:
    """Tiny deterministic visual descriptor; replaceable by a frozen model plugin."""

    id = "builtin.color-grid"
    version = "1.0.0"
    dimensions = 48

    def embed(self, image: Image.Image) -> tuple[float, ...]:
        array = np.asarray(image.convert("RGB").resize((4, 4)), dtype=np.float32) / 255.0
        return tuple(round(float(value), 5) for value in array.reshape(-1))


def cosine_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    a = np.asarray(left, dtype=np.float32)
    b = np.asarray(right, dtype=np.float32)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 1.0 if denominator == 0 else 1.0 - float(np.dot(a, b)) / denominator

