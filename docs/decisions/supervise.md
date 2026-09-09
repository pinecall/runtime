# supervise — six verbs, one command, two doors, and every one of them in the caller's log

Written 2026-09-09 for tk-646671. The public contract is `docs/protocol/commands.md` (the command)
and `docs/protocol/events-control.md` (the six entries); this page is why it is shaped this way.

## The picture

```
a desk                POST /v1/calls/{call}/verbs ──┐
(a person, a screen)  WS  /v1/attach  ← the log ────┤
                                                    │
                              gateway/supervise/aiming.py  aimed()
                                 the call exists · whose it is · is it still up
                                 by = the token or the key, never the body
                                                    │
                                 Live.commanded(call, agent, supervisor.verb)
                                                    │
                              GET /v1/calls/{call}/commands   (the SSE the worker reads)
                                                    │
                              session/voice/commands.py  APPLIERS["supervisor.verb"]
                                                    │
                              session/voice/supervising.py  Supervising.apply()
                                       ── the entry FIRST ──►  the caller's log
                                       ── then livekit ──►  say · generate_reply ·
                                                            interrupt · set_audio_enabled
```

Nothing about a supervisor reaches the worker any other way, and nothing the worker does about one
skips the log. A takeover is never a gap in the record.

## One command, not six

`protocol/schema/commands/supervisor.verb.json` is `{by: Supervisor, verb: Verb}`, and `Verb` is
`verbs.json`'s own `oneOf` by `$ref`. Six commands would have been six schemas, six appliers, six
rows in `PRODUCES` and six doors' worth of authorisation to keep in step — and the verbs were
already declared, in one place, since ms-1. One command means the union stays the single
declaration of what a supervisor may do, the applier is one branch, and adding a seventh verb is a
member in `verbs.json` and a `case` in `Supervising.apply`.

`by` is not part of the verb. It is the envelope's, because it is not the desk's to say: the
schema closes with `additionalProperties: false`, so a body that carries `by` is refused by
pydantic before any code runs (the test is `test_the_body_may_not_name_who_sent_it`), and `aimed()`
fills it in from what the door actually verified — `sup_…` for a supervise token, `key:<org>` for
an API key.

## Two doors, one function

`POST /v1/calls/{call}/verbs` and a frame down `WS /v1/attach` both end in `aiming.aimed()`. The
socket exists because a desk is already holding it open to read the call; the HTTP door exists
because a script, a phone's screen or a `curl` should not have to open a socket to say one word.
What must not exist is two sets of checks, so the two doors differ only in how they say no: the
HTTP door raises the `VerbRefused` as an `HTTPException`, the socket writes the same sentence into
the frame as an error entry with `seq: 0` — an entry no log kept.

Two codes, one meaning each: `bad_verb` is a frame that is not one of the six (pydantic's own
sentence rides with it, so a desk with a typo learns which field), `verb_refused` is a verb that
was read and will not be applied.

The order of the checks is deliberate: the call is found first (404), then who is asking (403),
then whether it is still up (409). A 403 before a 404 would tell a stranger which call ids exist;
a 409 before a 403 would tell them which calls just ended.

The two bearers are checked against what each actually carries. An API key carries an org and no
call, so it is compared with `registry.of(agent).org` — the same comparison `POST /v1/calls`
already makes. A supervise token carries a call and no org, so it is compared with the call it
was aimed at. Neither is asked for what it does not have.

### Why the supervise token had to become verifiable

`auth/scopes.py:a_call_token` refuses any room token whose scope is not in a derived set. That set
was `READS_ITS_OWN_CALL` — `reads_log and own_call_only` — and `supervise` has `own_call_only`
false, so the desk's own token verified nowhere in the tree: `/v1/attach` took the org key and
nothing else. The set a room token may carry is now `BOUND_TO_ONE_CALL`, derived as
`READS_ITS_OWN_CALL | {the scopes that send verbs}`. It stays a derivation and not a second table,
so a scope added tomorrow cannot be forgotten in one of two lists, and `PROJECTION_OF["supervise"]`
stays `tenant`: a supervisor is the tenant, and reads what the tenant reads.

