# The eval runner — a suite of goldens through the app that is actually serving

Why `POST /v1/evals/run` opens real text calls instead of headless ones, what a golden is
allowed to say, where the judges' import lives, and what is deliberately not in the schema.

## The runner IS the chat door, without a socket

A golden is a conversation, and this repo already has exactly one way to hold one in text:
`session/text/session.py:TextSession`, opened by `session/text/chat.py`. The runner
(`api/evals/conversation.py`) does what that door does — mint the call, `logs.writing()`,
`live.serve()` to the app socket the registry chose, `live.open()`, `start()`, hang up, and
`logs.forget()` — and the only difference is who says the words: a websocket frame there, a
line of the golden here.

That is the whole reason the calls are real. The app that is holding the agent right now is on
the other end of them: it renders the view from `call.started`, its `@tool` bodies run in the
tenant's own process, its `onEvent` handler fires, and the operator's Pipeline overrides apply
(`overrides.config_for`), because a golden that ran against something else would be testing
something else. A headless ring — `pinecall.evals/headless.py` — is the other shape, and it is
one turn deep by construction; this is the shape that walks a state machine.

## Waiting for the app: silence, not a handshake

Nothing on the wire says "I have finished reacting". The app answers `call.started` with
`prompt.set` · `state.set` · `tools.set` a moment later, and answers `event.received` with
whatever its handler does. So `api/evals/settling.py` watches the call's own log and the
runner waits for it to go quiet — 150 ms of nothing, and at most two seconds — before the next
caller turn. It is a watcher on the session, not a poll of the store, so it costs one timestamp.

Two seconds is the line between "a busy laptop" and "this app is not answering": a render is
milliseconds, and a run must not hang because a tenant's handler awaited something forever.

## `session.configure` is sent by the runner, through the app's own rule

The golden's `state` is seeded exactly as an app seeds a call: `SessionConfigure {state}`.
The handler's body moved out of `session/text/commands.py` into `commands.configure(session,
wanted)` and the registered handler now calls it, so there is one rule and two callers.
`commands.an_event(session, fact)` did the same for `call.event` — which is how a golden that
injects an event the agent never declared is refused **by name**, with the same sentence a live
app gets.

## The golden schema, and the two fields that are not in it

```
{name, state, input[], events[{after_turn, name, data}], expect{tools, not, says,
 grounded, register, replies}}
