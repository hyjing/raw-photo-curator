from dataclasses import dataclass, field
from importlib.metadata import entry_points

from .criteria import AnalyzerPlugin, Availability, RuntimeEnvironment
from .objective import BuiltinObjectivePlugin
from .optional_plugins import builtin_plugins


@dataclass
class PluginRegistry:
    environment: RuntimeEnvironment = field(default_factory=RuntimeEnvironment)
    _plugins: dict[str, AnalyzerPlugin] = field(default_factory=dict)
    _enabled: set[str] = field(default_factory=set)

    def register(self, plugin: AnalyzerPlugin, enabled: bool = True) -> None:
        if not isinstance(plugin, AnalyzerPlugin):
            raise TypeError("plugin does not implement AnalyzerPlugin")
        if plugin.id in self._plugins:
            raise ValueError(f"plugin already registered: {plugin.id}")
        self._plugins[plugin.id] = plugin
        if enabled:
            self._enabled.add(plugin.id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if plugin_id not in self._plugins:
            raise KeyError(plugin_id)
        if enabled:
            availability = self.availability(plugin_id)
            if not availability.available:
                raise RuntimeError(availability.reason or availability.status)
            self._enabled.add(plugin_id)
        else:
            self._enabled.discard(plugin_id)

    def availability(self, plugin_id: str) -> Availability:
        return self._plugins[plugin_id].available(self.environment)

    def enabled(self) -> tuple[AnalyzerPlugin, ...]:
        return tuple(
            plugin
            for plugin_id, plugin in self._plugins.items()
            if plugin_id in self._enabled and self.availability(plugin_id).available
        )

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "id": plugin.id,
                "name": getattr(getattr(plugin, "manifest", None), "name", plugin.id),
                "description": getattr(getattr(plugin, "manifest", None), "description", ""),
                "version": plugin.version,
                "enabled": plugin.id in self._enabled,
                "availability": self.availability(plugin.id).status,
                "unavailable_reason": self.availability(plugin.id).reason,
                "download_size_mb": getattr(
                    getattr(plugin, "manifest", None), "download_size_mb", 0
                ),
                "runtime_cost": getattr(
                    getattr(getattr(plugin, "manifest", None), "runtime_cost", None),
                    "value",
                    "cheap",
                ),
                "privacy": getattr(
                    getattr(plugin, "manifest", None), "privacy", "完全本地，不联网"
                ),
                "install_hint": getattr(
                    getattr(plugin, "manifest", None), "install_hint", ""
                ),
                "criteria": [criterion.id for criterion in plugin.criteria],
            }
            for plugin in self._plugins.values()
        ]


def default_registry(enabled: set[str] | None = None) -> PluginRegistry:
    if enabled is None:
        enabled = {"builtin.objective", "builtin.saliency", "builtin.timing-depth"}
    registry = PluginRegistry()
    for plugin in (BuiltinObjectivePlugin(), *builtin_plugins()):
        registry.register(plugin, plugin.id in enabled)
    for entry_point in entry_points(group="raw_photo_curator.analyzers"):
        try:
            plugin = entry_point.load()()
            registry.register(plugin, plugin.id in enabled)
        except (ImportError, TypeError, ValueError):
            continue
    return registry
