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
| **plugins**: `anthropic`, `openai`, `soniox`, `deepgram`, `elevenlabs` | `providers/llm/*`, `stt/*`, `tts/*` — **the only files that may import a vendor** | the plugin IS the adapter; one file per vendor, registered in one line. `providers/embed/` is the one modality that is no plugin: TEI, Perplexity and OpenRouter over HTTP behind the `Embedder` protocol, built by `embedder_for(settings, http)` — the one place `EMBED_PROVIDER` is switched on |
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
| **Org** | `id`, `slug`, `name` | `orgs` | has many keys, routes, calls, provider keys; **Quotas** in `quotas`, one row per org, replaced whole — four over what it CONSUMES (`minutes`, `messages`, `agents`, `concurrent_calls`) and two over what it KEEPS (`memory_facts`, `knowledge_chunks`). NULL is no limit, which is what a self-hosted box has; **0 is a real limit and is how a plan says it has no memory and no knowledge base at all** |
| **API key** | `sha256`, `org`, `label`, **`env`** (`production`·`development`), `scopes` (the doors as they are grouped: `app`·`calls`·`talk`·`supervise`·`pipeline`·`knowledge`·`memory`·`evals`·`numbers`·`keys`·`team`·`usage`, `types/key.py`), `subject`, `name`, revoked | `api_keys` | issued once, printed once, revoked by UPDATE. What a worker, an app and a CLI knock with. **The key knows where and who**: the world it opens — the agents registered on it, the doors they claim and every call they take are that world's — what it may do there, and the person it was minted for when it is a person's. A key issued before `0013` is production's with every scope |
| **Member** | `id`, `org`, `email`, `name`, `role` (`qa`·`supervisor`·`manager`·`admin`·`developer`, each a preset of key scopes — `types/member.py`), `agents` (empty is every one), `status` (`invited`·`active`·`disabled`), a password hash (argon2id, `auth/passwords.py`) | `members`; `invitations` (`token_hash`, `member`, `expires_at`, `spent_at`) | a person of one org. Made by a one-use invitation that dies in a week; `active` once they chose a password; `disabled` keeps the row and revokes their keys. Logging in mints a **key** for them and their device with their role's scopes, `subject` = the member |
| **Route** | `org`, `agent`, `channel` (`phone`·`web`·`whatsapp`), `number`, `label`, `env` | `routes` | one door into one agent, in one org, in one world. A number is a route, never an agent. The operator's row outranks the app's declaration; a door claimed in one world is refused to a key of the other |
| **AgentConfig** | `slug`, `channels`, `name`, `prompt` (→ PromptBlock: `name`, `region`), **Greeting** (one of `say`/`reply`, plus `allow_interruptions`), `language`, **Voice** (`provider`, `model`, `voice_id`), **Model** ×2 (`llm`, `stt`), **Turn** (`min_interruption_words`, `endpointing_ms`), `says`, `hears`, **KnowledgeFile** (`path`, `text`), **Docs** (`base`, `mode`, `k`, `min_score`), **MemoryPolicy** (`remember`, `forget`), `tools`, `state_fields` (→ Visibility), `events` | **no table** — declared by the app over `WS /v1/apps` at `agent.register`; the agent's own log `@<slug>` is the durable record | one agent, many app sockets (a fleet of `pinecall run`, or one console); many calls |
| **ToolSpec** | `name`, `description`, `parameters`, `side_effect` (`read`·`write`·`irreversible`), `pii`, `confirm`, `preview`, `result_summary`, `timeout_s` | inside AgentConfig | runs in the app's process; an irreversible one is the consent gate's subject |
| **CallContext** | `call`, `channel`, `direction`, `caller`, `route`, `today`, **Contact** (`id`, `phone`, `name`, `email`, `external_id`), `metadata`; `remembered_as`, who memory files the call under | `call_log_head` (`log`, `agent`, `call`, `seq`, `sealed`, `started_at`) | one call, one agent, one org, one route; bound to the one app socket that took it |
| **Entry** | `call`, `seq`, `ts`, `agent`, `type`, `ephemeral`, `data`; `log = call ?? '@'+agent` | `call_log`, primary key `(log, seq)`, UPDATE/DELETE refused | the only truth; everything below is a fold of it |
| **Grant** / **Scope** | `talk`·`chat`·`observe`·`supervise`·`participate` → `connects`, `audio`, `reads_log`, `sends_verbs`, `own_call_only`, `single_use`, `ttl_s`, `hears`, `hidden` | `tokens` (one row per call, spent once) | a token carries one call and one scope; spent by the dispatch that opens the call |
| **Consent** | `GateLine` (`seq`, `kind`, `call_id`, `tool`, `audience`, `side_effect`) → `ConsentRead` (`kept`·`broken`·`ungated`·`undeclared`) | read off the log | ring-3 check and ring-4 judge, same rule |
| **ProviderKeys** | `vendor → key` | `provider_keys` (`org`, `vendor`, `ciphertext`, `set_at`), Fernet under `PINECALL_VAULT_KEY` | absent row = the box's key (managed); one row = BYOK |
| **Fact** | `id`, `contact`, `text`, `category`, `source`, `valid_from`, `invalidated_at`, `score` | `contact_memories` (plus `embedding halfvec(1024)`, `model`, `supersedes`, `confidence`) | one contact's facts in one org, bi-temporal: an update is a new row that supersedes the old one, an invalidation an end date, `forget` the one DELETE. `memory/` recalls them per turn (cosine over the rows THIS embedder wrote, BM25 over every one of them, fused by rank, weighed by recency and confidence) and writes them at hang-up with one model call — a fact naming one of the class's own tools is refused before the table, and `pinecall remember` holds that one model call to goldens of its own (`memory/goldens.py`). `hold` is the other write, with no model in it: the sentences are given, which is what lets a golden measure the real ranking over a contact nobody has |
| **eval run** | `id`, `agent`, `started_at`, `finished_at`, `status`, `document` | `eval_runs` | ring-1 suites driven over live text sessions |
| **Base** / **Chunk** | `base`, `chunks`, `pushed_at` · `id`, `base`, `path`, `heading`, `text`, `score` | `knowledge_bases` (`org`, `base`, `model`, `dimensions`, `chunks`, `pushed_at`) · `knowledge_chunks` (`id`, `org`, `base`, `path`, `heading`, `ordinal`, `text`, `embedding halfvec(1024)`), HNSW by cosine and BM25 in spanish | a push replaces the base whole (`knowledge/store.py`); a chunk is embedded seeing its file's other chunks (`embed_documents`, one document per file); a search is both indexes fused by reciprocal rank (`types/fusion.py`, the one fusion memory ranks with too) and refuses a base another model pushed. `docs/decisions/retrieval.md` |

