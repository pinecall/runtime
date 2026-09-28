"""Written calls on the gateway: what they count against the org."""

from pinecall.gateway._text_calls import tokens_of
from pinecall.wire.metrics import LLMModelUsage


def test_the_tokens_a_call_used_are_its_models_in_and_out() -> None:
    used = LLMModelUsage(provider="acme", model="acme-1", input_tokens=10, output_tokens=5)
    assert tokens_of([used]) == 15
