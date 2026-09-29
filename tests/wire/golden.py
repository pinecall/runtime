"""The golden call log and the state it reduces to, shared with the TypeScript and Ruby reducers."""

from pathlib import Path

from pydantic import TypeAdapter

from pinecall.wire.frames import Entry

GOLDEN = Path(__file__).resolve().parent / "golden"
GOLDEN_LOG = GOLDEN / "call-log.json"
GOLDEN_STATE = GOLDEN / "call-log.state.json"

_LOG: TypeAdapter[list[Entry]] = TypeAdapter(list[Entry])


def golden_entries() -> list[Entry]:
    """The golden log's entries, in the order they were written."""
    return _LOG.validate_json(GOLDEN_LOG.read_text(encoding="utf-8"))
