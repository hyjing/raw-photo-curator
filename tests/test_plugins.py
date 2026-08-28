from raw_photo_curator.criteria import (
    AnalysisContext,
    Availability,
    CriterionDefinition,
    CriterionKind,
    PhotoInput,
    RuntimeEnvironment,
)
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
