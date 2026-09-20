"""One supervise verb aimed at a live call: who may, and onto the queue the worker reads."""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import Depends
from pydantic import TypeAdapter, ValidationError

from pinecall.api._deps import SnapshotsDep, what_is_live
from pinecall.api.calls.sink import another_orgs
from pinecall.auth.scopes import Reader
from pinecall.log.store import Store
from pinecall.session.text.session import TextSession
from pinecall.session.text.supervising import applied
from pinecall_protocol import Command, ProtocolError, encode, verbs
from pinecall_protocol.commands import SupervisorVerb
from pinecall_protocol.defs import Supervisor

# The HTTP door lets FastAPI validate the body; the socket has no body, so the one verb parser
# in this tree lives here and both doors end at the same union.
A_VERB: TypeAdapter[verbs.Verb] = TypeAdapter(verbs.Verb)

NOT_A_FRAME = "a supervise verb is one JSON object: {reason}"

# Nothing by that id is running here. 404 and not 403: a verb has nothing to act on either way,
# and a desk pointed at the wrong gateway learns that instead of guessing at a permission.
NO_LIVE_CALL = "no live call {call!r} on this gateway"

# The bearer's org is not the one whose call this is. 403 and never 404: whether a call
# exists is already public to anybody holding a key, and pretending otherwise would only make an
# operator debug a permission as if it were a routing bug.
NOT_YOUR_CALL = "that call belongs to another org"

# The scope a key steers a call with. A supervise TOKEN was minted at a door that already asked
# it, so the token's grant is the whole of its right; a key is asked here, at both verb doors.
STEERS = "supervise"

# The call is over, so there is nobody to say it to. 409, the same answer /listen gives.
CALL_IS_OVER = "call {call} is over: read its log or its recording instead"

# An API key IS the tenant, and a supervisor who came in on one is named by the org, not by a
# person: the desk's own token (`sup_…`) is what names a human.
A_KEY = "key:{org}"

# The wire type the worker's applier dispatches on. One command for all six verbs.
SUPERVISOR_VERB = "supervisor.verb"


def as_a_verb(frame: str | None) -> verbs.Verb:
    """One inbound frame as the verb it claims to be, or a ProtocolError carrying pydantic's why."""
    try:
        return A_VERB.validate_json(frame or "")
    except ValidationError as malformed:
        raise ProtocolError(NOT_A_FRAME.format(reason=malformed)) from malformed


# What a refusal is, before either door decides how to say it: the HTTP door raises it as an
# HTTPException, the socket writes it into the log as an error entry. One set of sentences.
class VerbRefused(Exception):
    """A verb that will not be applied, with the status the HTTP door would answer."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# What this module needs of the process's live memory, asked for by the one method it calls, so
# api/calls/ and api/supervise/ both reach it without importing _live.py.
class Queueing(Protocol):
    """The live memory, as far as a supervise verb touches it: one command onto a worker's queue."""

    def of(self, call: str | None) -> TextSession | None:
        """The text session this process runs for that call, or None when a worker runs it."""
        ...

    def commanded(self, call: str | None, agent: str, command: Command) -> bool:
        """Hold a command for the worker running this call. False when no such call is served."""
        ...


QueueingDep = Annotated[Queueing, Depends(what_is_live)]


# THE one path a verb takes, whichever door it arrived at: POST /v1/calls/{call}/verbs and the
# frames a supervisor sends down WS /v1/attach both end here, so there is one set of checks and
# one place a verb can be refused. See docs/decisions/supervise.md.
async def aimed(
    live: Queueing,
    store: Store,
    snapshots: SnapshotsDep,
    reader: Reader,
    call: str,
    verb: verbs.Verb,
) -> None:
    """This verb onto that call's worker, or a VerbRefused saying why it will not go."""
    snapshot = await snapshots.of(call)
    if snapshot is None:
        raise VerbRefused(404, NO_LIVE_CALL.format(call=call))
    agent = snapshot.state.agent
    # A token was minted for ONE call and carries no org; the key carries an org and no call.
    # Each is checked against what it has, and neither reaches a call the other's holder owns.
    # Whose the call is, is what its LOG says — the same question every read door asks — and never
    # who is holding the agent's socket right now: a person's key works in the sandbox corner it
    # was minted into, so asking the live table refused every desk a console ever opened on a
    # production call, in the words of a permission it did have.
    if await another_orgs(reader, store, call, ""):
        raise VerbRefused(403, NOT_YOUR_CALL)
    if reader.key is None and reader.call != call:
        raise VerbRefused(403, NOT_YOUR_CALL)
    if not snapshot.live:
        raise VerbRefused(409, CALL_IS_OVER.format(call=call))
    said = SupervisorVerb(by=_who(reader), verb=verb)
    # A text call runs HERE, in this process, and nobody drains the worker's queue for it: the
    # verb is applied against the session itself, and the refusals its applier raises are the
    # same 409 the socket and the HTTP door already say. See docs/decisions/supervise.md.
    session = live.of(call)
    if session is not None:
        try:
            await applied(session, said)
        except ProtocolError as refused:
            raise VerbRefused(409, str(refused)) from refused
        return
    # A voice call runs in a worker, so the verb rides the same queue the app's own commands do:
    # the worker applies them in the order they were sent, and a whisper never overtakes the say
    # before it. The envelope carries no id — an id is what an app gives its own command to match
    # a refusal to, and a desk reads the log instead.
    envelope = Command(type=SUPERVISOR_VERB, agent=agent, call=call, data=encode(said))
    if not live.commanded(call, agent, envelope):
        raise VerbRefused(404, NO_LIVE_CALL.format(call=call))


# Authority is the token or the key and never a field in the body: `by` is filled in HERE, from
# what the door verified, and a body that carries one is refused by the schema before it arrives.
# A person's key, or a seat minted from one, names the person: the member's id, and their name
# beside it. An org's own key names the org, as it did before people had keys; a seat minted from
# a machine key names the seat.
def _who(reader: Reader) -> Supervisor:
    """The supervisor this verb is from, as the thing that let them in names them."""
    if reader.subject is not None:
        return _named(reader.subject, reader.name)
    if reader.key is not None:
        return Supervisor(id=A_KEY.format(org=reader.key.org))
    return Supervisor(id=reader.viewer or "")


def _named(id: str, name: str | None) -> Supervisor:
    """A supervisor with a name when there is one: encode() drops what nobody set, never a null."""
    return Supervisor(id=id) if name is None else Supervisor(id=id, name=name)
