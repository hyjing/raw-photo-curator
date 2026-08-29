from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from raw_photo_curator import model_install
from raw_photo_curator.catalog import Catalog
from raw_photo_curator.criteria import (
    AnalysisContext,
    Availability,
    CriterionDefinition,
    CriterionKind,
    PhotoInput,
    RuntimeEnvironment,
)
from raw_photo_curator.optional_plugins import (
    FaceEyesPlugin,
    NimaAestheticPlugin,
    SaliencyPlugin,
    TimingDepthPlugin,
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


def test_face_plugin_returns_unknown_instead_of_fake_scores_without_face():
    pytest.importorskip("cv2")

    class Detector:
        def setInputSize(self, size):
            return None

        def detect(self, image):
            return None, None

    plugin = FaceEyesPlugin()
    plugin._face_detector = Detector()
    plugin._cascades = None
    results = plugin.analyze_image(Image.new("RGB", (120, 80), "navy"))
    assert len(results) == 3
    assert all(result.normalized_score is None for result in results)
    assert all(result.value == "unknown" for result in results)


def test_nima_plugin_converts_distribution_to_explainable_score(tmp_path: Path):
    class Input:
        name = "input"

    class Session:
        def get_inputs(self):
            return [Input()]

        def run(self, outputs, inputs):
            distribution = [0.0] * 10
            distribution[6] = 1.0
            return [np.asarray([distribution], dtype=np.float32)]

    plugin = NimaAestheticPlugin(tmp_path / "model.onnx")
    plugin._session = Session()
    result = plugin.analyze_image(Image.new("RGB", (320, 200), "orange"))[0]
    assert result.value == 7.0
    assert result.normalized_score == pytest.approx(2 / 3)
    assert result.evidence["distribution"][6] == 1.0
    assert result.evidence["warning"] == "generic_prior_not_personal_taste"


def test_model_download_rejects_checksum_mismatch(tmp_path: Path, monkeypatch):
    def fake_download(url, target):
        Path(target).write_bytes(b"not the expected model")

    monkeypatch.setattr(model_install.urllib.request, "urlretrieve", fake_download)
    target = tmp_path / "model.onnx"
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        model_install.install_model("aesthetic", target)
    assert not target.exists()
    assert not target.with_suffix(".onnx.part").exists()
