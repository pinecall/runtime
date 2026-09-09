"""The app socket's handler table: what a handler is written against, and the two decorators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Protocol

from fastapi import Depends

from pinecall.api._deps import what_is_live
from pinecall.api.agents.registry import Registry, Send, SocketId
from pinecall.log.entry import Entry
from pinecall.orgs.admission import Admission
from pinecall_protocol import Command, ProtocolError, WireModel, command_of
from pinecall_protocol.defs import ToolResult


# The two protocols below are why this module exists. The call-scoped handlers live in
# on_a_call.py, so if they had to import the socket class to be typed, that module would import
# the socket while the socket imported it back — a cycle that only held together because one of
# the two imports happened late. Both sides depend on this table, and neither knows the other.
class Live(Protocol):
    """The process's live memory, as the app socket uses it: who is connected, what is running."""

    def connect(self, owner: SocketId, send: Send) -> None:
        """This app socket is open and can be handed the entries of the calls it answers."""
        ...

    def disconnect(self, owner: SocketId) -> None:
        """The app socket is gone."""
        ...

    def of(self, call: str | None) -> Any:
        """The session a command names, or None when no call by that id runs in this process."""
        ...

    def answered(self, call: str, result: ToolResult) -> bool:
        """A tool of a call this gateway runs for a worker came back; False when nobody waited."""
        ...

    def commanded(self, call: str | None, agent: str, command: Command) -> bool:
        """Hold a command for the worker running this call. False when no such call is served."""
        ...


class Socket(Protocol):
    """What a handler may do with the app socket it was handed. AppSocket is the one that does."""

    registry: Registry
    live: Live
    admission: Admission

    @property
    def id(self) -> SocketId:
        """This connection, told apart from every other."""
        ...

    @property
    def org(self) -> str:
        """The org the key names: whose every agent on this socket is."""
        ...

    async def send(self, entry: Entry) -> None:
        """One log entry down the wire, exactly as the store keeps it."""
        ...

    async def emit(self, agent: str, type: str, event: WireModel) -> Entry:
        """Append the event to the agent's own log, then send the entry the store handed back."""
        ...

    async def refuse(self, agent: str, code: str, message: str, raw: Any) -> None:
        """Say no in the protocol's own words, naming the command and the id the app gave it."""
        ...


type Handler = Callable[[Socket, Command], Awaitable[None]]

# Every command the app socket answers, by its wire type. A new one is a function and a decorator.
# It is filled at import time, from this package and from api/agents/on_a_call.py, which the
# gateway pulls in when it includes the text router.
HANDLERS: dict[str, Handler] = {}


def handles(type: str) -> Callable[[Handler], Handler]:
    """Declare which command a handler answers."""

    def take(handler: Handler) -> Handler:
        HANDLERS[type] = handler
        return handler

    return take


def asked[T: WireModel](command: Command, shape: type[T]) -> T:
    """The command's data as the model its type names, refused when it is not that shape."""
    data = command_of(command)
    if not isinstance(data, shape):
        raise ProtocolError(f"{command.type} carries {type(data).__name__}, not {shape.__name__}")
    return data


# The live memory is one object the lifespan opened; each side asks for it with the type it needs.
LiveDep = Annotated[Live, Depends(what_is_live)]
