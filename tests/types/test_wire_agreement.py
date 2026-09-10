"""The types and the wire agree by name: a closed set here is the closed set there."""

from dataclasses import fields
from typing import Any, get_args

import pytest

from pinecall.types import (
    QUOTAS,
    AgentConfig,
    Contact,
    Docs,
    KnowledgeFile,
    MarkerName,
    MemoryPolicy,
    Model,
    PromptBlock,
    PromptRegion,
    Route,
    ToolSpec,
    Turn,
    Voice,
)
from pinecall.types.channel import CHANNELS, DIRECTIONS
from pinecall.types.knowledge import DocsMode
from pinecall_protocol import WireModel, defs, events

pytestmark = pytest.mark.unit

# Ours on the left, the generated wire shape on the right.
TWINS: list[tuple[type[Any], type[WireModel]]] = [
    (AgentConfig, defs.AgentConfig),
    (ToolSpec, defs.ToolSpec),
    (Route, defs.Route),
    (Contact, defs.Contact),
    (Voice, defs.VoiceConfig),
    (Model, defs.ModelConfig),
    (Turn, defs.TurnConfig),
    (PromptBlock, defs.PromptBlockSpec),
    (KnowledgeFile, defs.KnowledgeFile),
    (Docs, defs.DocsConfig),
    (MemoryPolicy, defs.MemoryConfig),
]


# The one wire field with no twin here, and the reason it has none: `voice.name` is the word the app
# wrote — a curated name or a vendor's id — and the gateway resolves it to a provider and an id
# before the declaration becomes an AgentConfig. A shape that kept the name would be a name that
# could still reach a vendor, which is the call this rule was written after.
RESOLVED_AT_THE_EDGE: dict[type[WireModel], frozenset[str]] = {
    defs.VoiceConfig: frozenset({"name"})
}


def test_the_channels_and_directions_here_are_the_wires() -> None:
    assert CHANNELS == set(get_args(defs.Channel.__value__))
    assert DIRECTIONS == set(get_args(defs.Direction.__value__))


def test_the_quota_names_here_are_the_ones_a_refusal_may_say() -> None:
    """A quota this runtime can refuse for has to be a word credits.exhausted is allowed to say."""
    said = events.CreditsExhausted.model_fields["quota"].annotation
    assert set(QUOTAS) == set(get_args(said))


def test_the_two_prompt_regions_here_are_the_wires() -> None:
    assert get_args(PromptRegion.__value__) == get_args(defs.PromptRegion.__value__)


def test_the_three_marker_names_and_the_two_docs_modes_here_are_the_wires() -> None:
    assert get_args(MarkerName.__value__) == get_args(defs.MarkerName.__value__)
    assert get_args(DocsMode.__value__) == get_args(defs.DocsMode.__value__)


@pytest.mark.parametrize(("ours", "theirs"), TWINS, ids=[ours.__name__ for ours, _ in TWINS])
def test_every_wire_field_has_a_field_here_of_the_same_name(
    ours: type[Any], theirs: type[WireModel]
) -> None:
    """The wire is what an app may say; the runtime may know more, never less."""
    missing = (
        set(theirs.model_fields)
        - {declared.name for declared in fields(ours)}
        - RESOLVED_AT_THE_EDGE.get(theirs, frozenset())
    )
    assert not missing, f"{ours.__name__} lacks {sorted(missing)}, which {theirs.__name__} carries"


def test_the_contact_is_the_same_shape_on_both_sides() -> None:
    assert {declared.name for declared in fields(Contact)} == set(defs.Contact.model_fields)