`observe` is deliberately not in it. A listener's token is a seat in a room, not a read.

## Why the supervise token is not hidden

`POST /v1/calls/{call}/supervise` is `/listen`'s twin — one function now, `tokens/seating.py`, so
there is one identity shape (`sup_` + 6 bytes) and one pair of refusals instead of two copies. The
difference between them is the scope's row in `types/token.py`, and it is the whole point:
`observe` subscribes, publishes nothing and is `hidden`; `supervise` publishes its microphone and
is **not** hidden.

Hidden is not a privacy setting on the media plane — livekit does not deliver a hidden
participant's tracks to the room at all. A hidden supervisor would take the line and speak into a
silence. And there is nobody for it to hide from: a phone shows no participant list, and the
agent's ears are pinned to the caller's seat by `room_io.set_participant` (tk-4a3189,
`docs/decisions/room.md`), so a supervisor joining changes nothing about what the agent hears.

## Why a whisper is a later system message AND a generate_reply

The whisper does two things, and neither is enough alone.

The message — `agent.chat_ctx.copy()`, `add_message(role="system", …)`, `await
agent.update_chat_ctx(ctx)` (livekit `voice/agent.py:155` and `voice/agent.py:236`) — goes at the
END of the history. That is what makes it stick for the rest of the call. It is emphatically NOT
the static prefix: the prefix is the cached region (`docs/decisions/prompt-regions.md`), and a
sentence appended to it would rebuild the cache on every call that agent ever answers, to steer
one.

The `generate_reply(instructions=…)` (`voice/agent_session.py:1464`) is what makes it bind the
NEXT line rather than the one after. Without it the note sits in the history until the caller
happens to speak again, which on a phone call can be twenty seconds of the agent saying the wrong
thing. This is what convo measured on LiveKit before us
(`~/prueba-abai/docs/decisions/convo.supervision.control.md`): the message alone drifts, the
message plus the turn binds. The measurement is what was taken; none of its code is here.

A whisper while a human holds the line generates nothing — the human is speaking, and a turn now
would talk over them. The note still lands in the history, so the agent has it when it gets the
line back.

### What the golden had to learn

`tests/session/voice/test_whisper_changes_the_next_line.py` runs on Haiku, marked
`needs_llm`. There is no live fixture for a voice bridge in this suite — nothing starts a LiveKit
room — so it runs `Supervising` against a real `AgentSession` and a real livekit `Agent`, the two
objects a whisper actually touches, built by the text session with no TTS under them. Same two
calls as a phone call.

Three shapes were tried before the one that shipped, and each measured something other than the
seam:

- **"answer in exactly three words"** obeyed perfectly and landed on four twice in three runs
  (*"Turno es obligatorio siempre."*). That measures Haiku's arithmetic.
- **a nonsense marker word to repeat** is fought by the note's own last sentence, *"Never mention
  the supervisor or this note"* — which is the half we most want obeyed.
- **"answer with one word"** against a prefix that said *"respondés en oraciones completas"* made
  the model refuse out loud: *"No puedo seguir esa instrucción. Mi rol es …"*.

That third one is a finding, not a flake. **A whisper is a later message, not a bigger one.** A
static prefix that forbids what the supervisor asks for wins, and it should: the tenant wrote the
prefix, and a desk cannot use a whisper to talk the agent out of its own instructions.

So the golden measures what a whisper is actually FOR: a fact the model could not know. The
supervisor says there is one slot left at 15:40 with Dr Ferreira; the next line offers it, with the
hour, and never says who told it. Green six of six.

