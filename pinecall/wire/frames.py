"""The frames: the base every wire model shares, the log entry, the command, a whole log."""

from typing import Self

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

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

    def written(self) -> JsonObject:
        """Return the model as it goes on the wire: wire keys, absent fields absent, JSON values."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)


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


_LOG: TypeAdapter[list[Entry]] = TypeAdapter(list[Entry])


def read_log(text: str) -> list[Entry]:
    """Return a whole log from a JSON array text, in the order it came."""
    try:
        return _LOG.validate_json(text)
    except ValidationError as error:
        raise DeclarationRefused(f"log: {error}") from error
