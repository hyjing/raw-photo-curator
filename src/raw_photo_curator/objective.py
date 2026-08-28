from dataclasses import asdict

from .criteria import (
    AnalysisContext,
    Availability,
    CriterionCost,
    CriterionDefinition,
    CriterionKind,
    CriterionResult,
    PhotoInput,
    RuntimeEnvironment,
)
from .metadata import PhotoMetadata
from .models import Result

METRIC_LABELS = {
    "sharpness": "清晰度",
    "exposure": "曝光",
    "highlights": "高光保留",
    "shadows": "阴影保留",
    "contrast": "对比度",
    "noise": "噪声控制",
    "color": "色彩信息",
    "white_balance": "白平衡",
    "composition": "构图代理",
    "horizon": "地平线",
    "edge_integrity": "边缘完整性",
}


class BuiltinObjectivePlugin:
    id = "builtin.objective"
    version = "4.0.0"
    criteria = tuple(
        CriterionDefinition(
            f"objective.{key}", label, CriterionKind.SOFT_WEIGHT, cost=CriterionCost.CHEAP
        )
        for key, label in METRIC_LABELS.items()
    ) + (
        CriterionDefinition(
            "raw.highlight_headroom",
            "RAW 高光余量",
            CriterionKind.SOFT_WEIGHT,
            cost=CriterionCost.MEDIUM,
        ),
        CriterionDefinition(
            "raw.shadow_recovery",
            "RAW 暗部恢复",
            CriterionKind.SOFT_WEIGHT,
            cost=CriterionCost.MEDIUM,
        ),
    )

    def available(self, environment: RuntimeEnvironment) -> Availability:
        return Availability(True, "ready")

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list[CriterionResult]:
        raise RuntimeError("use analyze_result after the shared preview pipeline")

    def analyze_result(
        self, result: Result, metadata: PhotoMetadata
    ) -> list[CriterionResult]:
        output = []
        metric_values = asdict(result.metrics)
        for key in METRIC_LABELS:
            value = float(metric_values[key])
            confidence = 0.7 if key in {"composition", "horizon", "edge_integrity"} else 0.9
            output.append(
                CriterionResult(
                    f"objective.{key}",
                    value,
                    value / 100,
                    confidence,
                    {"source": "embedded_preview", "method": key},
                    self.version,
                )
            )
        for criterion_id, value in (
            ("raw.highlight_headroom", metadata.raw_highlight_headroom),
            ("raw.shadow_recovery", metadata.raw_shadow_recovery),
        ):
            output.append(
                CriterionResult(
                    criterion_id,
                    value if value is not None else "unknown",
                    value / 100 if value is not None else None,
                    0.95 if value is not None else 0.0,
                    {
                        "source": "raw_linear_samples" if value is not None else "unavailable",
                        "warning": None if value is not None else "RAW linear data unavailable",
                    },
                    self.version,
                )
            )
        return output
