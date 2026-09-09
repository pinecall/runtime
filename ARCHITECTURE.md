# Architecture

The Pinecall runtime is one Python distribution, `pinecall`, that runs as **two processes** on
top of LiveKit: the **gateway**, the control plane, and the **worker**, the fleet that answers a
call. Everything else on a box — the SFU, the SIP bridge, Redis, Postgres — is somebody else's
software, run as it ships. This page is the shape of the thing, read off the code: every module
opens with one line that says what it is, `tests/test_isolation.py` says what may import what,
and the *why* of each decision is a page under `docs/decisions/`, named where it applies — the
maintainer's notebook, kept out of git, so a clone has the names and not the pages.

```
   telephone ─► carrier trunk ─► SIP bridge ─┐
   browser ──► POST /v1/tokens ─► joins ─────┼─► LiveKit SFU ─► dispatch ─► WORKER (a job per call)
   WhatsApp ─► /v1/whatsapp/webhook ─┐       │                                 │  HTTP, the org's key
   terminal ─► WS /v1/chat ──────────┼─► GATEWAY ◄───────────────────────────┘
                                     │      │ ▲
                    the tenant's own process│ │ WS /v1/apps: the class, its tools, its view
                    (`pinecall run`) ◄───────┘ │
                                        Postgres: the log, the tenants, the keys, the routes
```

## 1. What is LiveKit's, and what is ours

This runtime does not implement a conversation. LiveKit does, and the line is drawn in code:

| LiveKit provides | used by | as |
|---|---|---|
| **livekit-server**, the SFU: rooms, participants, tracks, the agent dispatch | the box (a container), both processes over its API | `infra/box/containers/pinecall-livekit.container`, `livekit.yaml` |
| **livekit-sip**: a carrier's trunk as a room participant | the box; `session/voice/sip.py`, `room/invite.py`, `transfer.py` | `CreateSIPParticipantRequest`, a REFER for a cold transfer |
| **`livekit.agents.AgentServer`**: the worker process, its job processes, the load it reports | `worker/main.py` | one server, one `rtc_session`, `load_fnc` |
| **`JobContext`**, **`JobProcess`**: one job, one process, prewarm | `worker/main.py`, `worker/entry.py` | `ctx.connect()`, `ctx.room` |
| **`AgentSession`** + **`Agent`**: the conversation — VAD, turn detection, STT → LLM → TTS, interruption, the chat context | `session/voice/session.py`, `session/text/session.py`, `session/*/agent.py` | one session per call, ours subclassing `Agent` for the prompt's blocks |
| **`livekit.agents.llm`**: `ChatContext`, `ChatMessage`, `FunctionCall`, `function_tool`, `ToolError` | `session/`, `evals/` | the model's history; our `ToolSpec` declared as a livekit tool (`session/declaring.py`) |
| **plugins**: `anthropic`, `openai`, `soniox`, `deepgram`, `elevenlabs` | `providers/llm/*`, `stt/*`, `tts/*` — **the only files that may import a vendor** | the plugin IS the adapter; one file per vendor, registered in one line. `providers/embed/tei.py` is the one vendor that is not a plugin: TEI over HTTP, behind the `Embedder` protocol |
| **`livekit.agents.metrics`**: `LLMMetrics`, `STTMetrics`, `TTSMetrics`, `VADMetrics`, EOU, `AgentSessionUsage` | `session/voice/metrics.py`, `log/latencies.py`, `providers/usage.py` | every block, every field, under livekit's own names, on the wire |
| **`livekit.agents.evals`**: `Judge`, `JudgeGroup`, `Verdict`, `Evaluator`, `JudgmentResult` | `evals/judges/*`, `evals/score.py` | our judges are theirs; `PolicyJudge` answers by code |
| **`livekit.agents.cli`** | `cli/worker.py` | `worker dev \| start \| download-files` pass livekit's own flags through |
| **`livekit.rtc`**: the room, `EventTypes`, the DataChannel | `session/voice/room/*` | the room's events as facts; the log to browsers in the room |
| **`livekit.api`** + **`livekit.protocol`**: tokens, `RoomConfiguration`, `RoomAgentDispatch`, `MuteRoomTrackRequest` | `auth/scopes.py`, `tokens/room.py`, `session/voice/room/*` | a call token IS a LiveKit room token with our scope in it |

