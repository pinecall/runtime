# The gateway

Why the control plane is the way it is. The code is `src/pinecall/gateway/` and
`src/pinecall/auth/`; this page covers the app socket, the registry and the door, and
grows as the rest of the chapter lands (the text session, the SSE reader, the room).

## By resource, not by route

The gateway spent ms-1 as twelve flat files. `calls.py` (57 lines) and `agents.py` (47) were the
same idea — reading a log back — split in two, sitting beside `reading.py`, which neither of them
was named after and both of them needed. `logs.py` and `log/` were one letter apart and meant
different things. `registry.py` and `declaration.py` had nothing to do with either half. A reader
opening the package learned the number of routes, not what the gateway is.

It is now two packages and one file each side of them:

| package | what it is |
|---|---|
| `apps/` | the tenant's process on the line: the socket, who owns which agent, what a declaration means |
| `log/` | everything that reads what was written down: the three doors, the sink they drain through, the live table, the folded state |
| `text/` | the channels with no LiveKit: the caller's socket, the session, what it measures |
| `app.py`, `deps.py` | the process itself: the lifespan, and what it opened |

Three routers reach `app.py` — `apps`, `log` and `text.chat` — where six used to. Nothing changed
meaning: every move was `git mv` plus the import that followed it, and the suite that was green
before is the same 379 tests green after.

`cli/` went the same way for the same reason: `sessions.py`, `render.py`, `latency.py` and
`calls.py` were one group's four files pretending to be four groups, and `doctor.py` and
`_probes.py` were one more. A group with more than one file is a package whose `verbs.py` holds
the parser; `gateway.py`, `worker.py`, `migrate.py` and `routes.py` are still one file each,
because they are still one idea each. The tests mirror the source, directory for directory, so a
file and the file that tests it are one `nvim -p` line apart.

### A dep lives with the class it hands out

`deps.py` used to hold every dependency, including `RegistryDep`, `LogsDep`, `LiveDep` and
`SnapshotsDep`. Once each of those classes lived in a package whose `__init__.py` includes a route
module, that was a cycle by construction: `deps` imports `apps.registry`, importing `apps` runs
`apps/__init__.py`, which imports `apps/socket.py`, which imports `deps` — half-built.

So the rule is now the one that has no cycle in it and reads better anyway: **`deps.py` holds what
the PROCESS opened** — the settings, the store, the keys, the way to a model, the projection — and
a dependency whose type is a resource's own class lives beside that class. `RegistryDep` is at the
bottom of `apps/registry.py`, `LogsDep` at the bottom of `log/live.py`, `SnapshotsDep` at the
bottom of `log/state.py`, `LiveDep` at the bottom of `text/live.py`. `log/sink.py` had already
been doing exactly this with `CursorDep`, `FilterDep` and `ReaderDep`; it just had no name.

`session/text/__init__.py` stays a bare docstring, and `app.py` includes `text.chat.router`
directly: `chat.py` imports `text/commands.py`, which imports `apps/socket.py` for the handler
table, which needs `LiveDep` from `text/live.py` — an aggregating `text/__init__.py` would close
that loop. The two packages whose `__init__.py` does aggregate, `apps/` and `log/`, were checked
by importing each of the three in isolation.


## The wire learned the domain's ToolSpec instead of the domain learning the wire's

The domain landed with `side_effect: read | write | irreversible` and `confirm: str | None`,
the read-back the agent says before an irreversible tool runs. The wire had two booleans, and
`docs/decisions/types.md` left the question open: does the app send the phrase, or does the
platform compose it?

The platform cannot compose it. The phrase is the last thing the caller hears before something
irreversible happens — a booking, a payment, a cancellation — and it is the tenant's sentence,
in the tenant's language, with the tenant's nouns in it. A phrase assembled from a tool's
description and its argument names would be the platform putting words in the tenant's mouth
at exactly the moment that matters most, and it would be wrong in the two ways that are hardest
to catch: right in English and wrong in Spanish, right for `book_slot` and absurd for
`cancel_policy`.

So the schema changed, in one place, `protocol/schema/defs.json`:

| field | was | is |
|---|---|---|
| `side_effect` | `boolean`, required | `"read" \| "write" \| "irreversible"`, absent means `read` |
| `confirm` | `boolean`, required | `string`, the read-back template with `{argument}` placeholders; absent means no confirmation |

Nothing was renamed and nothing was added: the two fields the wire already had say what the
domain already said. The gain is that the registry maps a declaration to the domain **by name**,
with no projection in either direction, and `tests/domain/test_wire_agreement.py` keeps
proving it. Two booleans could not have expressed three side effects anyway — `side_effect:
true` said "not a read" and left the platform guessing which kind of not-a-read it was.