```

- **`input` is a list.** The spec says "the input"; a conversation with an event in the middle
  of it needs a middle, so it is the caller's turns in order. A one-turn golden writes one.
- **`after_turn`** counts the caller's turns already answered. `0` is before a word is said.
- **`not`** is the mirror of `says` — phrases the agent must not use. It is about words and
  never about tools; the spec's own sentence ties it to staying silent.
- **`replies`** asks whether the agent took the fact up: `true` when the first thing it says
  after the event names something the event carried, `false` when it says nothing of it.
  Deliberately NOT "did it speak first" — whether a tenant's handler calls `agent.reply` or
  waits for the caller's next question is the app's design, and a golden that judged the order
  would be judging the wiring rather than what the caller was told.
- **`register`** is the wire's name; the field is `addressed_as`, because pydantic's own model
  metaclass already answers to `register` and a field that shadowed it warns at import time.
- **`memory` is not here.** The spec lists it, and there is nowhere to put it: the runtime has
  no memory store until ms-9 and `SessionConfigure` carries no such field. A key nothing reads
  is code for later. It lands with `Memory`.

## Every question is a judge, so a run is one table

`expect` is turned into judges, one per field the golden actually declares, and the matrix has a
column per judge — named by the judge's own `name`, never by a key the caller chose. Four of them
are in `pinecall.evals/expected.py`: `tools`, `says`, `silence`, `replies` — and two already
existed: `grounded` and `register`. Inventing a default question for any of those would make every
run pass something nobody wrote down, so each stays exactly as opt-in as its declaration.

One judge is not, and since 2026-09-08 it leads every panel: `consent` answers from the call's own
gate, needs no declaration and puts no question to a model, so a golden with `expect: {}` is
scored by it rather than by nobody — [pinecall-test.md](pinecall-test.md).

`bridge.py` gained what `replies` needs: every `event.received` on the case, with its seq — so
"the reply that followed" is the first assistant turn whose seq is higher, and the join is the
log's own numbering rather than a guess about order.

(Written when the judges were DeepEval graphs; they became `livekit.agents.evals.Judge`
subclasses on 2026-09-08 — [evals-judges.md](evals-judges.md). The division above is unchanged:
one question, one column.)

## The one lazy import

`pinecall-evals` is the runtime's `evals` group and not a dependency of it: it depends on
`pinecall[runtime]`, the worker's three vendor plugins, which a box that only answers calls does
not have. So `api/evals/scoring.py` is the ONE module that names it, and `runner.py` imports
that module inside `_scored()` — a box without the group starts, serves, and answers a run with
503 and the command to install it. mypy is told in `runtime/pyproject.toml`, pyright on the
file's own first lines.

The judge model is built for every run even when every judge answers by code, because every
`Evaluator` is handed one whether or not it asks it anything. A box with no Anthropic key answers
400 naming the key; a box with one spends nothing on a suite whose judges all answer from the
case, and `judge_calls` says so — the door test asserts `0`.

## One run per agent, and a deadline that is not a signal

`Runner` holds one fact — the id of the run in flight — and refuses a second with 409 naming it,
because a run drives the app socket that also answers real calls and a caller queued behind
fifteen minutes has no way of knowing it is queued. The design says SIGKILL; there is no child
to signal, so the deadline is `asyncio.timeout(15 min)`. What it cancels is the conversation in
flight: the calls already made keep their logs, and the row says what stopped it.

**Since ms-14 the hold is per agent, not per gateway** — `_in_flight` is a `slug → run id` map and
`alone(id, agent)` refuses only that agent's second run. The refusal's own reasoning already said
why: a run drives ONE app socket, so two agents held by two apps never meet, and a gateway-wide
lock only made dev B wait for dev A. The fleet is the point — two agents on one gateway from two
machines, one `pinecall test` each — and the 409 now names the agent as well as the run:
*eval run run_… is running on agent clinica-norte: … so there is one per agent at a time*.

## When the app leaves: the run stops, and the matrix stops with it

Found on the ms-6 acceptance, 2026-09-08. Bernardo closed the terminal running `pinecall test`
half-way through a matrix. The CLI process died and the class died with it — and the runner kept
going: it opened the remaining fifteen calls, each against a bare model with no view and no tools
(`tools.changed 0 · prompt.changed 0` on every one of them, against `1 · 2` on the calls the class
had answered), waited each turn out, and reported `8/20 held` in 276 seconds. Every judge was
right about every call; the number graded an agent that was not there.

A run already knows which socket it drives — it is refused a second run *because* it drives the
app that also answers real calls. So `api/evals/attachment.py` holds that one fact:
`Attachment(registry, agent, socket)`, whose `held` is `registry.on(slug, app) is not None`.

- **The conversation is a task, not an await.** `Attachment.driving()` runs the golden and, beside
  it, a watch that polls `held` every 50 ms; whichever finishes first ends the other. Nothing on
  the wire announces a socket closing, so it is a poll — one dict lookup, and only while a
  conversation is actually running. A turn waiting on a model would otherwise hold the run for
  that model's own timeout, which is exactly the wait this is about.
- **The call ends as `app_detached`, by `platform`.** A new `EndReason` in the schema, because a
  call left with nobody rendering its prompt or answering its tools is neither `error` nor
  `drained` and a reader must be able to tell it from a caller hanging up. `conversation.py` hangs
  up with it before the log is sealed, so the reason is in the call's own log and not only in the
  run's row.
- **No further golden is opened.** `held` is asked before the call id is minted, so a golden the
  app will never answer is not opened at all — it is absent from the matrix rather than red.
- **The run fails with arithmetic in it**: `the app detached after 1 of 3 goldens (clinica-norte):
  …`. Only the loop knows how far it got, so the sentence is built there and the row is stored
  `failed` with it. The door still answers 200: the caller asked for the run, and a run that
  stopped with a partial matrix is what there is to answer with.

**A partial matrix is never compared as if it were whole.** Everything that reads a run reads
`status` first: the nightly's gate exits 1 on anything but `done` and prints the error sentence
before it looks at a cell, and `pinecall test` prints the sentence under the count, so `1/1` is
never read as a suite that held. The scores already settled keep their cells — they are true about
the calls the app did answer — and they are a fragment, labelled as one.

## The row, and why the log is still the truth

`eval_runs` (migration `0004`) is one row per run: id, agent, started_at, finished_at, status,
and the whole run as one jsonb document. Not columns per metric — the matrix's shape is the
judges', and a table that mirrored it would need a migration every time a judge is added.

The row is written **when the run starts** and rewritten as each conversation opens, which is
what makes a run tailable: `GET /v1/evals/runs` shows it `running` with the calls it has opened
so far, and each of those is a normal call whose log the console tails through the doors it
already has. Nothing new was written for "log tailed".

`PostgresRuns` serialises the document itself. `open_pool` is the plain pool the gateway's other
tables share, and only the log's own store teaches its connections the jsonb codec
(`log/store/postgres.py`) — the test caught that on the first run against the dev stack.

## What is not here

- **The CLI.** `pinecall test` is tk-3b3aa9's: this card is the door, and the verb builds the
  request and prints the matrix.
- **A diff door.** `GET /v1/evals/runs?since=` is the whole of "diff by start time": two runs
  come back newest first and the caller diffs them. A door that computed the delta would be the
  Evals screen's job, done in the wrong process.
- **Tienda Sur's cart golden.** The spec names it as the reference for the shape; the example
  does not exist in this tree yet (`examples/` holds `chat` and `clinica-norte`). The shape is
  pinned by the door's own tests instead.