What is ours, and only ours: **the log** (every event, with a seq), **the wire** (the protocol
both sides are generated from), **the tenants** (orgs, keys, quotas, routes, the vault), **the
gateway** (many app sockets, one fleet), **the bridge** between livekit's session and all of that,
and **the box**. `docs/decisions/livekit-1.8.md`, `livekit-session.md`, `livekit-examples.md`.

Where livekit may be imported is enforced: `types/` and `log/` hold no framework at all (no
livekit, fastapi, uvicorn, asyncpg); a vendor SDK outside `providers/` fails the suite. By
package, what actually imports livekit today: `session` (the bridge — `AgentSession`, `Agent`,
`llm`, `metrics`, `rtc`, `protocol.sip`), `evals` (`llm.ChatContext`, `evals`, `metrics.usage`),
`worker` (`AgentServer`, `JobContext`, `cli`, `room_io`), `providers` (the five plugins),
`tokens` and `auth` (`protocol.room`, `agent_dispatch`, `api`), `cli` (`agents.cli`). `api`,
`orgs`, `routes`, `whatsapp`, `log`, `types` import none of it.

## 2. The entities

Frozen dataclasses in `types/`, spoken by both processes, holding no IO. The durable ones have a
table; the declared ones have a socket.

| entity | fields | lives | relations |
|---|---|---|---|
| **Org** | `id`, `slug`, `name` | `orgs` | has many keys, routes, calls, provider keys; **Quotas** (`minutes`, `messages`, `agents`, `concurrent_calls`) in `quotas`, one row per name |
| **API key** | `sha256`, `org`, `label`, scopes, revoked | `api_keys` | issued once, printed once, revoked by UPDATE. What a worker, an app and a CLI knock with |
| **Route** | `org`, `agent`, `channel` (`phone`·`web`·`whatsapp`), `number`, `label` | `routes` | one door into one agent, in one org. A number is a route, never an agent. The operator's row outranks the app's declaration |
| **AgentConfig** | `slug`, `channels`, `name`, `prompt` (→ PromptBlock: `name`, `region`), `greeting`, `language`, **Voice** (`provider`, `model`, `voice_id`), **Model** ×2 (`llm`, `stt`), **Turn** (`min_interruption_words`, `endpointing_ms`), `says`, `hears`, **KnowledgeFile** (`path`, `text`), **Docs** (`base`, `mode`, `k`, `min_score`), **MemoryPolicy** (`remember`, `forget`), `tools`, `state_fields` (→ Visibility), `events` | **no table** — declared by the app over `WS /v1/apps` at `agent.register`; the agent's own log `@<slug>` is the durable record | one agent, many app sockets (a fleet of `pinecall run`, or one console); many calls |
| **ToolSpec** | `name`, `description`, `parameters`, `side_effect` (`read`·`write`·`irreversible`), `pii`, `confirm`, `preview`, `result_summary`, `timeout_s` | inside AgentConfig | runs in the app's process; an irreversible one is the consent gate's subject |
| **CallContext** | `call`, `channel`, `direction`, `caller`, `route`, `today`, **Contact** (`id`, `phone`, `name`, `email`, `external_id`), `metadata` | `call_log_head` (`log`, `agent`, `call`, `seq`, `sealed`, `started_at`) | one call, one agent, one org, one route; bound to the one app socket that took it |
| **Entry** | `call`, `seq`, `ts`, `agent`, `type`, `ephemeral`, `data`; `log = call ?? '@'+agent` | `call_log`, primary key `(log, seq)`, UPDATE/DELETE refused | the only truth; everything below is a fold of it |
| **Grant** / **Scope** | `talk`·`chat`·`observe`·`supervise`·`participate` → `connects`, `audio`, `reads_log`, `sends_verbs`, `own_call_only`, `single_use`, `ttl_s`, `hears`, `hidden` | `tokens` (one row per call, spent once) | a token carries one call and one scope; spent by the dispatch that opens the call |
| **Consent** | `GateLine` (`seq`, `kind`, `call_id`, `tool`, `audience`, `side_effect`) → `ConsentRead` (`kept`·`broken`·`ungated`·`undeclared`) | read off the log | ring-3 check and ring-4 judge, same rule |
| **ProviderKeys** | `vendor → key` | `provider_keys` (`org`, `vendor`, `ciphertext`, `set_at`), Fernet under `PINECALL_VAULT_KEY` | absent row = the box's key (managed); one row = BYOK |
| **eval run** | `id`, `agent`, `started_at`, `finished_at`, `status`, `document` | `eval_runs` | ring-1 suites driven over live text sessions |
| **Base** / **Chunk** | `base`, `chunks`, `pushed_at` · `id`, `base`, `path`, `heading`, `text`, `score` | `knowledge_bases` (`org`, `base`, `model`, `dimensions`, `chunks`, `pushed_at`) · `knowledge_chunks` (`id`, `org`, `base`, `path`, `heading`, `ordinal`, `text`, `embedding halfvec(1024)`), HNSW by cosine and BM25 in spanish | a push replaces the base whole (`knowledge/store.py`); a search is both indexes fused by reciprocal rank |

