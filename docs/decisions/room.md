# The room: its facts into the log, the verbs that act on it, its DataChannel

`src/pinecall/session/voice/room/` is the one place in the tree that touches a LiveKit
room. The worker is the only process that holds one (`docs/decisions/worker.md`), so it is the
process that turns the room into facts and the protocol's room verbs into server API calls. Built
on tk-b92f09's bridge: `VoiceBridge._hold_the_room` asks livekit for the job — `get_job_context`,
the library's own accessor — and hands `job.room` and `job.api` (`job.py:438`) to the three things
below, before the job connects, so the first fact the room yields is `room.opened`. A call with no
job — a text session, a test — has no room, and the room verbs say so by name.

## The files

| file | the one idea |
|---|---|
| `holding.py` | the room as a verb reaches it: livekit's room, the server API, the trunk, the log |
| `facts.py` | the room's events as the facts they are: who joined and as what, who spoke, who left |
| `invite.py` · `mute.py` · `remove.py` · `send.py` | one verb each; `agent.reply` stays in `commands.py` because it acts on the session, not the room |
| `datachannel.py` | the log to the browsers in the room, projected public; what they may send back |

## Why facts, and why the room's own

The tenant never touches LiveKit. What it reads is `participant.joined`, `participant.left`,
`participant.speaking`, `track.*` — the room's events, written to the log in the order the room
produced them, through the bridge's one hand on the log (`Writing.later`, from the room's
synchronous callbacks). Nothing is invented on the way: a participant's `attributes` travel
verbatim, so the caller's number on a phone call is `sip.phoneNumber` inside `participant.joined`
and nowhere else — the reducer folds it into `state.room`, and the public projection drops the
attributes whole (`log/projection.py`, `PUBLIC_PARTICIPANT_FIELDS`).

**Who somebody is to the call** is read from three things the room already carries, in this order:
livekit's own `ParticipantKind.AGENT` says which seat is ours; the `pinecall.scope` attribute a
token was minted with (`auth/scopes.py`, `SCOPE_ATTRIBUTE`) says who came to `supervise` or to
`observe`; and the `sip.*` attributes say which leg is a phone — the phone whose `sip.phoneNumber`
is the number the call was resolved for (`worker/router.py`, `CALLER_NUMBER`) is the caller, any
other phone is a leg `room.invite` brought in, `kind: sip`. Everybody else is the person the agent
serves: a widget with a `participate` token is the caller of a web call.

Two things the SDK taught, both checked in `rtc/room.py`: the Python SDK never emits `connected`
— the `connection_state_changed` event carries the state and is the one `room_io.py:181` listens
on too — and `Room.sid` is a coroutine, because the sid arrives with the connect result. So the
opening is a task: `room.opened` with the sid, then `participant.joined` for every seat already
taken (ours first), then their tracks. `participant.speaking` is written on the change and not on
every `active_speakers_changed` tick: the set of who was speaking a moment ago is the one piece of
state `Facts` keeps.

## The verbs: livekit-api as it is, and the fact the room writes

Each verb is a function of the `Holding` and its typed command, registered in `commands.py`'s
dispatch table with one line, wrapped by `_in_the_room`, which checks the shape and that this call
has a room at all. The API calls are livekit's own — `CreateSIPParticipant`, `MutePublishedTrack`,
`RemoveParticipant`, `publish_data`, `generate_reply` — with nothing rewritten around them.

**Where the fact comes from differs per verb, and that was decided on purpose.** `room.invite`
and `participant.remove` write nothing themselves: the fact is what the room reports when it
happens — `participant.joined` with `kind: sip` when the far side answers, `participant.left` with
livekit's own reason `participant_removed` when they go — so the log says what happened and not
what was asked for. `participant.mute` is the exception: livekit mutes a track in place and the
room emits nothing a reader could take for "their audio left the room", so the verb writes
`track.unpublished` itself once the server said yes. `room.send` writes `room.sent` with the topic,
the addressee and the size — never the payload, which is the tenant's business.

**A verb the server refuses lands in the log as `error`**, `code: room_verb_failed`, `command`
naming the verb, `message` the server's own words, `recoverable: true`. The call goes on: the
caller is on the line, and an app that asked for something learns from the log alone that it did
not happen and why. `room.invite` of `kind: participant` is refused the same way: a browser joins
with a token the tenant minted, nothing on the media plane can pull one in.

The outbound SIP trunk `room.invite` dials through is `Holding.trunk`. The bridge has no setting to
read it from yet — the trunk is ms-4's, with the phone — so today a real call has none and
`room.invite` lands `error` naming the verb; the tests hand the `Holding` one and check the request.

## Why the worker projects, and why it reads its own log back

`docs/protocol/projections.md` is the contract: a participant in the room receives
`pinecall.snapshot` once, then `pinecall.log` entry by entry, both through the **public**
projection, and the projection is applied at the sink — here, in `datachannel.py`, before a byte
leaves for the browser. A widget is a client; a client can be modified; so it is never handed the
tenant's entry and asked to keep what it may. `project_state` and `project_entry` are imported from
`log/projection.py` and called with the viewer's identity, so a participant's own `event.received`
reaches it and nobody else's does.

The entries have to carry a `seq`, and the worker never learns one when it writes: the gateway
numbers the log and `POST /v1/calls/{call}/events` answers 204. So the worker **reads its own call
back** through the doors the console reads — `GET /v1/calls/{call}/state`, and
`GET /v1/calls/{call}/events` as a page and as the SSE stream, one URL that `Accept` splits in two
(`api/calls/sink.py`, `wants_sse`). `worker/client.py` grew `state`, `since` and `tail` for it,
`since` paging on the `next` cursor the endpoint hands back and nothing else. The worker reads with
the runtime's key, so it gets the entries whole (tenant projection, pii masked), and projects public
itself. `datachannel.py` asks for those three doors as a `Reading` Protocol that `Gateway` satisfies
structurally: the Protocol earns its keep in the tests, where a log written in memory with a live
tail stands in, which no HTTP fake could do as plainly.

The snapshot is read only after `Writing.flushed()`: the room's own facts about the arrival have
reached the gateway, so the snapshot already holds the seat that asked for it. One tail per call
serves every widget; each widget is remembered with the last `seq` it was sent, so a widget that
joins late gets its snapshot at a newer `last_seq`, is resent whatever the tail already passed, and
from then on receives only what lies above its own cursor — nothing skipped, nothing twice.
`pinecall.replay {after}` resends the durable entries above the cursor to that one viewer and ends
with `log.caught_up`, as the SSE stream does.

**What a widget may send** is exactly what the agent declared with `participant` among the
senders — `AgentConfig.accepts`, the same gate the gateway keeps for the app's `call.event`, asked
of the same config. A declared event lands as `event.received` with `source: participant` and the
sender's identity; an undeclared or malformed one is dropped with one warning in the process log and
never touches the call's log — not even as `error`, because a browser is not a party the log
answers. Only a `participate` token speaks on the channel at all: a supervisor's browser has the
tenant's doors.

## What is not here

`call.dtmf` and `call.hold` act on the caller's own SIP leg and have no runtime yet; `commands.py`
refuses them by name. `call.transfer` landed with the phone and is not a room verb: it acts on one
participant's line, from `worker/transfer.py` — see [sip.md](sip.md). The transport that carries an app's commands to
a worker-run call is still `VoiceBridge.apply`'s door to knock on. The words `text/event-stream`
are spelled once in the gateway's sink and once in `worker/client.py`, because neither process may
import the other's package; when a third writer appears they move to `log/wording.py`.
