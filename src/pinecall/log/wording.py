"""The words both processes write into a log alike: a tool's text, a silent outcome, a hash."""

from hashlib import sha256

from pinecall_protocol.defs import ToolResult

# The outcome line for a person reading the log of a call where the agent never said anything.
NOTHING_SAID = "no reply"

# The code of an `error` entry that says the platform said no: to a command, or to a tool call.
REFUSED = "refused"


def as_text(result: ToolResult) -> str:
    """What the model reads back from a tool: its error, its summary, or its output as text."""
    if result.error is not None:
        return result.error
    if result.summary is not None:
        return result.summary
    return "" if result.output is None else str(result.output)


def hashed_prompt(text: str) -> str:
    """sha256 of a prompt block, so two states can be compared without the text ever leaking."""
    return sha256(text.encode()).hexdigest()
