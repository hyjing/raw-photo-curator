"""Stable public surface for third-party analyzer plugins."""

from .criteria import (
    AnalysisContext,
    AnalyzerPlugin,
    Availability,
    CriterionCost,
    CriterionDefinition,
    CriterionKind,
    CriterionResult,
    PhotoInput,
    PluginManifest,
    RuntimeEnvironment,
)

SDK_VERSION = "1.0"

__all__ = [
    "SDK_VERSION", "AnalysisContext", "AnalyzerPlugin", "Availability",
    "CriterionCost", "CriterionDefinition", "CriterionKind", "CriterionResult",
    "PhotoInput", "PluginManifest", "RuntimeEnvironment",
]
