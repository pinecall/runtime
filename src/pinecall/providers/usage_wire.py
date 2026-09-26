"""livekit's own usage rows as this wire carries them: one conversion, for everything priced."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from livekit.agents.metrics.usage import (
    EOTModelUsage,
    InterruptionModelUsage,
    LLMModelUsage,
    ModelUsage,
    STTModelUsage,
    TTSModelUsage,
)

from pinecall_protocol import WireModel
from pinecall_protocol import metrics as wire

# One wire model per row class, by the tag ours gives it. livekit's rows already declare our field
# names, so a row is dumped and revalidated and nothing here adds a token up. Three readers meet
# here — the voice call's summary, the text call's, and the judge's bill on call.score — because
# three copies of a conversion is where two of them start disagreeing about a field.
ROWS: dict[type[Any], type[WireModel]] = {
    LLMModelUsage: wire.LLMModelUsage,
    TTSModelUsage: wire.TTSModelUsage,
    STTModelUsage: wire.STTModelUsage,
    InterruptionModelUsage: wire.InterruptionModelUsage,
    EOTModelUsage: wire.EOTModelUsage,
}


def as_wire_rows(usage: Sequence[ModelUsage]) -> list[wire.ModelUsage]:
    """Every row this wire has a shape for, in the order livekit summed them. Unchanged."""
    said: list[wire.ModelUsage] = []
    for row in usage:
        model = ROWS.get(type(row))
        if model is not None:
            said.append(cast("wire.ModelUsage", model.model_validate(row.model_dump())))
    return said
