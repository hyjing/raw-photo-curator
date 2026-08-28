from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class CriterionKind(str, Enum):
    HARD_RULE = "hard_rule"
    SOFT_WEIGHT = "soft_weight"
    LEARNED_FEATURE = "learned_feature"


class CriterionCost(str, Enum):
    CHEAP = "cheap"
    MEDIUM = "medium"
    EXPENSIVE = "expensive"


@dataclass(frozen=True)
class CriterionDefinition:
    id: str
    label: str
    kind: CriterionKind
    value_type: str = "score"
    cost: CriterionCost = CriterionCost.CHEAP
    default_enabled: bool = True
    version: str = "1.0.0"


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    value: float | bool | str
    normalized_score: float | None
    confidence: float
    evidence: dict[str, object]
    analyzer_version: str


@dataclass(frozen=True)
class RuntimeEnvironment:
    network_allowed: bool = False
    device: str = "cpu"


@dataclass(frozen=True)
class Availability:
    available: bool
    status: str
    reason: str = ""


@dataclass(frozen=True)
class PluginManifest:
    name: str
    description: str
    download_size_mb: float
    runtime_cost: CriterionCost
    privacy: str = "完全本地，不联网"
    install_hint: str = ""


@dataclass(frozen=True)
class PhotoInput:
    path: Path


@dataclass(frozen=True)
class AnalysisContext:
    profile_id: str


class AnalyzerPlugin(Protocol):
    id: str
    version: str
    criteria: tuple[CriterionDefinition, ...]

    def available(self, environment: RuntimeEnvironment) -> Availability: ...

    def analyze(
        self, photo: PhotoInput, context: AnalysisContext
    ) -> list[CriterionResult]: ...
