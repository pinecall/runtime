"""The words the log writes alike from both processes: one definition, imported by both sides."""

import pytest

from pinecall.log import wording
from pinecall_protocol.defs import ToolResult

pytestmark = pytest.mark.unit


def test_the_text_the_model_reads_is_the_error_then_the_summary_then_the_output() -> None:
    assert wording.as_text(ToolResult(call_id="x", name="t", error="no")) == "no"
    summarised = ToolResult(call_id="x", name="t", summary="three slots", output=[1, 2, 3])
    assert wording.as_text(summarised) == "three slots"
    assert wording.as_text(ToolResult(call_id="x", name="t", output={"a": 1})) == "{'a': 1}"
    assert wording.as_text(ToolResult(call_id="x", name="t")) == ""


def test_a_prompt_hash_is_sha256_hex_and_says_nothing_of_the_text() -> None:
    hashed = wording.hashed_prompt("You are Clara.")
    assert len(hashed) == 64
    assert "Clara" not in hashed
    assert hashed == wording.hashed_prompt("You are Clara.")
    assert hashed != wording.hashed_prompt("You are Clara")
