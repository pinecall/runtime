# The caller's SIP leg, and the cold transfer

Two files at the worker's root, because neither is about the room as a whole: `sip.py` finds the
caller's phone leg inside it, and `transfer.py` sends that leg somewhere else.

| file | the one idea |
|---|---|
| `worker/sip.py` | the caller's SIP leg, found in the room, and the two numbers written on it |
| `worker/transfer.py` | `call.transfer`, cold: the REFER, and the outcome whether or not it took |

## The caller is a participant, not a job attribute

A phone call is a room job, and **`job.participant` is empty in one**. livekit fills that field for
a *publisher* job — `agents/job.py:997` calls the argument `publisher` — and our worker is
`server.rtc_session(job, agent_name=…)`, which a SIP dispatch rule dispatches as a room job. The
first real INVITE through the box proved it (ms-4's report, *Where we stand* item 1): the seat in
the room carried `sip.trunkPhoneNumber`, the job carried nothing, and the call died in `NoRoute`
with the caller still hearing `180 Ringing`.

So there is one reader of the two numbers, `sip.the_numbers`, and it is given the attributes of a
**seat in the room**: the router asks it who dialled what, `facts.py` asks it whether a second leg
is the caller. `TransferSIPParticipant` needs the same seat for a different reason — it names a
participant identity, and that identity belongs to something the SFU created — so the leg is looked
for where it actually lives, in `room.remote_participants`, and never reconstructed from the job.

The consequence for `entry.answer` is an order: **`ctx.connect()` comes before the route is
resolved**, because until the worker is in the room there is nothing to route by. `Facts` therefore
subscribes to a room that is usually connected already, and says so itself (`watch` writes
`room.opened` at once when `room.isconnected()`), so the first fact of a call is unchanged.

It can be a moment late. The agent connects and starts as soon as the job arrives; the SIP leg is
seated by the SIP service, and a verb asked for in the first instants of a call would find an empty
room. So the lookup waits — with **livekit's own wait**, `agents/utils/participant.py:153`, asking
for `ParticipantKind.PARTICIPANT_KIND_SIP`. What that function does not do is give up: it returns
when the participant arrives and not before. The bound is ours, `asyncio.timeout`, five seconds,
because a verb that hangs is a caller listening to silence with nobody explaining why.

Two answers that are not a leg are the same answer, `None`: a room that never connected (livekit
raises there, and a room with no connection has no seats either), and a wait that ran out.

**A console session is skipped, and so is every other door.** `pinecall-runtime worker talk`, a widget and a chat
are rooms nobody dialled: there is no SIP leg coming, and waiting five seconds for one would be
five seconds of a laptop session spent on nothing. Two callers know that in two different ways, and
`the_sip_leg` takes the channel optionally for exactly that reason. A **verb mid-call** knows the
door — one rule that covers the console, the widget and WhatsApp alike — which is why `Holding`
carries the channel this call came in through, beside the room it acts on. The **router** does not:
which door this is, is the question it is asking. So the room answers for it — an inbound leg is
seated before the job is dispatched, and anybody already sitting there who is not a phone means
nobody dialled — and only a room still empty is worth waiting in. A dispatch that named its agent
in the metadata waits for nothing at all: it reads the seat if one is already there, for the
channel and the caller of an outbound call, and moves on.

**The first phone seated is the caller.** The room lists participants in the order it learned of
them, and on a phone call the caller is in the room before the agent finishes connecting. A second
phone leg is `room.invite`'s, and a call holding two of them is a warm transfer — out of scope
below. Matching the caller by `sip.phoneNumber` was considered and dropped: on an **outbound** call
that attribute is the number *we* dialled, not the `caller` the context carries, so the match would
be right inbound and wrong outbound. `facts.py` keeps that comparison because it is answering a
different question — which seat is the caller and which is an invited leg, for the log.

## Cold, and why warm is not here

Cold is a REFER: the caller's own leg is handed the new destination and leaves; the agent's part is
over. It needs nothing but the leg, which is why it works on a self-hosted LiveKit with only an
inbound trunk.

Warm needs two things we do not have. The agent stays on the line until the far side answers, so a
**second leg has to be dialled** — that is `CreateSIPParticipant` and it needs an **outbound
(termination) trunk**, a trunk the deployment buys and configures, not something the runtime can
invent. And when the far side answers, the caller has to be moved onto the new conversation:
livekit's `MoveParticipant` does that and is **LiveKit Cloud only**. Everything short of it is a
bridge we would be writing ourselves, which is the rule this repo keeps: the library first, and the
conversation is not ours to rewrite. So `call.transfer` with `mode: warm` is refused by name, in
the log, and the agent tells the caller.

## The outcome is a fact of the call, not an error entry

Every room verb that LiveKit refuses lands as `error`, `code: room_verb_failed`
(`docs/decisions/room.md`). `call.transfer` is the one that does not, and the reason is what
`ok: false` means: **the caller is still on the line**. That is not a footnote for an operator
reading errors later — it is the state of the call right now, the agent has to say something, and
`state.transfer` has to read `failed` and not stay `requested`. So the verb writes the wire's own
`call.transferred`, with `to`, `mode`, `ok` and `error`, and the reducer folds it
(`log/reduce.py`, `_on_call_transferred`).

`transfer.sent_on` returns that outcome rather than writing it, so the applier in `commands.py` is
the one place that writes the entry and acts on the result — the same shape every other verb has,
and a function a test can drive with nothing but a scripted API.

**Three ways it does not take, and all three carry the SIP status.**

1. The dial raises. livekit turns a SIP-level refusal into `SipCallError`, whose own `__str__`
   already reads `SIP call failed: 486 Busy Here` (`api/twirp_client.py:116`) — so the status is in
   the words without us parsing `metadata`, and every other refusal (auth, validation, the server
   down) reads the same way to whoever asked.
2. The dial does not raise and livekit answers anyway. `TransferSIPParticipantResponse.status` can
   be `STS_TRANSFER_FAILED` with no exception at all (`api/sip_service.py:845-849`), and then its
   `reason` and `sip_status` say why: `rejected, SIP 486 Busy Here`.
3. There is no leg, or the mode is warm. Nothing is dialled, and the outcome says which.

## A transfer that took is not a caller who hung up

The moment the far end takes the call, the caller's leg leaves the room. A session that only saw
them go closes as `PARTICIPANT_DISCONNECTED`, which `voice.py` reads as `caller_hung_up` — and the
log would say the caller hung up on an agent that had just sent them to the front desk. So a
successful transfer tells the bridge, through one more method on the `Ending` the commands already
reach, and `call.ended` carries `EndReason.transferred`. Nothing is ended there: the transfer
already took the caller away.

## The first real number, and the four choices it forced

`+1 417 674 3169` reached this box on 2026-09-08. It was already on the account, on the elastic
trunk `convo-platform` that convo used, pointing at a machine that had been deleted. Four decisions
came out of wiring it, and each of them is now the answer for the next number too. The **order** —
the commands, in the sequence they were actually run — is `infra/README.md`; this is why each one
is what it is.

**A standing trunk is adopted, never replaced.** `twilio_trunk.py` creates a trunk once and refuses
to touch one that already carries its name (`docs/decisions/box.md`). That is right for a trunk we
are building and useless for a trunk somebody else built which is wrong in exactly one field. The
tempting answer — a second trunk, ours, clean — is how a number ends up attached to the one nobody
is watching, and a delete-and-recreate is the shape a carrier's fraud detection reads as an account
takeover. So `--adopt` moves the field: it creates no trunk, attaches and detaches no number,
deletes nothing, and makes **exactly one write** — the trunk's single origination URI, repointed.
It refuses, changing nothing, in the three cases where guessing would be the bug: no trunk of that
name (adopting is not creating), the number not on the trunk (attaching it is a decision), and more
than one origination URI standing, because which of two is this box is a person's call.

**The origination URI names the port.** `sip:box.pinecall.io:5060;transport=udp`, not the bare
host. livekit-sip listens on 5060 and nowhere else (`infra/box/sip.yaml`), the box publishes it for
UDP and TCP alike (`infra/box/box.yml`), there is no TLS listener on 5061, and the firewall opens
exactly that pair. A bare hostname sends Twilio looking for NAPTR and SRV records the box does not
publish, and what answers that search is a carrier's default rather than ours — a fact that costs
nothing to write down and an evening to discover.

**The allow-list is as narrow as the carrier publishes.** Twilio gives a **/30** per edge — four
addresses, eight edges. `carrier-signalling-cidrs.txt` had grown the older, far wider prefixes, and
`54.172.60.0/23` is 512 machines admitted to a SIP port where the carrier uses four. The width was
tightened to the published one, in the one file both the firewall and the inbound trunk read, so
neither can be wider than the other. `test_carrier_cidrs.py` now pins the /30, because a fence is
only a fence at a width somebody keeps.

**Which agent answers is a ROW, not a field on the class.** Both doors exist: a tenant's class
declares `phone` and the registry carries it; an operator types `pinecall-runtime routes add` and
the table carries it, and the operator's row wins (`docs/decisions/routes.md`). The row is the
product's answer, for three reasons. A tenant does not choose which E.164 the carrier hands them —
the platform gives them a number, which is exactly what "an external tenant gets a number routed"
means. Moving it must not need a deploy, which is the promise `routes add` makes and a declaration
would quietly break on the app's next restart. And a real number written into `examples/` is our
carrier inventory committed to a public repository, dialable by anyone who reads it. So
`examples/clinica-norte/agent.ts` keeps the number it always had, unchanged, and the box carries
one row: `+14176743169 phone → clinica-norte`, `operator`.

## What is still open

`call.dtmf` and `call.hold` speak to the same leg and are still refused by name in `commands.py`:
the lookup they need is `sip.the_sip_leg`, and the verbs themselves are somebody's card. The trunk
`room.invite` dials through is still `Holding.trunk` with nothing setting it — outbound is not this
card.
