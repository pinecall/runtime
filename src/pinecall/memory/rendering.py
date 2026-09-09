"""How recalled facts reach the prompt: one line per fact, under the heading the view wrote."""

from collections.abc import Sequence

from pinecall.types import Fact


# The marker is replaced by these lines and nothing else: no heading, no count, no score. The
# view around the marker says what the section is; a fact that arrived with a newline in it is
# still one line, because a line is what a list item is.
def facts_as_text(facts: Sequence[Fact]) -> str:
    """`- {text}` per fact, in the order given; empty facts are an empty string, and no line."""
    return "\n".join(f"- {' '.join(fact.text.split())}" for fact in facts if fact.text.strip())
