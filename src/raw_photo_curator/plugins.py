from dataclasses import dataclass, field

from .criteria import AnalyzerPlugin, Availability, RuntimeEnvironment


@dataclass
class PluginRegistry:
    environment: RuntimeEnvironment = field(default_factory=RuntimeEnvironment)
    _plugins: dict[str, AnalyzerPlugin] = field(default_factory=dict)
    _enabled: set[str] = field(default_factory=set)

    def register(self, plugin: AnalyzerPlugin, enabled: bool = True) -> None:
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
                "version": plugin.version,
                "enabled": plugin.id in self._enabled,
                "availability": self.availability(plugin.id).status,
                "criteria": [criterion.id for criterion in plugin.criteria],
            }
            for plugin in self._plugins.values()
        ]