Eleven tables, eight migrations (`migrations/000N_*.sql`, applied in order by `migrate up`, never
edited; `0008_memory` is reserved for the contact's facts — **Fact** in `types/knowledge.py` is
its shape). `docs/decisions/types.md`, `orgs.md`, `keys.md`, `routes.md`, `tokens.md`,
`provider-keys.md`, `log.md`.

## 3. The wire

`pinecall-protocol` is generated from JSON Schema in the protocol repository for Python and
TypeScript alike, never edited by hand: `envelope`, `events`, `commands`, `verbs`, `metrics`,
`state`, `rest`, `room`, and the golden fixtures both reducers must fold to the same state. The
log's entry IS the wire's envelope (`log/entry.py`). The fifty-odd entry types, by family:

| family | types |
|---|---|
| the call | `call.started` `call.ringing` `call.dialing` `call.line` `call.log` `call.event` `call.ended` `call.summary` `call.score` |
| the turns | `turn.user` `turn.agent` (each carrying livekit's ChatMessage metrics) |
| the tools | `tool.call` `tool.result` · the gate: `confirm.request` `confirm.granted` `confirm.declined` |
| the metrics, one per typed block | `metrics.llm` `.stt` `.tts` `.vad` `.eou` `.eot` `.interruption` `.realtime` `.avatar` — every field livekit measures, joined by `speech_id` |
| the room | `room.opened` `participant.joined` `.left` `.speaking` `room.sent` |
| the state | `state.changed` (the tenant's fields, by Visibility) · `memory.ops` |
| the agent's own log `@slug` | `agent.register` `agent.registered` `agent.configure` `agent.configured` |
| **commands** (app → call) | `agent.say` `agent.reply` `agent.state` `agent.transcript` `state.set` `session.configure` `call.hangup` `call.transfer` `call.hold` `.unhold` `call.mute` `.unmute` `call.dtmf` `call.dial` `room.invite` `room.send` `participant.mute` `participant.remove` |

Two **projections** decide what leaves the platform (`log/projection.py`, `auth/scopes.py`, the
only two places that spell them): **public** — what a participant in the room may see: the
public state fields, a turn's `role`/`text`, one metric (`e2e_latency`), never `agent` or
`call`; **tenant** — everything, PII masked where it was written (`log/pii.py`), by the field
names the agent declared. A **scope** picks the projection a bearer reads through.

## 4. The gateway, process 1

`api/app.py` is one FastAPI process: a lifespan that opens the Postgres pool, the key table,
the routes, the vault and the meter, then twenty-seven routers, one door each. By resource:

| door | what |
|---|---|
| `WS /v1/apps` | **the app socket**. A tenant's process registers its class (`agent.register` → AgentConfig), holds the agent, receives the entries of the calls it answers, runs the tools, sends commands. `api/agents/socket.py`, `registry.py` (which sockets hold which agent, live), `on_a_call.py` |
| `GET /v1/agents/{slug}/config` · `/provider-keys` · `/pipeline` · `PUT …/pipeline/overrides` | what the **worker** asks about an agent: the declaration resolved, the org's own keys (the one door that ever answers with a key), what it hears/thinks/speaks with, and an operator's knob over it |
| `POST /v1/calls` · `POST /v1/calls/{call}/events` · `/sealed` · `GET /v1/calls/{call}/commands` · `POST …/tools` · `POST …/fill` · `POST …/remember` | the **worker's** side of a call: open the log, append entries, seal, read the app's commands, relay a tool to the app that declared it and wait for the answer, fill a turn's markers from memory and the knowledge base, remember the call at hang-up |
| `GET /v1/calls/{call}/events` (SSE) · `/state` · `/recording` · `GET /v1/agents/{slug}/sessions` · `/calls` | the **readers**: a log as it happens (backlog, marker, live — `log/replay.py`), the folded state memoised per seq (`log/snapshots.py`), the audio, the listing |
| `WS /v1/chat` · `WS /v1/attach` | a **text call** from a terminal or a browser, served in this process; attaching to one |
| `POST /v1/calls/{call}/listen` · `/supervise` · `/verbs` | **the desk**: a supervisor's hidden ear, a seat in the call, the six supervise verbs |
| `POST /v1/evals/run` · `/replay/{call}` · `/caller` · `/voice` · `GET /v1/evals/runs` | **rings 1–3** driven from the gateway: a suite over live text sessions, a finished call re-checked by code, the next line of a simulated caller, one simulated caller on a real line |
| `POST /v1/tokens` | LiveKit's token endpoint with our three things in front: minted only for an agent the key's org answers, single-use, the dispatch riding it |
| `GET /v1/routes` · `/v1/ops/routes` · `/v1/ops/orgs` · `/v1/ops/orgs/{org}/keys` · `/quotas` · `/provider-keys` · `/v1/ops/usage` | the tenant's read, and **the operator API** (`docs/protocol/operator-api.md`), keyed by `PINECALL_OPS_KEY` — what `pinecall/cloud` talks to |
| `GET/POST /v1/whatsapp/webhook` | Meta's handshake and every delivered message; one thread per contact per number, each a text call (`api/whatsapp/`, `whatsapp/`) |
| `GET /v1/whoami` | the name on the key that knocked |

**What the process keeps in memory** (`api/_live.py`, `Live`): the open app sockets, the text
sessions running here, the tool calls in flight waiting on an app, and the calls being **served**
— each bound to the app socket that took it, with its subscription and the queue its commands
wait in for the worker. None of it is durable and none of it should be: "it is a fact about
which sockets are open right now, not a fact about the world. The world is the log."
`docs/decisions/api.md`, `dispatch.md`, `supervise.md`, `whatsapp.md`, `eval-runner.md`.

## 5. The worker, process 2

`worker/main.py` builds one `AgentServer` from the settings — the SFU URL and key pair **handed**
to livekit, never left to it — registers `rtc_session(job, agent_name=<fleet>)` (an empty name
would answer every room in the deployment, so it is refused), and reports its load: **slots**
(`active_jobs / PINECALL_MAX_JOBS`) when the box was measured, the CPU otherwise — and it is
livekit-server that stops routing to a worker at 0.7 of what it reports (`worker/load.py`). livekit forks a job process per call and warms it (`warmed`).

`worker/entry.py`, `answer(ctx, worker)`, is the whole job:

1. `ctx.connect()` and the routes, in one wait; `worker/router.py` says who the job is for —
   what the dispatch said, then the number dialled, then the default (`worker/seat.py`: which
   participant is the caller).
2. The agent's config and the org's provider keys, in one wait, from the gateway
   (`worker/client.py`, the worker's **only** door — HTTP, the org's key; no database, no cache).
3. `POST /v1/calls`: the gateway opens the log and binds the call to an app socket.
4. `session/voice/kit.py` asks `providers/` for the three vendor objects the declaration names;
   `session/voice/session.py` builds the `AgentSession`; **`VoiceBridge`** (`session/voice/voice.py`)
   sits between it and the platform.
5. `worker/commanding.py` streams the app's commands off `GET /v1/calls/{call}/commands` and
   applies each to the bridge, until `None` — the call ending.
6. Hang-up: the bridge writes `call.ended`, asks the gateway to remember the call
   (`POST /v1/calls/{call}/remember`, under `PINECALL_REMEMBER_BUDGET_S`), writes `call.summary`,
   hands its own log to the `Scorer` (`session/scoring.py` → `evals/score.py`), writes
   `call.score`, and the worker seals the call.

**The bridge** hooks livekit's session and turns its life into entries: `events.py` (every
transcript, state, turn, error → an entry), `metrics.py` (every measured block), `writing.py`
(the entries, in order, to the gateway), `tools.py` (every declared tool as livekit runs one: out to the app's process, the
answer back), `hearing.py` (the keyterms the ears are told to expect: the words the agent
declared and the names its state holds), `barge_in.py` (two words cut the agent off, never two
words of agreement), `supervising.py` (the six desk verbs), `commands.py` (say, reply, rewrite the
prompt, stop), `dead_end.py` (a failure whose cause cannot change ends the call instead of
retrying), `room/` (the room's events as facts; invite, mute, remove, send; the DataChannel to
browsers, projected public), `sip.py` and `transfer.py` (the caller's leg; a cold transfer by
REFER). `docs/decisions/worker.md`, `voice-bridge.md`, `room.md`, `sip.md`.

## 6. A session, on either channel

`session/` is one call on either channel, and what both share: the `Scorer` seam, the pending
tool calls, the supervise verbs, `clock.py` (today's date as a tool call the model appears to
have made, never a system message), `declaring.py` (our ToolSpec as livekit's tool).

**Voice** runs in the worker, in a room, with audio. **Text** (`session/text/`) runs in the
**gateway** — WhatsApp, `/v1/chat`, `pinecall chat`, the ring-1 runner — as one livekit
`AgentSession` driven by hand, one turn per message, no room, no ears, the same entries under the
same names; it measures nothing itself, livekit's `LLMMetrics` is the measurement. The **prompt**
on both is a list of named blocks (`types/prompt.py`, `Blocks`) in two regions, in one order —
static blocks (cached by the vendor; `identity · knowledge · tools` by default) · append-only
history · dynamic blocks at the end (the tenant's **view** of its state by default, plus any it
declares). The app writes a block by name with `prompt.set`; the static ones are livekit's
`instructions`, rewritten only when their joined text moved, and `providers/blocks.py` builds
each request: the dynamic blocks after the history, one message each, and for Anthropic one
`system` string per static block, so a rewritten `tools` block leaves the others cached. The
tenant never writes a prompt: the class is the prompt, `render(state)`. A view writes **markers**
and never resolves them (`types/markers.py`): `<!-- knowledge: … -->` in a static block becomes the
text of the file the app declared, fixed at session start, so the cached prefix never moves;
`<!-- memory: … -->` and `<!-- retrieved: … -->` in a dynamic block are asked of the session's
`Filler` (`session/filling.py`) when the caller's turn ends — livekit's `on_user_turn_completed`
on both agents, the whole turn as the query, under `PINECALL_FILL_BUDGET_MS` — and past the budget
the turn goes on unfilled with an `error` entry (`memory_skipped`, `retrieval_skipped`) that says so.
`request_context` applies the fills on the way into the request only: the app's text, and the hash
`prompt.changed` carries, are never touched. At hang-up, between `call.ended` and `call.summary`,
the session's `Rememberer` writes what the call taught about the contact; a miss is
`remember_failed`, recoverable, and the call seals. The voice session's filler is the worker's
gateway client; the text session's is the gateway's own `filling/`, in-process. **A tool runs in the
tenant's process**: the session
sends `tool.call` to the gateway, the gateway relays it down the app socket the call is bound
to, the tenant's `@tool` runs where it was written, `tool.result` rides back to the model.
`docs/decisions/text-session.md`, `prompt-blocks.md`, `memory.md`, `livekit-context.md`,
`livekit-words.md`.

## 7. The path of a call, door by door

| door | arrives | becomes | answered by |
|---|---|---|---|
| **telephone** | the carrier's trunk → livekit-sip → a room, the SIP leg a participant | the dispatch names the fleet; `router.py` finds the route by the number dialled | the worker; a **VoiceBridge** |
| **web** | `POST /v1/tokens` with the org's key → a room token with the agent's dispatch and the scope inside, single-use | the browser joins; the dispatch spends the token | the worker; the log to the browser over the DataChannel, projected public |
| **WhatsApp** | Meta → `POST /v1/whatsapp/webhook`, signature over the raw bytes | one thread per (contact, number) = one text call, `whatsapp/routing.py` picks the agent | the gateway; a **TextSession**; `whatsapp/sending.py` puts the agent's words back on Meta's API |
| **a terminal** | `WS /v1/chat` (`pinecall chat`, `pinecall-runtime chat`) | a text call, `?app=<id>` binds it to the console that opened it | the gateway; a **TextSession** |

Every door ends the same way: `call.summary` (livekit's usage rows, the cost from
`providers/prices.py`), then the judges, then `call.score`, then the seal. `docs/decisions/tokens.md`,
`dispatch.md`, `whatsapp.md`.

## 8. The log

`log/` imports no framework. `entry.py` (the envelope), `store/` (the Store protocol; Postgres,
the one door to a driver; memory for tests; the pool), `fanout.py` (every live reader, bounded
queues — a slow reader is dropped, an append never waits), `replay.py` (backlog, marker, live),
`reduce.py` + `room.py` (fold entries into State; the TypeScript reducer keeps the same rules),
`snapshots.py` (one reduction per call per seq, for the whole process), `projection.py` and
`pii.py` (§3), `filters.py` (what a reader asked for), `latencies.py` (livekit's names off the
two entries a turn lands as), `usage.py` (what an org consumed, folded from `call.summary` and
`call.score`), `writers.py` (which logs this process is writing). The `seq` is born under the
database in the same INSERT; ephemerals spend a seq and leave no row; `ts` is the runtime's
clock. **Compact the view, never the log.** `docs/decisions/log.md`.

## 9. The tenants

`orgs/table.py` (the orgs and their quotas), `admission.py` (may this org open one more call,
hold one more agent), `meter.py` (every org's consumption, folded from the log as it grows, one
cursor per process), `vault.py` (a tenant's own provider keys, Fernet at rest, read by exactly one
door — the worker's). `auth/keys.py` (sha256, no salt; a **dev key** that needs no database and is
then the only key honoured), `auth/bearer.py` (one parser of the header, one close code),
`auth/scopes.py` (which projection, and the room token that carries one call, one scope).
`routes/answering.py`: when the operator's table and the app's declaration both name a door, the
row wins and the loser is named.

## 10. Evals: four rings, one score

| ring | what | runs where | in the tree |
|---|---|---|---|
| 1 | goldens over text: a starting state, what the caller says, what must come of it | the gateway, over the app that holds the agent (`api/evals/`) | `evals/goldens.py`, `headless.py`, `matrix.py`, `report.py`, `runs.py` |
| 2 | the same, spoken: one simulated caller on a real line, the box's speech tool as its voice, an interferer and packet loss if asked | a room, the worker | `evals/calling.py`, `caller.py`, `speech.py`, `line.py` |
| 3 | a finished call read back whole and checked **by code, with no model**: consent, provider errors, latency budget, the register scan, a replay | `POST /v1/evals/replay/{call}`, `pinecall eval` | `evals/checks/*` |
| 4 | **every finished call judged at hang-up**, the verdict an entry in the tenant's own log | the session's `Scorer`, on either channel | `evals/score.py`, `evals/judges/*` |

The judges are livekit's shape. The ring-4 panel is `ConsentJudge` (by code, off the gate lines)
and `GroundedJudge` (every price, hour, date and name the agent stated, against the evidence).
`RegisterJudge` (tú or usted, by code) runs in ring 1 when a golden declares `register`; it is not
on the ring-4 panel because `AgentConfig` declares no register yet. A judge that wants a model
gets one Haiku behind a ceiling (`PINECALL_JUDGE_CEILING_EUR`; zero means no judge asks), and
`call.score` records who was RUN and who ANSWERED. `docs/decisions/evals.md` and its chapters,
`scoring.md`.

## 11. Who may import whom

The whole table, enforced by `tests/test_isolation.py`:

```
types      ← nothing                          (no IO, no framework)
log        ← types                            (no framework, no driver outside store/)
providers  ← types                            (the only place a vendor is named)
auth       ← types, log
orgs       ← types, log
routes     ← types, log
tokens     ← types, log, auth
session    ← types, log, providers
whatsapp   ← types, log, session, routes, providers
evals      ← types, auth, log, session, providers
memory     ← types, log, providers            the contact's facts, in Postgres
knowledge  ← types, log, providers            the knowledge base, in Postgres
filling    ← types, log, providers, memory, knowledge   the gateway's answer to a turn's markers
api        ← all of the above                 never worker/
worker     ← all but whatsapp, memory, knowledge, filling   never api/ — they meet over HTTP
cli        ← the verbs over any of them
```

The core never imports a tenant; a tenant never imports LiveKit. A package earns its directory by
having a line in that table.

## 12. The box, and the line

One machine (`PINECALL_ROLE=all`), or a **hub** — gateway, SFU, SIP, Redis, Postgres, Caddy — and
**workers** dialling it by URL with nothing but the org's key and the vendors' keys. Declared:
cloud-init, systemd units, Quadlet containers, nftables, encrypted systemd credentials, a
Makefile that is the manifest, on any provider. `infra/box/README.md`, `docs/decisions/box.md`.

Everything a self-host needs is here, open: orgs, keys, quotas, usage, routes, the log, the
vault, the operator API, the box. Everything that charges — signup, plans, Stripe, managed
provider keys, the fleet dashboard — is `pinecall/cloud`, private, and only ever talks to a
runtime through the operator API. The same image runs on our box and on a customer's.
