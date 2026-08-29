from raw_photo_curator.sdk import (
    AnalysisContext,
    Availability,
    CriterionCost,
    CriterionDefinition,
    CriterionKind,
    CriterionResult,
    PhotoInput,
    PluginManifest,
    RuntimeEnvironment,
)


class ExampleAnalyzer:
    id = "example.constant"
    version = "1.0.0"
    manifest = PluginManifest(
        "Example analyzer", "Minimal SDK-compatible analyzer.", 0, CriterionCost.CHEAP
    )
    criteria = (
        CriterionDefinition("example.constant", "Example score", CriterionKind.SOFT_WEIGHT),
    )

    def available(self, environment: RuntimeEnvironment) -> Availability:
        return Availability(True, "ready")

    def analyze(self, photo: PhotoInput, context: AnalysisContext) -> list[CriterionResult]:
        return [CriterionResult("example.constant", 50, 50, 1.0, {"source": "example"}, self.version)]