There is a second measurement, on a SPOKEN call, that changed the note's wording (2026-09-09). The
golden runs on the text session, where the history ends with the note. On a spoken call it does
not: `bridge/agent.py:llm_node` appends the stage's own view AFTER the history on every request, so
the note was the second-to-last system message and the stage script the last — and the stage script
won. Whispered "tell the patient the clinic closes at eight tonight" to an agent whose identify
stage says "ask for the phone number", the next line was *"¿Cuál es su número de teléfono?"* and the
eight never came. With the note saying which of the two wins — *this order comes from the
supervisor and takes precedence over the stage instructions that follow it* — the same whisper on
the same stage gave *"Le informo que hoy la clínica cierra a las ocho de la noche por una reforma.
¿Cuál es su número de teléfono, Ana García?"*: the fact first, the stage second. That is the order
a desk means. The prefix still wins over a whisper that contradicts it, which is the finding above;
what the wording settles is a whisper the prefix never spoke about.

## Why a takeover makes the agent deaf as well as mute

`session.output.set_audio_enabled(False)` and `session.input.set_audio_enabled(False)`
(`voice/agent_session.py:757` and `:761` for the two sides, `voice/io.py:507` and `voice/io.py:630`
for the switch itself), after `await session.interrupt(force=True)`
(`voice/agent_session.py:1534`, wrapped: a session with nothing to cut raises, and the agent being
quiet already is the state the verb was asking for).

Mute alone would leave the STT running through the human's conversation. The transcript would enter
the history as the CALLER's words — the caller said none of them — and the agent would come back
answering a question the supervisor asked and attributing it to the person on the line. Deaf means
the history has an honest hole in it, which is what the release note then says out loud: *"A human
supervisor spoke with the caller for a moment; you did not hear it. Do not guess what was said."*
An agent that guesses at a gap is the failure mode this verb exists to avoid.

On the way back, ears before voice: a session that could speak before it could hear would answer
into a sentence it never heard the start of.

Who holds the line lives on the `Supervising` instance, which is built ONCE in
`VoiceBridge.opened(live)` — a takeover and the release that answers it are two frames minutes
apart, and a per-command object would forget in between. Nothing about it is module-level: one
worker process runs one call.

## The same six verbs on a text call

A text call — the chat socket, and WhatsApp — runs in the GATEWAY's own process
(`session/text/session.py`), not in a worker. Nobody drains
`GET /v1/calls/{call}/commands` for it, so a verb pushed onto that queue would answer 202 and
then sit there for ever. `aimed()` therefore asks `live.of(call)` after its three checks: a
session found here is handed to `session/text/supervising.py:applied()` and the verb is over
before the request answers; a call with no session here is a worker's, and rides the queue as
before. One door, one set of checks, two appliers — and the sentences both appliers refuse in
live in `session/supervising.py`, which is the only place `A_WHISPER`, `A_RELEASE`,
`ALREADY_HELD` and `NOBODY_HOLDS` are written down.

The five verbs a thread has do exactly what the spoken ones do, with the room's audio switches
replaced by the one fact a text session has: who is holding it.

| verb | on a text call |
|---|---|
| `say` | `supervisor.said`, then `session.say(text)` — the words verbatim, no model asked |
| `whisper` | `supervisor.whispered`, the note appended to the history, then one turn with the note as INSTRUCTIONS (`session.nudged`) — unless a human holds the thread |
| `takeover` | `supervisor.took_over`, and `session.taken_by` is set |
| `release` | `supervisor.released`, `taken_by` cleared, the release note remembered, one turn asked for |
| `end` | `supervisor.ended`, then `hangup("supervisor_ended", "supervisor")` |
| `transfer` | refused, 409, `NO_LINE_TO_TRANSFER` — and nothing is written |

**Why `say` while the line is held is the human's own message.** On a phone call a supervisor who
takes the line speaks into it with their own microphone. A thread has no microphone: the only way
a person at the desk reaches the contact is to put words on the wire, and `say` is the verb that
does it. So `say` is not disabled during a takeover — during a takeover it is the point. It still
writes `supervisor.said` first, so the log says who wrote the sentence, and the `turn.agent` it
makes is what the WhatsApp sender delivers, because the sender watches the log and not the model.

