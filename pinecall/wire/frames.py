"""The frames: the base every wire model shares, the log entry, the command, a whole log."""

from typing import Self

from pydantic import BaseModel, ConfigDict, ValidationError

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import JsonObject


class WireModel(BaseModel):
    """A shape on the wire: unknown keys are refused; a field builds by name or alias."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True)

    @classmethod
    def read(cls, raw: JsonObject, what: str) -> Self:
        """Build the model from decoded JSON, raising DeclarationRefused with what did not fit."""
        try:
            return cls.model_validate(raw)
        except ValidationError as error:
            raise DeclarationRefused(f"{what}: {error}") from error

    # The two below stay in pydantic's own parser and writer (Rust): on the paths a gateway runs
    # per entry, decoding to a dict and reading it, or dumping and encoding, is what shows in its
    # profile (21 % of a core at 100 calls, 2026-10-01).
    @classmethod
    def read_json(cls, raw: str | bytes, what: str) -> Self:
        """Build the model from JSON text, raising DeclarationRefused with what did not fit."""
        try:
            return cls.model_validate_json(raw)
        except ValidationError as error:
            raise DeclarationRefused(f"{what}: {error}") from error

    def written(self) -> JsonObject:
        """Return the model as it goes on the wire: wire keys, absent fields absent, JSON values."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)

    def written_json(self) -> str:
        """Return the model as `written()` is, as JSON text in one step."""
        return self.model_dump_json(by_alias=True, exclude_unset=True)


# seq is written before control returns, so two readers never disagree about order.
class Entry(WireModel):
    """One line of a call's log, or of an agent's log when call is null."""

    seq: int
    ts: float
    call: str | None
    agent: str
    type: str
    ephemeral: bool
    data: JsonObject


# The gateway answers with the events the command produces, or with an error naming the id.
class Command(WireModel):
    """One instruction from an app to the gateway over its WebSocket."""

    type: str
    agent: str
    call: str | None
    id: str | None = None
    data: JsonObject