The alternative was a third field carrying the template beside the two flags. It would have
made a tool with `confirm: true` and no template a shape the schema allows and the domain
refuses, which is the definition of a lie a generator cannot catch.

## The log is the record; memory is only who is on the line

`apps/registry.py` keeps three dicts: socket → agents, agent → registration, door → agent. All of it
dies with the process, and that is right — none of it is a fact about the world, it is a fact
about which TCP connections are open right now. What is durable is the agent's own log:
`agent.registered` and `agent.configured` are appended through the `Store` with `call=None`
before the socket hears a word back, so `agent_since(slug)` is the history of the agent, with a
seq, readable by the console and by a replay.

The alternative was a table of registrations in Postgres. It would have been a second truth,
and the two would drift the first time a process died between the write and the socket: a row
saying an agent is registered on a connection nobody holds. The log has no such state — it
records what happened, not what is.

`Route.door` — the `(channel, number)` pair — is what the registry keeps unique. A number
answers for one agent at a time, whatever the fleet or the label, because that is what a caller
experiences.

## Many sockets, one agent — and one fleet

An agent may be held by several app sockets at once, in the order they claimed it: a new call
takes the newest, a call that has started keeps the socket it took, and whoever opens a call may
name the socket it wants. That is a chapter of its own, with the reasoning and what it
deliberately leaves out: [dispatch.md](dispatch.md). What is left here is what did not move.

`Route.door` is still unique — one number answers for one agent, and `_refuse_a_taken_door` is
untouched. A slug is still one fleet's: `_refuse_another_fleet` says so out loud now that the
one-socket rule is not enforcing it by accident. Disconnect releases only that socket's
registration, and the agent stands while anybody else is holding it.

A refusal is a `DeclarationRefused`, the one name the domain raises when a contract is broken,
so `apps/socket.py` catches one exception and answers one `error` event naming the command's `id` and
the rule. An ownership conflict raises it too: "this socket speaks for this agent" is a
declaration like any other.

## The frames

`app.py` is a lifespan and an include list, nothing else — the file every card of this chapter
appends a router to. `deps.py` reads what the lifespan opened off `app.state` through
`HTTPConnection`, which is what a request and a WebSocket both are, so one dependency serves the
SSE routes and the app socket alike; a test overrides the function and the lifespan never runs.

`apps/socket.py` dispatches by a dict of type → handler. A command the protocol knows and this socket
has no handler for is call-scoped: it needs a session, and the answer is an `error` with code
`no_session` naming the command. A type the protocol never heard of is `unknown_command`. The
text session card adds its handlers with a decorator and a function, and touches nothing else.

Every answer is a log `Entry`, appended before it is sent, because that is the chapter's rule.
The one exception is a frame so broken it names no agent: no log could be told which one to
write to, so the app hears an `error` with `seq: 0`. Every log numbers from 1, so zero reads as
"this was never written down", and no reader has to be taught a new shape.

## Keys: sha256, no salt, and a dev key that needs no database

A Pinecall API key is 256 bits from a CSPRNG. There is nothing to guess and nothing to look up:
no dictionary covers 2^256, and a rainbow table is a precomputation over the space of things
people choose, which this is not. A per-key salt would buy exactly one property — two identical
keys hashing differently — and nobody has two identical 256-bit random keys. What the handshake
does need is speed, and one sha256 is a microsecond; bcrypt's whole point is being slow, which
is the right answer for a secret a person invented and the wrong one here.

`PINECALL_DEV_KEY` short-circuits the database entirely: when it is set it is the only key the
gateway honours, and no pool is opened. That is what lets a fresh clone run the gateway before
Postgres exists. It is never set in production, and the gateway refuses to start with neither a
dev key nor a database rather than accepting everything.

The key travels as `Authorization: Bearer` on the WS upgrade, never in the URL, because a URL
ends up in an access log. A key nothing answers to closes the socket with 1008, policy
violation, and no body: a wrong key learns nothing about why it was wrong.

`asyncpg` ships no type stubs, so its name is said once, inside `open_pool`, behind a `Pool`
Protocol with the two methods this module uses. Everything else — `PostgresKeys` included —
sees a pool a test can fake, which is why the SQL path is covered in ring 0 with no database.

## The text session

