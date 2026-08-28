from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Metrics:
    sharpness: float
    exposure: float
    highlights: float
    shadows: float
    contrast: float
    noise: float
    color: float
    white_balance: float
    composition: float
    horizon: float = 50.0
    edge_integrity: float = 50.0


@dataclass(frozen=True)
class Result:
    path: Path
    keep_score: float
    edit_score: float
    metrics: Metrics
    notes: tuple[str, ...]
    thumbnail: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["path"] = str(self.path)
        return data
