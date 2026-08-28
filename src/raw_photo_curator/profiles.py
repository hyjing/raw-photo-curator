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
        },
        {},
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


def weighted_score(result: Result, profile: Profile) -> float:
    values = result.metrics.__dict__
    usable = {
        key: weight
        for key, weight in profile.weights.items()
        if key in values and weight > 0
    }
    total = sum(usable.values())
    if not total:
        return result.keep_score
    return round(sum(values[key] * weight for key, weight in usable.items()) / total, 1)


def hard_rule_reasons(result: Result, profile: Profile) -> tuple[str, ...]:
    reasons = []
    values = result.metrics.__dict__
    for criterion, rule in profile.hard_rules.items():
        threshold = float(rule.get("threshold", 0))
        if rule.get("action") == "reject" and criterion in values and values[criterion] < threshold:
            reasons.append(f"{criterion} 低于硬规则阈值 {threshold:g}")
    return tuple(reasons)