Thirteen tables, ten migrations (`migrations/00NN_*.sql`, applied in order by `migrate up`, never
edited; `0008_memory` holds the contact's facts and `0010_memory_model` says which embedder wrote
each one — **Fact** in `types/knowledge.py` is its shape —
and `0009_knowledge` the knowledge base's chunks, **Chunk** beside it). `docs/decisions/types.md`,
`orgs.md`, `keys.md`, `routes.md`, `tokens.md`, `provider-keys.md`, `log.md`, `memory.md`.

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
the routes, the vault and the meter, the embedder `EMBED_PROVIDER` names, memory, the knowledge base and the one `Lookups`
over them, then thirty routers, one door each. By resource:

| door | what |
|---|---|
| `WS /v1/apps` | **the app socket**. A tenant's process registers its class (`agent.register` → AgentConfig), holds the agent, receives the entries of the calls it answers, runs the tools, sends commands. `api/agents/socket.py`, `registry.py` (which sockets hold which agent, live, **namespaced by the key's `env`**: the same slug is held once in production and once in development, a dialled door is one agent's in one world, and `agent.registered` says which), `on_a_call.py` |
| `GET /v1/agents/{slug}/config` · `/provider-keys` · `/pipeline` · `PUT …/pipeline/overrides` | what the **worker** asks about an agent: the declaration resolved, the org's own keys (the one door that ever answers with a key), what it hears/thinks/speaks with, and an operator's knob over it |
| `POST /v1/calls` · `POST /v1/calls/{call}/events` · `/sealed` · `GET /v1/calls/{call}/commands` · `POST …/tools` · `POST …/lookup` · `POST …/remember` | the **worker's** side of a call: open the log, append entries, seal, read the app's commands, relay a tool to the app that declared it and wait for the answer, run `recall` or `search` against memory and the knowledge base, remember the call at hang-up |
| `GET /v1/calls/{call}/events` (SSE) · `/state` · `/recording` · `GET /v1/agents/{slug}/sessions` · `/calls` | the **readers**: a log as it happens (backlog, marker, live — `log/replay.py`), the folded state memoised per seq (`log/snapshots.py`), the audio, the listing |
| `WS /v1/chat` · `WS /v1/attach` | a **text call** from a terminal or a browser, served in this process; attaching to one |
| `POST /v1/calls/{call}/listen` · `/supervise` · `/verbs` | **the desk**: a supervisor's hidden ear, a seat in the call, the six supervise verbs |
| `POST /v1/evals/run` · `/replay/{call}` · `/caller` · `/voice` · `GET /v1/evals/runs` | **rings 1–3** driven from the gateway: a suite over live text sessions, a finished call re-checked by code, the next line of a simulated caller, one simulated caller on a real line |
| `POST /v1/tokens` | LiveKit's token endpoint with our three things in front: minted only for an agent the key's org answers, single-use, the dispatch riding it |
| `GET /v1/routes` · `/v1/ops/routes` · `/v1/ops/orgs` · `/v1/ops/orgs/{org}/keys` · `/quotas` · `/provider-keys` · `/v1/ops/usage` | the tenant's read, and **the operator API** (`docs/protocol/operator-api.md`), keyed by `PINECALL_OPS_KEY` — what `pinecall/cloud` talks to |
| `GET/POST /v1/whatsapp/webhook` | Meta's handshake and every delivered message; one thread per contact per number, each a text call (`api/whatsapp/`, `whatsapp/`) |
| `PUT /v1/provider-keys/{vendor}` · `GET /v1/provider-keys` · `DELETE /v1/provider-keys/{vendor}` | **the keys a tenant brought of its own**, on the tenant's key and scoped to its org, which it cannot name: bring one (`{key}` → 204, replacing whatever that vendor had), read the vendors back by name and never a value, take one back (404 for a vendor never brought). A build that knows no such vendor is 400 with the list; a runtime with no `PINECALL_VAULT_KEY` is 503. `api/provider_keys.py` |
| `PUT /v1/knowledge/{base}` · `GET /v1/knowledge` · `DELETE /v1/knowledge/{base}` · `POST /v1/knowledge/{base}/eval` | **the knowledge base**, on the tenant's key: a base pushed whole (`{files: [{path, text}]}` → `{base, chunks, took_ms}`), listed, dropped (404 for a name never pushed); and **the golden** — questions naming the heading path that should answer them (`{asks, expects}`), asked of the base and answered as `recall@k` and `nDCG@10` by code with no model (`knowledge/scoring.py`, the arithmetic shared with memory's golden in `types/goldens.py`). `api/knowledge.py` |
| `GET /v1/contacts/{contact}/memory` · `DELETE` · `POST /v1/contacts/memory/eval` | **a contact's memory**: every fact ever held, current first; forget, the right to be forgotten (`{forgotten: n}`); and **the golden** — questions that bring their own facts (`{holds, asks, expects}`), written to a scratch contact, recalled, deleted, and answered as `recall@k` and `nDCG@10` by code with no model (`memory/scoring.py`, the arithmetic shared with the base's golden in `types/goldens.py`). `api/contacts.py` |
| `POST /v1/agents/{slug}/memory/extraction` | **the write side of memory, held to its own goldens**: a call already written down (both speakers) and the facts already held, one hang-up extraction per case on the org's own model and keys, and four questions asked of what came back — which categories got a fact, which never did, which values must not survive in any fact's text, which held facts were superseded, and that a planted sentence was refused. Judged by code, never by comparing sentences. The body and the answer are the schema's own `ExtractionCases` and `ExtractionRun`, so a transcript is a list of `[who, what]` pairs in all three languages. `api/extraction.py`, `memory/goldens.py`; the verb is `pinecall remember` |
| `GET/POST /v1/members` · `PATCH /v1/members/{id}` · `POST /v1/invitations/{token}` · `POST /v1/login` · `POST /v1/login/codes` | **the org's people**: invite (the one-use token, once), list, change the role, the agents or the standing (disabling revokes their keys); accept an invitation with a password and take the first key; log in with org, email and password — argon2id, five tries a minute per name, one 401 sentence for every wrong thing — or with a one-use code a key holder minted so a browser never carries a key in a URL. `api/members.py`, `api/login.py`, `auth/members.py`, `auth/passwords.py`, `auth/codes.py`, `auth/throttle.py` |
| `POST /v1/agents/{slug}/dev/{family}/{verb}` | **the directory verbs**, relayed: the console asks the gateway, the gateway sends an ephemeral, unstored `dev.request` down the app socket holding the agent — the newest `pinecall run` in the key's world, or the one `?app=` names — and hands back the `dev.answer`'s result, or its refusal's status and sentence verbatim (`api/agents/dev.py`, the waiting room in `api/_live.py`; `docs/protocol/dev-verbs.md`). Four doors, one scope each: `chat` → `talk`, `knowledge`, `memory`, `evals` |
| `GET /v1/sessions` · `GET /v1/events` | **the org's floor**: every agent's newest calls in one list (`store.calls_of`, off the head rows' `org`), and the floor changing as SSE — `agent.registered`, `call.ringing`/`dialing`/`started`/`ended` — tapped off every log the org owns as it is written (`log/writers.py`, `Logs.feed`, `ORG_EVENTS`), live only. `api/floor.py` |
| `GET /.well-known/pinecall` | **discovery**, no key: `{version, cloud}` — which runtime answers here, and whether it is Pinecall's hosted gateway (`PINECALL_CLOUD`) or a box of its own. `api/discovery.py` |
| `GET /{path}` — the LAST route | **the console**: the built page from `src/pinecall/gateway/console/` (package data `scripts/console` copies in from the agents repo, git-ignored, shipped in the wheel) for every path that is not a door's, its assets as themselves, and a JSON 404 under `/v1/` and `/.well-known/` as before. One catch-all, and `tests/api/test_the_console_is_served.py` pins that it is one and last. `api/console.py` |
| `GET /v1/whoami` | the name on the key that knocked: the org, the key's id and label, the world it opens, its scopes, and the person it was minted for |
| **every tenant door** | asks the key for exactly ONE scope by the dep it takes (`api/_deps.py`, `opening(scope)` → `AppKeyDep`, `CallsKeyDep`, …); the read doors ask `calls` at `the_reader`, the verb doors ask `supervise` of a key; `403 this key does not open X: it opens …` (`auth/keys.py`, `not_opening`), and both sockets close with that sentence. `tests/api/test_scopes_at_the_doors.py` walks the app and refuses a door that declares none or two. A seat minted from a person's key carries `pinecall.subject` and `pinecall.name`, so a supervise verb is written down as theirs (`tokens/seating.py`, `supervise/aiming.py`) |

**What the process keeps in memory** (`api/_live.py`, `Live`): the open app sockets, the text
sessions running here, the tool calls in flight waiting on an app, and the calls being **served**
— each bound to the app socket that took it, with its subscription, the queue its commands
wait in for the worker, and the `CallContext` and `AgentConfig` the door that opened it knew,
which is all a lookup ever asks of a call (`Live.the_call` → `lookups.OpenCall`). None of it is
durable and none of it should be: "it is a fact about which sockets are open right now, not a
fact about the world. The world is the log." `docs/decisions/api.md`, `dispatch.md`,
`supervise.md`, `whatsapp.md`, `eval-runner.md`.

A gateway on a dev key opens no Postgres pool, so it holds no memory and no knowledge: a lookup
finds nothing and refuses nobody, and the knowledge and contact doors say so
in one sentence each (`this gateway keeps no knowledge: it runs on a dev key`, 503).

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
history · the dynamic region at the end, which is the tenant's **view** of its state and nothing
else. The app writes a block by name with `prompt.set`; the static ones are livekit's
`instructions`, rewritten only when their joined text moved, and `providers/blocks.py` builds
each request: the history, then this turn's lookups, then the dynamic blocks, one message each,
and for Anthropic one `system` string per static block, so a rewritten `tools` block leaves the
others cached. The tenant never writes a prompt: the class is the prompt, `render(state)`, and
`knowledge` is the file's own text in a static block.

**Memory and the knowledge base are two declared tools**, `recall` and `search`
(`types/lookup.py`), and the class's declaration is what brings each one: `memory` declares
`recall`, `docs` declares `search`. They stand in the request's `tools` array beside the app's
own, and their answers reach the model **as `tool_result` blocks, JSON-encoded** — the one place
both vendors name for anything that arrived from outside the conversation
(`docs/security/prompt-injection.md`, a public contract). With `docs.mode = "retrieved"` (the
default) and whenever `memory` is declared, the session runs the lookup itself and puts a real
`FunctionCall` + `FunctionCallOutput` pair into the request, paired by `call_id` so livekit's
formatter groups it (`session/lookups.py`, the same shape `clock.py` puts today's date in).
**On a spoken call it starts while the caller is still talking**: `session/voice/events.py` hands
every interim transcript to `TurnLookups.heard_so_far`, and the first one carrying four words
(`WORDS_ENOUGH_TO_SEARCH_WITH`) starts one task per tool, asked with the caller's words so far.
One run per turn; livekit's `on_user_turn_completed` consumes it and drops it — already back, it
is read with no wait; still out, its tail is awaited under `PINECALL_VOICE_LOOKUP_BUDGET_MS`; never
started, because the turn was too short, it runs there and then. A text turn has no interim and runs
the whole lookup at turn end, under `PINECALL_TEXT_LOOKUP_BUDGET_MS`, which is larger because nobody
hears a chat's silence. Past the budget that tool's pair is left out and an `error` entry
(`recall_skipped`, `search_skipped`) says why — per tool, so a `recall` that answered is used beside
a `search` that did not; with `docs.mode = "tool"` the platform runs nothing and the model calls
`search` itself, through the same callable. The pair is rebuilt every turn and never kept in the
history, so the cached prefix never moves. At hang-up, between `call.ended` and
`call.summary`, the session's `Rememberer` writes what the call taught about the contact; a miss
is `remember_failed`, recoverable, and the call seals.

**A lookup is run by the gateway, never by the app**: the voice session's `Lookup` is the worker's
gateway client (`POST /v1/calls/{call}/lookup`, `/remember`), the text session's is the gateway's
own `lookups/`, in-process — one
`Lookups(memory, knowledge, logs, calls, keys_of, quotas_of, may_remember)` per process,
implementing both protocols, that recalls the contact's facts (the contact is
`CallContext.remembered_as`: the resolved id, else the number on phone and WhatsApp, else
nobody — never what the model wrote in the tool's input), searches the agent's `docs.base` under
its declared `k`/`min_score`, writes `memory.ops` and `docs.sources` on the call's log with the
turn's `speech_id`, and names the embedder's vendor and URL in the error entry when it is down.
The answers are `{"facts": [{text, source, since}]}` and `{"chunks": [{path, heading, text}]}` and
nothing else. The last two arguments are the org's PLAN, asked of `orgs/` (which `lookups/` may
not import): a tool whose quota is `0` finds nothing, embeds nothing and writes no entry at all —
a plan without the feature is not a failure and never reads as one — and a hang-up whose org may
keep no more facts writes `memory.ops` with an op that kept none, its `credits.exhausted` one
entry away in the agent's log, and asks no model to extract what it could not store. **A tool runs
in the tenant's process**: the session
sends `tool.call` to the gateway, the gateway relays it down the app socket the call is bound
to, the tenant's `@tool` runs where it was written, `tool.result` rides back to the model.
`docs/decisions/text-session.md`, `prompt-blocks.md`, `memory.md`, `retrieval.md`,
`livekit-context.md`, `livekit-words.md`.

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
hold one more agent, keep one more fact, push these chunks — one refusal vocabulary,
`credits.exhausted` in the agent's own log and the same sentence at the door), `meter.py` (every org's consumption, folded from the log as it grows, one
cursor per process), `vault.py` (a tenant's own provider keys, Fernet at rest, written at two
doors — the tenant's own and the operator's — and read back by exactly one, the worker's). `auth/keys.py` (sha256, no salt; a **dev key** that needs no database, is
then the only key honoured, and opens `development`), `auth/bearer.py` (one parser of the header, one close code),
`auth/scopes.py` (which projection, and the room token that carries one call, one scope),
`auth/members.py` (the org's people and their invitations), `auth/passwords.py` (argon2id, the one
slow hash in the tree, for the one secret a person invents), `auth/codes.py` (one-use login codes,
this process's memory), `auth/throttle.py` (so many tries per name per minute at the password door).
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
lookups    ← types, log, providers, memory, knowledge   the gateway runs recall and search
api        ← all of the above                 never worker/
worker     ← all but whatsapp, memory, knowledge, lookups   never api/ — they meet over HTTP
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
