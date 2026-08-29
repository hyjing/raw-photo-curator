from raw_photo_curator.criteria import AnalyzerPlugin, RuntimeEnvironment
from raw_photo_curator.example_plugin import ExampleAnalyzer
from raw_photo_curator.plugins import PluginRegistry
from raw_photo_curator.sdk import SDK_VERSION


def test_example_analyzer_is_protocol_compatible():
    plugin = ExampleAnalyzer()
    assert isinstance(plugin, AnalyzerPlugin)
    assert plugin.available(RuntimeEnvironment()).available
    assert SDK_VERSION == "1.0"


def test_registry_accepts_sdk_plugin():
    registry = PluginRegistry()
    registry.register(ExampleAnalyzer())
    assert registry.describe()[0]["id"] == "example.constant"
