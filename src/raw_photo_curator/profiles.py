from dataclasses import asdict, dataclass

from .models import Result

METRIC_IDS = (
    "sharpness",
    "exposure",
    "highlights",
    "shadows",
    "contrast",
    "noise",
    "color",
    "white_balance",
    "composition",
    "horizon",
    "edge_integrity",
)


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    weights: dict[str, float]
    hard_rules: dict[str, dict[str, float | str]]
    enabled_plugins: tuple[str, ...] = ("builtin.objective",)

    def to_dict(self) -> dict:
        return asdict(self)


BASE = {
    "sharpness": 0.22,
    "composition": 0.18,
    "exposure": 0.12,
    "highlights": 0.08,
    "shadows": 0.06,
    "contrast": 0.07,
    "noise": 0.07,
    "color": 0.05,
    "white_balance": 0.05,
    "horizon": 0.03,
    "edge_integrity": 0.03,
    "subject.saliency_concentration": 0.06,
    "subject.background_separation": 0.03,
    "depth.separation": 0.04,
    "timing.motion_clarity": 0.04,
    "aesthetic.embedding_score": 0.03,
}

BUILTIN_PROFILES = (
    Profile("travel", "Travel", BASE, {}),
    Profile(
        "portrait",
        "Portrait",
        {
            **BASE,
            "sharpness": 0.25,
            "composition": 0.21,
            "color": 0.08,
            "contrast": 0.04,
            "face.eye_focus": 0.12,
            "face.expression": 0.08,
        },
        {"face.blink": {"action": "reject", "threshold": 30.0}},
    ),
    Profile(
        "landscape",
        "Landscape",
        {**BASE, "composition": 0.23, "highlights": 0.12, "shadows": 0.10, "color": 0.08},
        {},
    ),
    Profile(
        "wildlife",
        "Wildlife",
        {**BASE, "sharpness": 0.31, "composition": 0.20, "exposure": 0.10, "noise": 0.10},
        {"sharpness": {"action": "reject", "threshold": 25.0}},
    ),
    Profile("custom", "Custom", BASE, {}),
)


def weighted_score(
    result: Result, profile: Profile, criteria: list[dict[str, object]] | None = None
) -> float:
    values = result.metrics.__dict__
    criterion_values = {
        str(item["id"]): float(item["score"]) * 100
        for item in criteria or []
        if item.get("score") is not None and float(item.get("confidence", 0)) >= 0.35
    }
    all_values = {**values, **criterion_values}
    usable = {
        key: weight for key, weight in profile.weights.items() if key in all_values and weight > 0
    }
    total = sum(usable.values())
    if not total:
        return result.keep_score
    return round(sum(all_values[key] * weight for key, weight in usable.items()) / total, 1)


def hard_rule_reasons(
    result: Result, profile: Profile, criteria: list[dict[str, object]] | None = None
) -> tuple[str, ...]:
    reasons = []
    criterion_values = {
        str(item["id"]): float(item["score"]) * 100
        for item in criteria or []
        if item.get("score") is not None and float(item.get("confidence", 0)) >= 0.5
    }
    values = {**result.metrics.__dict__, **criterion_values}
    for criterion, rule in profile.hard_rules.items():
        threshold = float(rule.get("threshold", 0))
        if rule.get("action") == "reject" and criterion in values and values[criterion] < threshold:
            reasons.append(f"{criterion} 低于硬规则阈值 {threshold:g}")
    return tuple(reasons)