A text call has no audio, no room and no livekit. What it does have is the same log a phone call
writes, and that is the whole design: `session/text/session.py` produces `call.started`,
`turn.user`, `agent.state`, `agent.transcript`, `tool.call`, `tool.result`, `metrics.llm`,
`turn.agent`, `call.ended` and `call.summary` — the registry's names, none of them invented for
this path — so a console, a replay and an eval read a chat exactly the way they read a call.

`WS /v1/chat` is the caller's door and `WS /v1/apps` is the tenant's, and one call is live on both:
every entry goes to both, unprojected. The projections (public for a participant, tenant for the
console) belong to the sink, which is the state card's; a session that projected on the way out
would leave two truths about one seq. The app socket gets the entries because the app's own class
runs on them — `onEvent`, a tool method, a handler — and the caller gets them because the widget is
a reader of its own call.

### The instruction of `agent.reply` is not a turn the caller took

`agent.reply {instructions}` makes the model speak now, guided by a line the caller never sees:
"tell them a slot at 10:15 just opened". The model has to read it, so it enters the history as a
user-role message — that is the only role a provider will accept for it. It does **not** produce a
`turn.user`. A `turn.user` is a claim that the person on the line said something, and every reader
downstream believes it: the transcript, the eval, the human reading the console at 2am. Writing
words nobody spoke into that record to satisfy a symmetry would corrupt the one thing the log is
for. The reply lands as exactly one `turn.agent`, which is what the protocol's `PRODUCES` says.

`agent.say` is the sibling that skips the model entirely: one `turn.agent`, verbatim, and the
FakeLLM in the tests proves no request was made. `allow_interruptions` is accepted and ignored on
both — there is no audio to talk over.

### The session measures its own metrics, and leaves absent what it cannot measure

