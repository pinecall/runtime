"""The prompt's blocks: the default four, the joined static text, and what one write changes."""

import pytest

from pinecall.types import DEFAULT_LAYOUT, Blocks, DeclarationRefused, PromptBlock

pytestmark = pytest.mark.unit

IDENTITY = "You are Clara, of Clínica Norte."
KNOWLEDGE = "The clinic opens at nine."
TOOLS = "find_patient looks a patient up by name and phone."
A_VIEW = "The caller is Ana. Two slots are free."


def test_the_default_layout_with_nothing_declared_is_the_four_blocks() -> None:
    assert [block.name for block in DEFAULT_LAYOUT] == ["identity", "knowledge", "tools", "view"]
    assert [block.region for block in DEFAULT_LAYOUT] == ["static", "static", "static", "dynamic"]
    blocks = Blocks()
    assert (blocks.instructions, blocks.dynamic_texts) == ("", ())
    for block in DEFAULT_LAYOUT:
        blocks.set(block.name, f"the {block.name}")
    assert blocks.static_texts == ("the identity", "the knowledge", "the tools")
    assert blocks.dynamic_texts == ("the view",)


def test_the_static_blocks_join_into_one_instructions_string_in_layout_order() -> None:
    blocks = Blocks()
    blocks.set("tools", TOOLS)
    blocks.set("identity", IDENTITY)
    blocks.set("knowledge", KNOWLEDGE)
    assert blocks.static_texts == (IDENTITY, KNOWLEDGE, TOOLS)
    assert blocks.instructions == f"{IDENTITY}\n\n{KNOWLEDGE}\n\n{TOOLS}"


def test_a_block_nobody_wrote_is_not_sent_and_leaves_no_blank_between_the_others() -> None:
    """The default layout with only an identity is byte for byte the prefix an app used to send."""
    blocks = Blocks()
    blocks.set("identity", IDENTITY)
    blocks.set("tools", TOOLS)
    assert blocks.static_texts == (IDENTITY, TOOLS)
    assert blocks.instructions == f"{IDENTITY}\n\n{TOOLS}"
    assert blocks.text_of("knowledge") == ""


def test_writing_a_static_block_says_the_instructions_moved_and_a_dynamic_one_does_not() -> None:
    blocks = Blocks()
    assert blocks.set("identity", IDENTITY) is True
    assert blocks.set("view", A_VIEW) is False
    assert blocks.set("view", "The caller is Ana. One slot is free.") is False
    assert blocks.dynamic_texts == ("The caller is Ana. One slot is free.",)
    assert blocks.instructions == IDENTITY


def test_rewriting_a_block_with_its_own_bytes_is_no_update() -> None:
    """The same static text again would still cost a cache write, so the caller is told not to."""
    blocks = Blocks()
    blocks.set("identity", IDENTITY)
    assert blocks.set("identity", IDENTITY) is False
    assert blocks.set("identity", "You are Clara. Say less.") is True


def test_a_name_outside_the_layout_is_refused_with_the_name_and_the_names_that_are_in_it() -> None:
    blocks = Blocks()
    with pytest.raises(DeclarationRefused, match=r"'faq'.*identity, knowledge, tools, view"):
        blocks.set("faq", "We open at nine.")
    with pytest.raises(DeclarationRefused, match="'faq'"):
        blocks.text_of("faq")


def test_a_declared_layout_keeps_its_own_order_within_each_region() -> None:
    """A tenant's blocks: a cached faq after the identity, an availability before the view."""
    blocks = Blocks(
        (
            PromptBlock("identity", "static"),
            PromptBlock("faq", "static"),
            PromptBlock("availability", "dynamic"),
            PromptBlock("view", "dynamic"),
        )
    )
    blocks.set("view", A_VIEW)
    blocks.set("availability", "Free today: 10:15, 11:45.")
    blocks.set("faq", "We open at nine.")
    blocks.set("identity", IDENTITY)
    assert blocks.static_texts == (IDENTITY, "We open at nine.")
    assert blocks.dynamic_texts == ("Free today: 10:15, 11:45.", A_VIEW)
