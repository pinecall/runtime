"""The golden call log and the state it reduces to, shared with the TypeScript and Ruby reducers."""

from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden"
GOLDEN_LOG = GOLDEN / "call-log.json"
GOLDEN_STATE = GOLDEN / "call-log.state.json"
