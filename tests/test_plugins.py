from pathlib import Path

from PIL import Image

from raw_photo_curator.catalog import Catalog
from raw_photo_curator.criteria import (
    AnalysisContext,
    Availability,
    CriterionDefinition,
    CriterionKind,
    PhotoInput,
    RuntimeEnvironment,
)
from raw_photo_curator.optional_plugins import SaliencyPlugin, TimingDepthPlugin
from raw_photo_curator.plugins import PluginRegistry


class FakePlugin:
    id = "test.fake"
    version = "1.0.0"
    criteria = (CriterionDefinition("test.score", "Test", CriterionKind.SOFT_WEIGHT),)

    def available(self, environment: RuntimeEnvironment) -> Availability:
        return Availability(environment.device == "cpu", "ready")

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list:
        return []


def test_plugins_can_be_registered_and_disabled():
    registry = PluginRegistry()
    registry.register(FakePlugin())
    assert [plugin.id for plugin in registry.enabled()] == ["test.fake"]
    registry.set_enabled("test.fake", False)
    assert registry.enabled() == ()
    registry.set_enabled("test.fake", True)
    assert registry.describe()[0]["criteria"] == ["test.score"]


def test_zero_download_preview_plugins_return_confidence_and_evidence():
    image = Image.new("RGB", (80, 60), (40, 90, 140))
    results = SaliencyPlugin().analyze_image(image) + TimingDepthPlugin().analyze_image(image)
    assert {result.criterion_id for result in results} == {
        "subject.saliency_concentration",
        "subject.background_separation",
        "depth.separation",
        "timing.motion_clarity",
    }
    assert all(0 <= result.confidence <= 1 for result in results)
    assert all(result.evidence for result in results)


def test_plugin_settings_are_local_and_persistent(tmp_path: Path):
    database = tmp_path / "catalog.sqlite3"
    with Catalog(database) as catalog:
        catalog.set_plugin_enabled("builtin.saliency", False)
    with Catalog(database) as catalog:
        assert catalog.plugin_settings() == {"builtin.saliency": False}
