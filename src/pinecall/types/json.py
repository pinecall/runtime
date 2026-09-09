"""The one alias every layer shares: what a decoded JSON object looks like."""

from typing import Any

# What crosses the wire as an event's `data`: decoded JSON, keys are strings.
type JsonObject = dict[str, Any]