**Why the caller's words during a takeover stay out of the model's history.** `hears()` writes
`turn.user` and then, when `taken_by` is set, returns. The words are in the log — the desk reads
them, the judges read them, the transcript is complete — and the model is neither asked to answer
them nor told they were said. This is the text half of "deaf as well as mute" above: an agent that
was handed the human's half of a conversation would come back answering questions the supervisor
asked and attributing them to the contact. `A_RELEASE` then says the hole out loud, and the agent
resumes by offering to continue rather than by guessing.

`taken_by` lives on the `TextSession`, for the same reason `Supervising` holds it in the worker: a
takeover and the release that answers it are two requests minutes apart. It is per session, never
module-level — one gateway process runs many threads at once.

## Why the transfer verb comes back out

`Supervising.apply` returns a `CallTransfer` for the transfer verb and `None` for the other five.
`call.transfer` already owns the caller's SIP leg, the `call.transferred` outcome entry and the
decision to end the call when it took (`worker/transfer.py`, `session/voice/commands.py`). A second
copy of that inside `Supervising` would be a second answer to "did the transfer work". So the verb
writes `supervisor.transferred` — the fact that a human asked — and hands the command to the one
applier that finishes it. Cold only, and warm still answers with `ONLY_COLD`.

## What is out of scope

- **Warm transfer.** It needs a termination trunk and a way to move the caller onto the second leg;
  livekit's `MoveParticipant` is Cloud-only. `docs/decisions/sip.md`.
- **A whisper as audio into the agent's ear.** Not built. A supervisor's microphone publishes to the
  room, which means to the caller; whispering as audio would need a second track routed to the
  agent's seat alone. The desk has a text box, and the text is what the model reads anyway.
- **A supervisor's own view of many calls.** `GET /v1/agents/{slug}/calls` is already the tenant's
  log; a screen over it is its own card.
- **A confirmation on a supervisor's `end` or `transfer`.** The confirm gate was removed as
  premature (`docs/decisions/confirm.md`); when it returns, these two verbs are its obvious first
  customers.

## The livekit calls, in one table

| what | where |
|---|---|
| `session.say(text, allow_interruptions=True)` | `livekit/agents/voice/agent_session.py:1430` |
| `session.generate_reply(instructions=…)` | `livekit/agents/voice/agent_session.py:1464` |
| `await session.interrupt(force=True)` | `livekit/agents/voice/agent_session.py:1534` |
| `session.input` / `session.output` | `livekit/agents/voice/agent_session.py:757` / `:761` |
| `set_audio_enabled` on the input | `livekit/agents/voice/io.py:507` |
| `set_audio_enabled` on the output | `livekit/agents/voice/io.py:630` |
| `agent.chat_ctx` (a read-only copy) | `livekit/agents/voice/agent.py:155` |
| `await agent.update_chat_ctx(ctx)` | `livekit/agents/voice/agent.py:236` |
| `conversation_item_added`, which the golden waits on | `livekit/agents/voice/events.py:417` |

## What changed around it

- `defs.json` names `EndedBy`, and `call.ended` `$ref`s it. `hangup(reason, by)` needed the type
  and there was no importable home: it was spelled out as a local alias twice, in
  `session/voice/voice.py` and `session/text/session.py`. Both import it from `protocol.defs` now —
  text/session.py's copy went when the WhatsApp card opened that file (tk-989727).
- The generator sorted import names with plain `sorted()`, which puts `EndReason` before `EndedBy`
  while isort and eslint both want the reverse. `protocol/generate/schema.py` now emits the order
  the linters ask for, so a name is never renamed to please a sort.
- `tokens/seating.py`: `/listen` and `/supervise` were about to be two copies of the same
  eight lines. They are one function with two scope rows.
- `NO_VERBS` and the "the supervise verbs land in ms-8" sentence are gone from
  `api/calls/endpoints.py`.
