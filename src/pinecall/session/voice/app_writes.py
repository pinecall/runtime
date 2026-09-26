"""What the app writes into a spoken call's log: its state, its facts, its lines, its callbacks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pinecall.session.callbacks import a_callback
from pinecall.session.voice.log_writer import Writing
from pinecall.types import AgentConfig, CallContext
from pinecall_protocol import ProtocolError
from pinecall_protocol.commands import CallCallback
from pinecall_protocol.events import Custom, StateChanged
from pinecall_protocol.room import EventReceived

# What the ears are told when the state moves, or nothing on a session whose recogniser takes no
# keyterms. The bridge hands it in because it is the one that holds the session.
type Ears = Callable[[Mapping[str, Any]], None]

UNDECLARED = (
    "agent {slug} never declared the event {name!r} from the app: "
    "declare it in agent.configure before sending it"
)


# The four commands whose whole effect is an entry. They are one class rather than four methods on
# the bridge because that is all they have in common with each other and nothing they share with
# the session: a log to write to, and a declaration to be held to.
class Recorder:
    """The app's own hand on this call's log, held to what the agent declared."""

    def __init__(
        self, config: AgentConfig, context: CallContext, writing: Writing, ears: Ears
    ) -> None:
        self._config = config
        self._context = context
        self._writing = writing
        self._ears = ears

    async def set_state(self, state: Mapping[str, Any], changed: Sequence[str]) -> None:
        """state.set: the app's state moved, and the whole of it goes into this call's log."""
        await self._writing.emit(
            "state.changed", StateChanged(state=dict(state), changed=list(changed))
        )
        self._ears(state)

    async def receives(self, name: str, data: Mapping[str, Any]) -> None:
        """call.event: a declared fact from the tenant's backend; an undeclared one is refused."""
        if not self._config.accepts(name, "app"):
            raise ProtocolError(UNDECLARED.format(slug=self._config.slug, name=name))
        await self._writing.emit(
            "event.received", EventReceived(name=name, data=dict(data), source="app")
        )

    async def log_custom(self, name: str, data: Mapping[str, Any]) -> None:
        """call.log: a line of the app's own, with a seq like everything else."""
        await self._writing.emit("custom", Custom(name=name, data=dict(data)))

    # Into this call's own log, where the caller it is about is. `GET /v1/callbacks` reads the
    # type across every log the org has, so the app finds it there beside the ones the widget and
    # the overflow agent wrote, and a person reading the call sees why it was asked for.
    async def call_back(self, wanted: CallCallback) -> None:
        """call.callback: the number to ring back, and what it is about, as one entry."""
        await self._writing.emit("callback.requested", a_callback(self._context, wanted))
