"""What retrieval puts in front of the model: each chunk under its source, the heading once."""

from collections.abc import Sequence

from pinecall.knowledge.chunking import HEADING_SEPARATOR, body_of
from pinecall.types import Chunk


def chunks_as_text(chunks: Sequence[Chunk]) -> str:
    """`### path › heading`, then the text, one blank line between chunks; nothing for none."""
    return "\n\n".join(_one(chunk) for chunk in chunks)


# The indexes read the heading path over the body; the model reads it in the source line and
# then the body alone, so a heading is never said twice.
def _one(chunk: Chunk) -> str:
    """One chunk: where it came from, then what it says."""
    source = f"### {chunk.path}"
    if chunk.heading:
        source = f"{source}{HEADING_SEPARATOR}{chunk.heading}"
    return f"{source}\n{body_of(chunk.text, chunk.heading)}"