There is no livekit here to hand us a metrics block, so `text/measure.py` measures the same fields
itself, under livekit's own names: `metrics.llm` per provider request with `ttft`, `duration`,
`tokens_per_second` and every token count the provider reported, and `turn.agent` with
`llm_node_ttft`, `llm_node_tps`, `e2e_latency` (from the moment the caller's text arrived) and the
provider request ids.

What the text path cannot measure is **absent**, never zero: `tts_node_ttfb`, `playback_latency`,
`started_speaking_at`. Zero is a measurement — it says "instant" — and a dashboard that averages it
lies for as long as it runs. `encode()` excludes unset fields, so the rule is enforced by building
each entry from the fields that have a value; a count the provider was silent about, like
Anthropic's `cache_creation_tokens` on a request that wrote no cache, never reaches the entry.

That same rule is why every metrics and usage model is built with its `type` tag passed
explicitly: an unset default is not serialised, and a usage row without `llm_usage` on it cannot be
read back through the discriminated union it belongs to.

### Prices are a file with a date on it

`providers/prices.py` is USD per million tokens per model, an exchange rate, and the date both were
read — not a service, not a lookup, and never a number nobody can reproduce. Cache reads and cache
writes are priced apart because the providers price them apart (a read at a tenth of input, a write
above it), and folding them into one input number misprices every cached call in both directions.
`input_tokens` includes the cached ones the way every provider reports it, so the uncached part is a
subtraction; charging the whole of it at the input price would bill a cache hit twice.

A model the table does not know is listed in `cost.unpriced` and contributes nothing. Never zero:
zero is a claim that the call was free, and the one thing worse than not knowing a cost is
publishing a wrong one. A model id carries a date, so the table lists families and matches by the
longest prefix — `claude-haiku-4-5-20251001` is priced by `claude-haiku-4-5`, and a family nobody
listed stays unpriced.

The outcome line of `call.summary` is the last thing the agent actually said. A text session has no
summariser, and inventing one here would put a second model in the hangup path of every call; when
the summariser lands it replaces that line and nothing else moves.

### One seam for the log, one table for the providers

Everything a call writes goes through `text/calllog.py`: a two-verb Protocol, `append` and `seal`,
with the call's id already bound. Today it wraps the `Store`; tk-313f68's `CallLog` is the same
shape, and when it lands it is one line in `chat.py`. Nothing above the seam knows which one it has.

`providers/llm.py` is the contract — `Prompt`, `Message`, `ToolUse`, `Usage`, and a `stream` that
yields text deltas and then exactly one `Finished`. `claude.py` and `gpt.py` are the two adapters,
and `llms.py` is a table of provider → adapter: a third vendor is one new file and one row, never an
edit to a growing `if`. Anthropic's cache breakpoint goes on the static prompt region only, and only
above the length the provider will actually cache; OpenAI caches long prefixes by itself and has
nothing to declare, which is why its prompt goes down as one system message.

A tool call that never comes back is a caller waiting on a backend nobody is watching, so
`text/pending.py` holds the deadline the app declared for that tool and hands the model an error when
it lapses. The model can apologise, try another tool, or ask the caller something — which is what a
person would do.

### What the state changed for

`state.changed` carries a `cause` the app never sends, because the app does not know it: the session
does. A `state.set` that arrives while a tool is in flight is caused by that tool; one that arrives
after an `event.received` is caused by that event, by name and seq; one that arrives on its own has
no cause and the entry says so by leaving the field out. The cause is consumed when it is used —
one fact explains one change.

`call.event` is gated by `AgentConfig.accepts(name, "app")` before anything is written. An
undeclared event is refused with its own name in the message and leaves no trace at all: a log that
recorded a refused event would be a log of things that did not happen.

### The door, for now

`WS /v1/chat` takes the dev key as a Bearer token, and that is temporary: the talk token — short
lived, minted for one call, scoped to talk — is ms-7's card, and this door verifies that instead the
day it lands. A caller who names an agent nobody is registered for gets the handshake closed with
1008 and no explanation; whether a tenant runs an agent by that name is not a stranger's business.

## Reading the log over HTTP

`GET /v1/calls/{id}/events` and `GET /v1/agents/{slug}/calls` are one URL each with two
flavours, and `Accept` picks. The JSON page is `{entries, live, next}`; with
`Accept: text/event-stream` the same entries arrive as SSE and the connection stays open.
`WS /v1/attach?call=` is the supervisor's socket and, in ms-1, only tails.

### `?token=` exists because EventSource cannot set a header

Everywhere else the key rides `Authorization: Bearer`, and the app socket refuses the query
string outright — a URL ends up in an access log. The browser's `EventSource` has no API for
headers at all, and neither does its `WebSocket`. A reader that cannot say who it is cannot read,
so the query string is accepted **here and only here**, on the three read endpoints. The tokens
meant for it are the short-lived ones (`observe`, `participate`); an API key on a query string is
the tenant's own risk, on the tenant's own box.

### 204, not an empty page

The JSON flavour can say `live: false` in its body and a reader knows there will never be more.
A stream has no body to say it in: an empty SSE body reads exactly like a quiet call, and the
browser reconnects, for ever, on a call that ended last Tuesday. So a cursor at or past the end of
a sealed log is `204 No Content`, in both flavours, for the same reason in both: it is the only
answer that means *stop asking*. A cursor **before** the end of a sealed log still answers 200 —
the reader has not read it yet.

Whether a log is over is read off its tail, not off a flag: the last entry is `call.summary` or
the call is not finished. The `Store` keeps no such flag on purpose — it would have to read the
protocol to write a row.

### The markers ride the stream and never the store

`log.caught_up` and `log.gap` are facts about a *reader*, not about the call, so `replay.py` makes
them and no store ever sees them. They ride SSE as ordinary frames with `id:` set to the seq of the
last entry they speak for, which is what makes them safe to resume from: a browser that reconnects
with a marker's `Last-Event-ID` asks for exactly what it would have asked for anyway.

A sealed log's stream therefore ends without a `log.caught_up`: the body stops at `call.summary`,
and there is no "what follows is live" to announce.

### The filter is applied at the sink, and `next` is what was read

`types=` and `durable=` are parsed into the log layer's own `Filter` and handed down; nothing is
renumbered anywhere. The page's `next` is the last seq the page **read**, not the last it kept —
with a filter the two differ, and a page whose every entry was filtered out must still move the
reader's cursor past them. `next: null` means the read itself came back empty, which is the only
honest "there is nothing after this".

### One `Logs` per process, and it is memory

`api/calls/live.py` holds the `CallLog`/`AgentLog` this process is *writing*, so an SSE reader can
subscribe to a live fanout. It is deliberately tiny and deliberately not durable: the store is what
is durable, and this only answers "does this process have a live log for that id". A reader can
never grow the table — `reading()` hands back a detached log over the store for a call nobody here
is writing, which then replays, says `log.caught_up`, and waits. That is the right behaviour for a
reader of another gateway's call, and it is why an unknown id cannot pin memory per request.

The writers append through the same object: `logs.writing(call, agent)` for the text session,
`logs.writing_agent(slug)` for the app socket. Until they do, a running gateway's readers see new
entries on reconnect (the backlog is the store) but not live — the live half is one line in each
writer's card, and is covered at the sink by `tests/api/calls/test_events.py`.

### The pings, and why the pacing carries one pending task

A `: ping` comment every 25 s keeps a proxy with a 30 s idle timeout from cutting a stream that is
merely on a quiet call. It costs a timer, not a protocol event. `log/sink.py`'s `paced()` races the entry
iterator against that timeout and **keeps the pending `__anext__` across the tick**: awaiting a
fresh one after each ping would drop whatever the first was still waiting for.

### `/v1/attach` says `no_verbs` instead of ignoring

The supervise verbs (`say`, `whisper`, `takeover`, `release`, `transfer`, `end`) need a live
session to act on, which arrives with the worker in ms-8. Until then the socket tails, and answers
any inbound frame with an `error` carrying the code `no_verbs` and `seq: 0` — the app socket's own
shape for "this was never written down". A stub that silently swallowed a `takeover` would be the
worst possible failure of exactly the feature a supervisor reaches for.

## State on the wire

`GET /v1/calls/{id}/state` folds the whole log and answers `{state, last_seq, live}`. Three
choices are worth writing down.

**The memo is keyed by `last_seq`, not by a clock.** A log is append-only, so the one thing that
can invalidate a reduced state is a new entry, and the store already knows the highest seq it has
given out. `Snapshots.of(call)` asks for that number — one indexed read — and folds only when it
moved. A TTL would have been the reflex, and it is wrong in both directions: a state that changed
inside the window is stale for the rest of it, and a call where nothing happened is folded again
for no reason. Twenty widgets on one call, all polling, cost one reduction between them, and the
test asserts it by counting the calls to `reduce()`. The memo dies with the process, which is
right — it is a cache of a fold, not a fact, and the log is the fact.

**The projection is decided at the sink, from what the caller IS.** There is no `?projection=`
and there never will be: a widget that could name its projection could name `tenant`. An API key
is the tenant itself and reads `tenant`; a participate token reads `public`, and only its own
call — any other id is 403 before anything is read. The table lives in `auth/scopes.py` and the
two functions that apply it in `log/projection.py`, and a test walks every module of the runtime
with `ast` to prove nothing else so much as spells the two words in code. Only the vocabulary's
two declarations — the generated `protocol/` and `domain/agent.py`, which say which words exist
and interpret none of them — are allowed to.

A public entry loses `agent` and `call` from its envelope too, the same way the public state
loses them: the participant asked about one call and learns nothing about whose fleet answered
it. It keeps `seq`, which is the cursor, and `ts`, which is the clock a transcript is drawn on.

**The participate token is a LiveKit room token.** This section used to argue for an HMAC of our
own, `pt_<call>.<expiry>.<tag>`. The ms-2 audit overruled it: LiveKit already signs and verifies
exactly that claim set, and the browser joining a voice room in ms-7 needs its token anyway. One
format for text and for voice, minted with `livekit.api.AccessToken` and read back with
`TokenVerifier`, with the call id as the room. The whole argument is in [auth.md](auth.md).

**The handler table lives in `api/agents/handlers.py`, and nothing under `apps/` knows `text/`
exists.** The app socket answers three commands itself and ten more that only make sense inside a
running call, and those ten live with the text channel that runs them. That used to mean two
imports in a circle: `text/commands.py` imported `apps/socket.py` for `AppSocket`, `handles` and
`asked`, and `apps/socket.py` imported `text/live.py` for the process's live memory — a cycle that
only held together because Python got to one of the two imports late. Now the table, its
decorator, `asked`, and the two Protocols a handler is written against (`Socket`, `Live`) live in
`apps/handlers.py`, which imports nothing from `text/`. Both sides depend on it and neither on the
other; `AppSocket` satisfies `Socket` structurally, and the one place that knows a running call is
a `TextSession` is the module that runs it. The dep that hands out the live memory moved to
`api/_deps.py`, so both doors ask for the very same object with the type each of them needs.
The registration still happens on the way in — `gateway/app.py` includes the chat router, which
imports `text/chat.py`, which imports `commands` — and a test proves it in a process of its own,
because a suite that has already imported everything cannot.

**The CLI reads through the store, not through the driver.** `asyncpg` is named in exactly one
directory of the runtime, `log/store/`, and a test walks every module with `ast` to say so.
`cli/sessions/source.py` holds a `PostgresStore` and asks it; the two questions it asks that span
every log rather than one — the newest calls of the whole database, and the newest log nothing has
sealed — are methods on `PostgresStore` and not on the `Store` protocol, because that protocol is
what keeps `log/` portable and only an operator ever asks them. The doctor's Postgres probe calls
`installed_extensions(dsn)` from the same module, and the gateway's key pool comes from
`create_pool(dsn)` there too: a second import of the driver is a second door to close.
