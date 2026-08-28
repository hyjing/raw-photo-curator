from pathlib import Path

from raw_photo_curator.models import Metrics, Result
from raw_photo_curator.profiles import Profile, hard_rule_reasons, weighted_score
from raw_photo_curator.server import _active_profile, _connect, _profiles


def result_with(**overrides: float) -> Result:
    values = {
        "sharpness": 50,
        "exposure": 50,
        "highlights": 50,
        "shadows": 50,
        "contrast": 50,
        "noise": 50,
        "color": 50,
        "white_balance": 50,
        "composition": 50,
    }
    values.update(overrides)
    return Result(Path("photo.arw"), 50, 50, Metrics(**values), (), "photo.jpg")


def test_profile_weight_and_hard_rule_are_applied():
    profile = Profile(
        "test",
        "Test",
        {"sharpness": 3, "composition": 1},
        {"sharpness": {"action": "reject", "threshold": 30}},
    )
    assert weighted_score(result_with(sharpness=100, composition=0), profile) == 75
    assert hard_rule_reasons(result_with(sharpness=20), profile)
    assert not hard_rule_reasons(result_with(sharpness=40), profile)


def test_builtin_profiles_are_persisted_and_active_profile_changes(tmp_path: Path):
    database = tmp_path / "feedback.sqlite3"
    _connect(database).close()
    assert {profile.id for profile in _profiles(database)} >= {
        "travel",
        "portrait",
        "landscape",
        "wildlife",
        "custom",
    }
    with _connect(database) as connection:
        connection.execute(
            "UPDATE app_settings SET value = 'landscape' WHERE key = 'active_profile'"
        )
    assert _active_profile(database).id == "landscape"
    custom = next(profile for profile in _profiles(database) if profile.id == "custom")
    assert "subject.saliency_concentration" in custom.weights
    assert "horizon" in custom.weights
