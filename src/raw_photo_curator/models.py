from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Metrics:
    sharpness: float
    exposure: float
    highlights: float
    shadows: float
    color: float
    composition: float


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

