"""Experimental group affinity: collection, optional local model and scoring."""

from .service import (
    AffinityService, allowed_group, component_kind, directed_sentiment, enabled, settings, snapshot, value,
)

__all__ = [
    "AffinityService", "allowed_group", "component_kind", "directed_sentiment", "enabled", "settings", "snapshot", "value",
]
