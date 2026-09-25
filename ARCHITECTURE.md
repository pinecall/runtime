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
                    (`pinecall start`) ◄───────┘ │
                                        Postgres: the log, the tenants, the keys, the routes
```

## 1. What is LiveKit's, and what is ours

This runtime does not implement a conversation. LiveKit does, and the line is drawn in code:

| LiveKit provides | used by | as |
|---|---|---|
| **livekit-server**, the SFU: rooms, participants, tracks, the agent dispatch | the box (a container), both processes over its API | `infra/box/containers/pinecall-livekit.container`, `livekit.yaml` |
| **livekit-sip**: a carrier's trunk as a room participant | the box; `session/voice/sip.py`, `room/invite.py`, `room/dtmf.py`, `transfer.py`, `bridging.py`; `api/rebuilding.py` asks the SFU for every trunk the tables know at each gateway start, because livekit-sip keeps them in Redis and a Redis that came up empty took every number with it | a REFER for a cold transfer, `CreateSIPParticipantRequest` for a warm one — the person dialled into the call's own room — and `publish_dtmf` for the tones |
| **`livekit.agents.AgentServer`**: the worker process, its job processes, the load it reports | `worker/main.py` | one server, one `rtc_session`, `load_fnc` |
| **`JobContext`**, **`JobProcess`**: one job, one process, prewarm | `worker/main.py`, `worker/entry.py` | `ctx.connect()`, `ctx.room` |
| **`AgentSession`** + **`Agent`**: the conversation — VAD, turn detection, STT → LLM → TTS, interruption, the chat context | `session/voice/session.py`, `session/text/session.py`, `session/*/agent.py` | one session per call, ours subclassing `Agent` for the prompt's blocks |
| **`livekit.agents.llm`**: `ChatContext`, `ChatMessage`, `FunctionCall`, `function_tool`, `ToolError` | `session/`, `evals/` | the model's history; our `ToolSpec` declared as a livekit tool (`session/declaring.py`) |
| **plugins**: every vendor livekit-agents ships one for — forty-seven of them | `providers/catalog.py` is the table; `providers/plugin.py` builds any row from the plugin's own signature; `providers/llm/*`, `stt/*`, `tts/*` are the six this runtime has an OPINION about — **the only files that may import a vendor by name** | the plugin IS the adapter. A tuned file exists where this build overrides a default; every other vendor needs no file (`tts/shelf.py` and `tts/sampling.py` are no vendor: a vendor's own voice catalogue, and one sentence said in a voice, for the doors a person picks a voice at; `providers/language.py` is the one reading of a declared language, livekit's), and `livekit` is LiveKit Inference, which fronts most of them on the box's own project with no vendor key. `providers/embed/` is the one modality that is no plugin: TEI, Perplexity and OpenRouter over HTTP behind the `Embedder` protocol, built by `embedder_for(settings, http)` — the one place `EMBED_PROVIDER` is switched on |
| **`livekit.agents.metrics`**: `LLMMetrics`, `STTMetrics`, `TTSMetrics`, `VADMetrics`, EOU, `AgentSessionUsage` | `session/voice/metrics.py`, `log/latencies.py`, `providers/usage.py` | every block, every field, under livekit's own names, on the wire |
| **`livekit.agents.evals`**: `Judge`, `JudgeGroup`, `Verdict`, `Evaluator`, `JudgmentResult` | `evals/judges/*`, `evals/score.py` | our judges are theirs; `PolicyJudge` answers by code |
| **`livekit.agents.cli`** | `cli/worker.py` | `worker dev | start | download-files` pass livekit's own flags through |
| **`livekit.rtc`**: the room, `EventTypes`, the DataChannel | `session/voice/room/*` | the room's events as facts; the log to browsers in the room |
| **`livekit.api`** + **`livekit.protocol`**: tokens, `RoomConfiguration`, `RoomAgentDispatch`, `MuteRoomTrackRequest` | `auth/scopes.py`, `tokens/room.py`, `session/voice/room/*` | a call token IS a LiveKit room token with our scope in it |

What is ours, and only ours: **the log** (every event, with a seq), **the wire** (the protocol
both sides are generated from), **the tenants** (orgs, keys, quotas, routes, the vault), **the
gateway** (many app sockets, one fleet), **the bridge** between livekit's session and all of that,
and **the box**. Decisions: *livekit-1.8*, *livekit-session*, *livekit-examples*.
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
| **Org** | `id`, `slug`, `name` | `orgs` | has many keys, routes, calls, provider keys; **Quotas** in `quotas`, one row per org, replaced whole — four over what it CONSUMES (`minutes`, `messages`, `agents`, `concurrent_calls`) and four over what it KEEPS (`memory_facts`, `knowledge_chunks`, `numbers` — the routes the box bought for it — and `seats`, the people it holds: invited and active together, since an invitation sent is a seat taken, and a disabled member keeps their row and holds none); `budget_eur` rides beside them, euros a month shown and never refused. NULL is no limit, which is what a self-hosted box has; **0 is a real limit and is how a plan says it has no memory, no knowledge base, no bought number at all** |
| **API key** | `sha256`, `org`, `label`, **`env`** (`production`·`sandbox`), `scopes` (the doors as they are grouped: `app`·`calls`·`talk`·`supervise`·`pipeline`·`words`·`knowledge`·`memory`·`evals`·`numbers`·`keys`·`providers`·`team`·`usage`, `types/key.py`), `subject`, `name`, `created_by`, `last_used_at` (`0039`, written when the key opens the app socket or asks `/v1/whoami`), `expires_at` (`0049`: NULL is never; a sandbox instance's person keys live a day and a key minted from a key never outlives it, `auth/persons.py`; an expired key verifies as a revoked one), revoked | `api_keys` | issued once, printed once, revoked by UPDATE. What a worker, an app and a CLI knock with. **A key knows who; the instance is where** (`PINECALL_WORLD`: one world per instance, and a `pinecall-env` header naming the other is `403`). A **person's** key (`subject` set, prefix `pc_`) is one per device, carries the role's scopes whole and is read in the instance's world: a door that opens no scope reads it as an identity (whoami, a login code, pairing, the org switch, one's own keys); every scoped door reads it as it acts — at production the header must be said, and production opens only while the member's row says so (`Member.opens_production`, read at every request, `auth/world.py`; `403 … has no production access` otherwise). A **server's token** (no `subject`, `pc_live_…` production · `pc_test_…` sandbox) is made by a person from the console (`POST /v1/keys`), belongs to the org, outlives its maker, and opens the one world it was made for — a header naming the other is `403`. The world a request runs in decides the agents registered on it, the doors they claim and every call they take. Keys minted before (`pk_…`) still verify: a key is found by its sha256, never its shape. A key issued before `0013` is production's with every scope. **`fleet` is the box's worker's** (`keys issue --scope fleet`, minted by `pinecall-worker-key.service`, in no role's preset): one worker answers every org's calls, so at the doors it knocks the corner is the CALL's — the org, the world and the holder the dispatch named (`?org=&env=&holder=`, `auth/corner.py:corner_of`) — and never this key's org. A tenant's key names no corner but its own. A person with production access holds `app` there too (`pinecall start --prod`); a deployed slug is normally held by a server's token |
| **Tuning** / **Lexicon** | what the org set for an agent — `voice`, `tts`, `tts_model`, `stt`, `llm`, `greeting`, `hangup`, `turn`, `memory`, `knowledge` (what it knows by heart, Markdown, the floor's to write) and `bases` (the RAG, `Docs` each, 0040) — and the org's words, `said` and `heard` (`types/tuning.py`) | `agent_config` and `lexicon` (`0037`): per (org, `env`, `holder`, version), a row a version, never updated; `''` is the org's own corner | put on the declaration at `providers/tuning.py:tuned`, the one function every session is built through (`api/agents/tuned.py:tuned_for`, per session, never cached) — the class declares the contract (tools, layout, language) and nothing of the environment: the wire's `voice`, `llm`, `greeting`, `docs` and the rest are ignored, one release, and go; a corner reads KNOB BY KNOB down its corners — its own row supplies the knobs it set and the org's own supplies the rest, so a voice of your own keeps the team's llm, and an empty row supplies nothing (`orgs/resolving.py:resolved`, the one fall-through both stores read through), as the bases fall back (0021); a call's head row keeps the two versions it ran on. `orgs/tuning.py` is the store and `orgs/resolving.py` what its rows resolve to; `api/tuning.py`, `api/lexicon.py` the doors, which write the world the request runs in — production's in the org's own corner, set directly by a key that acts there ([docs/protocol/settings-api.md](docs/protocol/settings-api.md)). Replaced `pipeline_overrides`, the six knobs an operator turned over the class with nobody told, dropped in 0040 |
| **Member** | `id`, `org`, `email`, `name`, `role` (`qa`·`supervisor`·`manager`·`admin`·`developer`, each a preset of key scopes — `types/member.py`), `agents` (empty is every one), `status` (`invited`·`active`·`disabled`), `production`, `verified` (`verified_at`, 0048: the address proved theirs — a link that travelled by mail alone accepted, a provider's word, the operator's invitation; what lets a second org seat them without a link), a password hash (argon2id, `auth/passwords.py`) | `members`; `invitations` (`token_hash`, `member`, `expires_at`, `spent_at`, `vouched`: whether accepting it proves the address) | a person of one org. Made by a one-use invitation that dies in a week; `active` once they chose a password; `disabled` keeps the row and revokes their keys. **`production`** (`0039`) is the switch an admin sets: whether their requests may run in production; an admin always opens it (`opens_production`). Logging in mints a **key** for them and their device with their role's scopes, `subject` = the member (`auth/persons.py:a_persons_key`) |
| **Carrier** | `org`, `account`: a **TwilioAccount** (`account_sid`, `user`, `secret`) or a **SipPeer** (`username`, `password`, `addresses` CIDR, and four optional `outbound_*` fields — where the box places an INVITE, over what transport, and what it authenticates as, falling back to the pair the peer registers with) | `carriers` (`org`, `kind`, `account`, `ciphertext` under the vault key, `set_at`) | whose numbers reach the org's agents. One per org. Importing a number: the carrier's trunk pointed at the box and the number attached (`routes/twilio.py`), the org's LiveKit inbound trunk admitting it (`routes/trunks.py`), the route. `docs/protocol/numbers.md` |
| **OutboundTrunk** | `org`, `kind`, `trunk_id`, `address`, `username`, `password` | `outbound_trunks` (0031), the password sealed under the vault key | the other direction, and a **different object**: origination is where the carrier sends a call that ARRIVES, termination is where the box sends one it PLACES. Provisioned once and remembered because neither Twilio nor LiveKit shows a trunk's password twice (`routes/outbound.py`, `orgs/outbound.py`, `api/outbound.py`) |
| **DialPolicy** / **Destination** | `dial_anywhere` (off), `per_minute` (6), `per_day` (200), `max_duration_s` (600) · a number and the calling code it reaches | `dial_policy` (0032), one row per org replaced whole — a NULL column is the code's default, not "no limit"; its `countries` column is still in the table and read by nothing; every dial asked for, taken **or** refused, in `dials` (0033) | what one org may dial out, set by the operator alone (`PUT /v1/ops/orgs/{org}/dialling`): an org that could lift its own fence has none. Which countries a dial may reach is the carrier account's own setting (Twilio's geo permissions), never a guard here. `types/dialling.py` holds E.164's calling codes and the satellite and global-service ranges a call BACK is never to; `orgs/guards.py` judges one dial in the order that refuses the cheapest thing first — shape, stranger, a minute's window, a day's — and writes the ledger row either way. `docs/protocol/console-api.md` §4 |
| **Route** | `org`, `agent`, `channel` (`phone`·`web`·`whatsapp`), `number`, `label`, `env`, `managed` (the box bought it) | `routes` | one door into one agent, in one org, in one world. A number is a route, never an agent. A row is the ONLY way a door exists — a class declares none — and the table's own key is what makes a number one org's and one world's |
| **AgentConfig** | `slug`, `channels`, `name`, `prompt` (→ PromptBlock: `name`, `region`), **Greeting** (one of `say`/`reply`, plus `allow_interruptions`), `language`, **Voice** (`provider`, `model`, `voice_id`), **Model** ×2 (`llm`, `stt`), **Turn** (`min_interruption_words`, `endpointing_ms`, `eot_threshold`, `eager_eot_threshold`), `says`, `hears`, `knowledge` (the org's Markdown, read whole into the static block), `bases` (every **Docs** the world attached: `base`, `mode`, `k`, `min_score`), `uses_knowledge` (whether the class searches one itself), **MemoryPolicy** (`remember`, `forget`), `tools`, `state_fields` (→ Visibility), `events` | **no table** — declared by the app over `WS /v1/apps` at `agent.register`; the agent's own log `@<slug>` is the durable record | one agent, many app sockets (a fleet of `pinecall start`, or one console); many calls |
| **ToolSpec** | `name`, `description`, `parameters`, `side_effect` (`read`·`write`·`irreversible`), `pii`, `confirm`, `preview`, `result_summary`, `timeout_s` | inside AgentConfig | runs in the app's process; an irreversible one is the consent gate's subject |
| **CallContext** | `call`, `channel`, `direction`, `caller`, `route`, `today`, **Contact** (`id`, `phone`, `name`, `email`, `external_id`), `metadata`; `remembered_as`, who memory files the call under | `call_log_head` (`log`, `agent`, `call`, `seq`, `sealed`, `started_at`) | one call, one agent, one org, one route; bound to the one app socket that took it |
| **Entry** | `call`, `seq`, `ts`, `agent`, `type`, `ephemeral`, `data`; `log = call ?? '@'+agent` | `call_log`, primary key `(log, seq)`, UPDATE/DELETE refused | the only truth; everything below is a fold of it |
| **Grant** / **Scope** | `talk`·`chat`·`observe`·`supervise`·`participate` → `connects`, `audio`, `reads_log`, `sends_verbs`, `own_call_only`, `single_use`, `ttl_s`, `hears`, `hidden` | `tokens` (one row per call, spent once) | a token carries one call and one scope; spent by the dispatch that opens the call |
| **Consent** | `GateLine` (`seq`, `kind`, `call_id`, `tool`, `audience`, `side_effect`) → `ConsentRead` (`kept`·`broken`·`ungated`·`undeclared`) | read off the log | ring-3 check and ring-4 judge, same rule |
| **ProviderKeys** | `vendor → key` | `provider_keys` (`org`, `vendor`, `ciphertext`, `set_at`), Fernet under `PINECALL_VAULT_KEY` | absent row = the box's key (managed); one row = BYOK |
| **Fact** | `id`, `contact`, `text`, `category`, `source`, `valid_from`, `invalidated_at`, `score` | `contact_memories` (plus `env`, `embedding halfvec(1024)`, `model`, `supersedes`, `confidence`) | one contact's facts in one org **in one world** — a test call's facts never reach the memory a production call reads under the same number (`0018`) — bi-temporal: an update is a new row that supersedes the old one, an invalidation an end date, `forget` the one DELETE. `memory/` recalls them per turn (cosine over the rows THIS embedder wrote, BM25 over every one of them, fused by rank, weighed by recency and confidence) and writes them at hang-up with one model call — a fact naming one of the class's own tools is refused before the table, and `pinecall remember` holds that one model call to goldens of its own (`memory/goldens.py`). `hold` is the other write, with no model in it: the sentences are given, which is what lets a golden measure the real ranking over a contact nobody has |
| **eval run** | `id`, `agent`, `started_at`, `finished_at`, `status`, `document` | `eval_runs` | ring-1 suites driven over live text sessions |
| **Persona** | `name` (lower-case words joined by hyphens, as `--persona` takes it), `about`, `goal`, `style`, `facts`, `state`, `llm`, `tts`, `voice`, `accepts_when`, `declines_when`, `author`, `set_at` | `agent_personas` (`0042`), primary key (`org`, `name`) since `0045`, the five of how it is played and when it accepts since `0047` | the **org's** synthetic callers. It **was** a file of the tenant's project (`test/<agent>/personas/*.ts`), which is why only a terminal standing in that directory could list one and the production console showed none: a persona is not code but a goal, a manner and a handful of facts, the same kind of thing as the voice and the lexicon, so it moved here with them (`0038`). It was then filed under one agent, which was the file's directory talking: a caller is a person on the phone, and who they are does not depend on which of the org's agents answers, so `0045` made it one list per **org** — merging a name two agents held onto the most-recently-written and keeping the other as `<name>-<agent>`. Not per world either: a caller is a test, not something a customer hears, and `price-shopper` is written once for both. Written whole (nothing is merged); `was` in the body renames. `llm` · `tts` · `voice` are the agent's own three knobs, read by `providers/tuning.py` and refused at the door; the rule rides the dispatch onto `call.started`, where the `persona` judge reads it. `orgs/personas.py` is the store, `api/personas.py` the three doors and `api/persona_runs.py` the fourth |
| **Base** / **Chunk** | `base`, `chunks`, `pushed_at` · `id`, `base`, `path`, `heading`, `text`, `score` | `knowledge_bases` (`org`, `env`, `holder`, `base`, `model`, `dimensions`, `chunks`, `pushed_at`) · `knowledge_chunks` (`id`, `org`, `env`, `holder`, `base`, `path`, `heading`, `ordinal`, `text`, `embedding halfvec(1024)`), HNSW by cosine and BM25 in spanish · `knowledge_files` (`0041`: the files as pushed — `path`, `text`, `chunks`, `pushed_at` — under the base's row, what a person reads and edits one at a time; `knowledge/files.py`) | a base is one world's (`0018`): a sandbox push never replaces the one the telephone answers from; production's is pushed there directly, by a key that acts in production. A push replaces the base whole (`knowledge/store.py`); a chunk is embedded seeing its file's other chunks (`embed_documents`, one document per file); a `whole` file is one row with no vector that no search answers and the resolver reads entire into the static block (`api/agents/tuned.py:with_the_whole_files`) — the file the class used to carry by heart; a search is both indexes fused by reciprocal rank (`types/fusion.py`, the one fusion memory ranks with too), over every base the settings attach (`lookups/service.py`), and refuses a base another model pushed. Decision: *retrieval* |

Twenty-nine tables, forty-six migrations (`migrations/00NN_*.sql`, applied in order by `migrate
up`, the two `.post.sql` by `migrate up --post`; `0008_memory` holds the contact's facts and
`0010_memory_model` says which embedder wrote each one — **Fact** in `types/knowledge.py` is its
shape — and `0009_knowledge` the knowledge base's chunks, **Chunk** beside it). Decisions: *types*,
*orgs*, *keys*, *routes*, *tokens*, *provider-keys*, *log*, *memory*.

**A migration is never edited, and that is enforced, not asked.** `schema_migrations` keeps a
`sha256` per applied file and `log/store/migrating.py` refuses a checkout where one has changed:
every database that ran it has the OLD one, and an old migration is fixed by a new one. A run takes
an advisory lock before any DDL, holds each migration to a 5 s statement and a 1 s lock timeout
inside its own transaction, and names the database it talks to before applying anything.
`migrations/migrations.lock` names the last one that landed — bumping it makes two branches adding
`0022` conflict in git — and is the baseline `scripts/lint-migrations` lints above with **squawk**,
which reads the `.sql` for what it will do to a table that has rows. `tests/migrations.py` proves a
migration against data: a schema built as a box HAD it, rows written, the migration applied on top.

## 3. The wire

`pinecall-protocol` is generated from JSON Schema in the protocol repository for Python and
TypeScript alike, never edited by hand: `envelope`, `events`, `commands`, `verbs`, `metrics`,
`state`, `rest`, `room`, and the golden fixtures both reducers must fold to the same state. The
log's entry IS the wire's envelope (`log/entry.py`). The fifty-odd entry types, by family:

| family | types |
|---|---|
| the call | `call.started` `call.ringing` `call.dialing` `call.line` `call.log` `call.event` `call.ended` `call.summary` `call.score` `call.transferred` `callback.requested` · and `attention.requested` `.answered`, the agent asking for a person: the caller holds until a supervisor takes the line or the wait runs out (`session/attention.py`, `session/*/attending.py`) |
| the turns | `turn.user` `turn.agent` (each carrying livekit's ChatMessage metrics) |
| the tools | `tool.call` `tool.result` · the gate: `confirm.request` `confirm.granted` `confirm.declined` |
| the metrics, one per typed block | `metrics.llm` `.stt` `.tts` `.vad` `.eou` `.eot` `.interruption` `.realtime` `.avatar` — every field livekit measures, joined by `speech_id` |
| the room | `room.opened` `participant.joined` `.left` `.speaking` `room.sent` |
| the state | `state.changed` (the tenant's fields, by Visibility) · `memory.ops` |
| the agent's own log `@slug` | `agent.register` `agent.registered` `agent.configure` `agent.configured` |
| the two voices | `user.state` `user.transcript` `agent.state` `agent.transcript` — an `agent.transcript` entry is one **delta**, never the reply so far: in a voice call one word with the seconds the voice aligned it to, in a written call one model token; `reduce.py` joins every delta since the last `turn.agent` into `live.agent` |
| **commands** (app → call) | `agent.say` `agent.reply` `state.set` `session.configure` `call.hangup` `call.transfer` `call.attention` `call.callback` `call.hold` `.unhold` `call.dtmf` `call.dial` `room.invite` `room.send` `participant.mute` `participant.remove` — and `call.mute` `.unmute`, which are in the wire and have no runtime |

Two **projections** decide what leaves the platform (`log/projection.py`, `auth/scopes.py`, the
only two places that spell them): **public** — what a participant in the room may see: the
public state fields, a turn's `role`/`text`, one metric (`e2e_latency`), never `agent` or
`call`; **tenant** — everything, PII masked where it was written (`log/pii.py`), by the field
names the agent declared. A **scope** picks the projection a bearer reads through.

## 4. The gateway, process 1

`api/app.py` is one FastAPI process: a lifespan that opens the Postgres pool, the key table, the
routes, the vault and the meter, the embedder `EMBED_PROVIDER` names, memory, the knowledge base and
the one `Lookups` over them, then the routers `api/_doors.py` lists, one door each, in order — and
the one loop that answers to nobody, the **reaper** (`api/reaping.py`, §8). By resource:

| door | what |
|---|---|
| `WS /v1/apps` | **the app socket**. A tenant's process registers its class (`agent.register` → AgentConfig), holds the agent, receives the entries of the calls it answers, runs the tools, sends commands. `api/agents/socket.py`, `registry.py` (which sockets hold which agent, live, **namespaced by the key's `env` and by whose key it is**: `Held` is `(env, holder, slug)`, where the holder is nobody in production — what is deployed is the org's, whether a server's token or a person with production access holds it — and the member in the sandbox, so two developers of one tenant each hold their own and neither takes the other's; a sandbox key naming nobody holds the org's own, which is what a developer holding none falls back to. A dialled door is namespaced by neither: a number is one agent's in one world, and WHICH corner answers its ring is **the line** (`api/agents/doors.py`) — nobody's corner in production, where there is one, and in the sandbox the developer who claimed it, because an org shares one sandbox number. The first corner to hold an agent takes its line, a second one has to claim it, and it is handed on when the terminal holding it closes. A key that opens `team` AND `app` reads the table by CORNER and not by slug, so an admin and the box see what the whole team is running, each row saying whose it is — a manager opens `team` alone and no developer's copy (`holding(every_corner=…)`, `auth/keys.py:sees_every_corner`). `agent.registered` says the world), `api/agents/holding.py` (one agent as one socket holds it), `on_a_call.py`. **A call is its agent's, not a socket's**: when the socket serving it closes, `api/calls/attaching.py` hands it to the socket that would take a new call of that agent in that corner, or parks it for the next one that registers (a console takes none), and the socket that takes it hears `call.attached` and the tools still waiting. A written call, which runs in this process, is taken up from its log after a restart — `api/calls/taking_up.py`, `session/text/resuming.py` — [docs/protocol/a-deploy-never-cuts-a-call.md](docs/protocol/a-deploy-never-cuts-a-call.md) |
| `GET/POST/DELETE /v1/agents/{slug}/line` | **the line**: whose terminal a call that RINGS at this agent's doors lands in, the claim a second developer makes to take it, and the release that hands it on. Reading it takes `calls`, claiming it takes `app` — a ring lands on the line, so a corner with no app in it would take the call and drop it. `api/agents/endpoints.py` |
| `GET /v1/agents/{slug}/config` · `/provider-keys` · `/pipeline` | what the **worker** asks about an agent: the declaration with the corner's settings on it (the one door that opens to `app` OR `calls`, because the console reads it too and a person holds no `app` in production — `DeclarationKeyDep`, pinned by path in `tests/api/test_scopes_at_the_doors.py`), the org's own keys (the one door that ever answers with a key), and what it hears/thinks/speaks with |
| `GET`·`PUT /v1/agents/{slug}/settings` · `…/history` · `…/diff` · `POST …/rollback` · `GET /v1/calls/{call}/settings` · `/v1/lexicon[/history]` | **the settings**: what the org set over the class, per world, per corner, a version a row — the three corners as the key sees them (yours, the team's, production's), the whole set written with the version it was read at (409 when the corner moved), one corner's history, a diff, a rollback that is a new version. A set writes the world the request runs in: in production the org's own corner, by a key that acts there — there is no promote; the goldens run in CI before a deploy. `pipeline` sets everything; `words` (a supervisor's, a manager's) the opening's words, the lexicon and what is remembered, refused the rest by name. What a call ran on, by the versions its head row kept. `api/tuning.py`, `api/lexicon.py`, [docs/protocol/settings-api.md](docs/protocol/settings-api.md) |
| `POST /v1/calls` · `POST /v1/calls/{call}/events` · `/sealed` · `/reopened` · `GET /v1/calls/{call}/commands` · `POST …/tools` · `POST …/lookup` · `POST …/remember` | the **worker's** side of a call: open the log, append entries, seal, read the app's commands, relay a tool to the app that declared it and wait for the answer, run `recall` or `search` against memory and the knowledge base — the app's own `this.knowledge.search` asks the same `lookup` door, its answer the wire's `SearchFound {chunks: [{path, heading, text}]}` — remember the call at hang-up. Opening, appending and sealing are `api/calls/worker_doors.py`. A gateway that restarted forgot every call it served; every door that names one answers it 404, and the worker — which holds the call and the very context it opened it with (`worker/client.py`) — says it again at `/reopened`, once, and asks again: no quota, no token, no second `call.ringing`, and the socket holding the agent hears `call.attached` |
| `GET /v1/calls/{call}/events` (SSE) · `/state` · `/recording` · `GET /v1/agents/{slug}/sessions` · `/calls` | the **readers**: a log as it happens (backlog, marker, live — `log/replay.py`), the folded state memoised per seq (`log/snapshots.py`), the audio, the listing |
| `WS /v1/chat` · `WS /v1/attach` | a **text call** from a terminal or a browser, served in this process; attaching to one |
| `POST /v1/calls/{call}/listen` · `/supervise` · `/verbs` | **the desk**: a supervisor's hidden ear, a seat in the call, the six supervise verbs. Whose call it is, is what its LOG says (`api/calls/sink.py:another_orgs`) and never who holds the agent's socket: a key of that org steers it, whatever world the key was minted into |
| `POST /v1/evals/run` · `/replay/{call}` · `/caller` · `/voice` · `GET /v1/evals/runs` | **rings 1–3** driven from the gateway: a suite over live text sessions, a finished call re-checked by code, the next line of a simulated caller, one simulated caller on a real line |
| `GET /v1/personas` · `PUT`·`DELETE …/{name}` | **the org's synthetic callers** (`evals`): one list an org — no agent in the path — and not per world, each a goal, a manner and the facts it may state. A PUT writes one whole — nothing merged — and `was` in the body renames the caller it names (`404` for a name nobody wrote, `409` for one somebody else holds); a name is lower-case words joined by hyphens or `422`. `api/personas.py`, `orgs/personas.py` |
| `GET /v1/personas/{name}/runs?limit=&before=` | **what that caller has done** (`evals`): every simulation it has run in the key's world and corner, newest first — the call, the agent, when, how many turns the caller took, how it ended, its outcome, what it cost and how the judges answered. Read off `call_facts.persona` (`0046`), which `log/facts.py` projects from the name the call's own `call.started` carries beside `run` — put there by the chat socket (`/v1/chat?persona=`) on the written path and by the dispatch (`types/dispatch.py`, `worker/router.py`) on the spoken one, where the WORKER is what writes that entry. Paged exactly as the sessions list is. `api/persona_runs.py` |
| `POST /v1/tokens` | LiveKit's token endpoint with our three things in front: minted only for an agent the key's org answers, single-use, the dispatch riding it |
| `GET /v1/routes` · `/v1/ops/routes` · `/v1/ops/orgs` · `/v1/ops/orgs/{org}/keys` · `/quotas` · `/provider-keys` · `/v1/ops/usage` | the tenant's read, and **the operator API** (`docs/protocol/operator-api.md`), opened by `PINECALL_OPS_KEY` or by the key of a person the box made an operator (`api/_operator.py`) — what `pinecall/cloud` talks to |
| `GET/POST /v1/whatsapp/webhook` | Meta's handshake and every delivered message; one thread per contact per number, each a text call (`api/whatsapp/`, `whatsapp/`) |
| `GET /v1/apps` · `POST /v1/apps/{app}/stop` | **the processes holding the org's agents right now**, one per app socket (`api/agents/processes.py`, in memory like the registry): agents, world, the machine the app named at `agent.register` (`host`), address, SDK, whose corner, since when — every corner's for a key that opens `team`. A stop (`app`, the request's world, a corner the key may see) sends `error` `stopped` and closes the socket; the SDK exits instead of reconnecting, and a supervisor starts it again. `api/processes.py` |
| `GET /v1/keys` · `POST /v1/keys` · `POST /v1/keys/{fingerprint}/revoke` | **the org's tokens**, scoped to the key's org, which it cannot name: the listing (any key) is every server's token and the asker's own person keys — every person's for a key that opens `keys` — each `{fingerprint, label, kind: person or server, env (null for a person's), name, created_by, created_at, last_used_at, revoked_at, scopes}`, never a key; a POST `{label, env}` makes a **server's token** — only on a person's key (`403` otherwise) that opens `app`, in the instance's world only (`400` naming where the other's is made) — with the fixed `SERVER_SCOPES` (`app`·`calls`·`talk`·`knowledge`·`evals`), `created_by` the maker, and answers it in the clear the once; it does not die when its maker leaves. Revoking (any key) is a POST because the row stays: your own keys, the tokens you made, or any with `keys`; anything else is the same 404 as a fingerprint that is nobody's. `api/keys.py` |
| `PUT /v1/provider-keys/{vendor}` · `GET /v1/provider-keys` · `DELETE /v1/provider-keys/{vendor}` | **the keys a tenant brought of its own** (`providers`), on the tenant's key and scoped to its org, which it cannot name: bring one (`{key}` → 204, replacing whatever that vendor had), read the vendors back by name and never a value, take one back (404 for a vendor never brought). An alias is resolved before it is stored, so `11labs` and `elevenlabs` are one row. A build that knows no such vendor is 400 with the list; a runtime with no `PINECALL_VAULT_KEY` is 503. `api/provider_keys.py` |
| `GET`·`PUT /v1/agents/{slug}/pipeline/hold-audio` · `…/audio` · `…/played` · `GET /v1/agents/{slug}/hold-audio[/audio]` | **the hold melody**: what a caller hears while a tool runs — the runtime's own (`session/a-new-life.ogg`), off, or a file uploaded as the body and converted once to Opus 48 kHz mono (`session/hold_audio.py`, `hold_audio` 0036). Its own doors, never a field of the settings' whole-set PUT. The worker asks the second pair on the fleet's key, keeps a clip by its hash (`worker/hold.py`), and `session/voice/hold.py` plays it on livekit's `BackgroundAudioPlayer` around each tool's round trip, after a grace AND never before the agent has stopped talking — the model emits the line that announces a tool and the tool call in one response, so on a clock alone the melody came up underneath the agent's own voice every time. `api/hold_audio.py` |
| `GET /v1/providers` | **the catalogue** (`providers`): every vendor this build runs — what each does, every word it answers to, whether this box has its plugin and whether it holds a key — plus the vendor each stage runs on when an agent declares none (`defaults`, one per modality), the models this build vouches for under `<modality>/<vendor>` with each vendor's default first (`models`, off the tuned vendor files — a vendor with no entry runs its own default and takes no model name), and the voices this build curates. Never a key, not even a prefix. The same rows ride inside the pipeline report, built by the same function, so the two screens cannot disagree. `api/providers.py`, `providers/catalog.py` |
| `PUT /v1/knowledge/{base}` · `GET /v1/knowledge` · `DELETE /v1/knowledge/{base}` · `POST /v1/knowledge/{base}/eval` · `GET /v1/knowledge/attached` | **the bases** — the RAG, which is a different thing from what the agent knows by heart — on the tenant's key: a base pushed whole, in the request's world (`{files: [{path, text}]}` → `{base, chunks, took_ms}`), in production production's base directly, listed, dropped (404 for a name never pushed); **the golden** — questions naming the heading path that should answer them (`{asks, expects}`), asked of the base and answered as `recall@k` and `nDCG@10` by code with no model (`knowledge/scoring.py`, the arithmetic shared with memory's golden in `types/goldens.py`); and which agents' settings read each base. `agent.configure` refuses a class whose world attaches a base never pushed there, naming `pinecall docs push`, and one that searches for itself with none attached (`api/agents/socket.py:NO_SUCH_BASE`, `NO_BASE_ATTACHED`). `api/knowledge.py` |
| `GET /v1/contacts/{contact}/memory` · `DELETE` · `POST /v1/contacts/memory/eval` | **a contact's memory**: every fact ever held, current first; forget, the right to be forgotten (`{forgotten: n}`); and **the golden** — questions that bring their own facts (`{holds, asks, expects}`), written to a scratch contact, recalled, deleted, and answered as `recall@k` and `nDCG@10` by code with no model (`memory/scoring.py`, the arithmetic shared with the base's golden in `types/goldens.py`). `api/contacts.py` |
| `POST /v1/agents/{slug}/memory/extraction` | **the write side of memory, held to its own goldens**: a call already written down (both speakers) and the facts already held, one hang-up extraction per case on the org's own model and keys, and four questions asked of what came back — which categories got a fact, which never did, which values must not survive in any fact's text, which held facts were superseded, and that a planted sentence was refused. Judged by code, never by comparing sentences. The body and the answer are the schema's own `ExtractionCases` and `ExtractionRun`, so a transcript is a list of `[who, what]` pairs in all three languages. `api/extraction.py`, `memory/goldens.py`; the verb is `pinecall remember` |
| `GET/POST /v1/members` · `PATCH`·`DELETE /v1/members/{id}` · `POST /v1/invitations/{token}` · `POST /v1/login` · `POST /v1/login/codes` · `POST /v1/login/redeem` · `GET/POST /v1/ops/orgs/{org}/members` · `DELETE …/{id}` | **the org's people**: invite (the one-use token, once — by the org, or by the operator for an org's first admin where sign-ups are shut; the operator reads and invites and changes nobody), list, change the role, the agents, the standing or `production` — the switch that lets a person act in production; an admin always does, and `production: false` on one is `409` (disabling revokes their keys), remove one for good (keys revoked first, the row and its links gone, the seat free; `409` for yourself and the last active admin — the operator's twin in `api/ops_members.py`); accept an invitation with a password and take the first key; log in with org, email and password — argon2id, five tries a minute per name, one 401 sentence for every wrong thing — or with a one-use code a key holder minted so a browser never carries a key in a URL. **Production is the identity**: `POST /v1/login/redeem` spends a code a person carried to the sandbox and answers the org and the member as its rows stand, `disabled` included, never the production switch (`auth/identity.py`), and every door a person is made, changed or proved at — the member doors but the listing too — is production's: `404` on a sandbox, naming `PINECALL_IDENTITY_URL` (`api/identity.py`, `AtProduction`). A sandbox's `POST /v1/login {code}` redeems a code it never minted there, mirrors the org and the member (a stale row of the address goes, its keys revoked; a member disabled there has every key here revoked and is `403`), and mints a key that lives a day. An invitation and an admin's reset are also **mailed** (the answer gains `mailed`), and `POST /v1/login/reset {email}` is the person asking for their own link — `202` whoever asks, the login's throttle, a token minted only where a letter can carry it and the org does not sign in with a provider. `GET/PUT/DELETE /v1/org/mail` · `POST /v1/org/mail/test` (`team`, 0034) wire an org's own SMTP account, its password under the vault key, which wins over the box's own mail — the mailbox the operator stored at `PUT /v1/ops/mail`, else `PINECALL_SMTP_URL`; with none nothing is sent. The letter leaves AFTER the door answers, and what came of it is on the org's row (`verified_at`, `last_error`) or in the box's log. `api/members.py`, `api/_seating.py` (may this key grant that role or switch; is the link this admin's to hold, or the person's alone), `api/login.py`, `api/forgot.py`, `api/org_mail.py`, `mail/` (generic SMTP, no vendor), `auth/members.py`, `auth/granting.py`, `auth/passwords.py`, `auth/codes.py`, `auth/throttle.py` |
| `POST /v1/login/pairings` · `GET/POST …/{code}` · `GET …/{code}/key` | **signing a terminal in**: `pinecall login` holds no key and the person at it has none to paste, so the terminal prints a word, the browser holding their key approves it, and the terminal collects a key of its OWN — the same person, the same role, labelled as that machine; `--prod` names production per request. A password is typed into a page and never into a shell, which is also why SSO changes nothing here: a person signs in to the console with their org's provider and approves the terminal as always. `auth/pairing.py` (the words), `api/pairing.py` (the four doors), `docs/protocol/people.md` |
| `POST /v1/agents/{slug}/dev/{family}/{verb}` | **the directory verbs**, relayed: the console asks the gateway, the gateway sends an ephemeral, unstored `dev.request` down the app socket holding the agent — the newest `pinecall start` in the key's world, or the one `?app=` names — and hands back the `dev.answer`'s result, or its refusal's status and sentence verbatim (`api/agents/dev.py`, the waiting room in `api/_live.py`; `docs/protocol/dev-verbs.md`). Four doors, one scope each: `chat` → `talk`, `knowledge`, `memory`, `evals` |
| `GET /v1/sessions` · `GET /v1/events` · `GET /v1/ops/events` | **the org's floor**: every agent's newest calls in one list (`store.calls_of`, off the head rows' `org`), and the floor changing as SSE — `agent.registered`, `call.ringing`/`dialing`/`started`/`ended`, `attention.requested`/`answered`, `supervisor.took_over`/`released` — tapped off every log the org owns as it is written (`log/writers.py`, `Logs.feed`, `ORG_EVENTS`), live only. `api/floor.py`. **The box's floor**, the operator's: every org's at once (`Logs.box`), each frame `BoxEvent {org, entry}`. `api/ops_floor.py`; the generic SSE writer both use is `a_stream` in `api/calls/sink.py` |
| `GET/PUT/DELETE /v1/org/sso` · `GET /v1/login/sso` · `/v1/login/sso/callback` · `POST /v1/login/sso/discover` | **an org's own identity provider** (0030): what an admin wires on `team` — the issuer, the client, the email domains it admits, who an uninvited address is seated as, whether a password opens the org at all — and the sign-in itself, authorization code with PKCE, state and nonce. The client secret is sealed under the vault key and read back by nothing. What the callback hands the browser is the SAME one-use login code `pinecall start` prints, so no key is ever in a URL. `api/sso.py`, `api/login_sso.py`, `orgs/sso.py`, `auth/openid.py`, `auth/sso.py`; `docs/protocol/people.md`. **Box-wide, the operator's**: `GET /v1/login/google` · `/callback` — one OAuth client at Google for every org's people (`GET /v1/ops/signin`, `PUT`·`DELETE /v1/ops/signin/google`, `api/box_signin.py`, `orgs/signin.py`: a table of known issuers and one `box_settings` row each), the same OpenID code, the address matched against every org's members and a member still invited seated by it; nobody is `302 /?refused=…`. `api/login_google.py`, [docs/protocol/the-box.md](docs/protocol/the-box.md) |
| `GET /v1/insights` · `/v1/org/judging` · `/v1/agents/{slug}/threads` · `/memory` · `/widget` · `POST /v1/evals/judge/{call}` | **the console's doors** ([docs/protocol/console-api.md](docs/protocol/console-api.md)): a day counted, judging on or off (`JudgedWhen`, `evals/score.py`), the inbox by contact with a person's read cursor, an agent's facts across contacts, the widget's settings (`orgs/widgets.py`), a call judged later — all read off the call index (`log/facts.py`, `store/index.py`), never a fold of every log. `api/insights.py`, `judging.py`, `threads.py`, `agent_memory.py`, `widget.py`, `evals/judge.py` |
| `PUT/GET/DELETE /v1/carrier` · `GET /v1/numbers/available` · `POST /v1/numbers` · `DELETE /v1/numbers/{number}` | **a tenant's own numbers**: its carrier brought (a Twilio account verified once, or a SIP peer), what the account owns, one number imported in three looked-up-first writes — the carrier's trunk, the SFU's trunk, the route — or the plan alone with `?dry_run=true` (`409` for a number another trunk on the SFU already lists: livekit-sip refuses an INVITE two trunks match), and one let go. A number is one instance's: nothing moves it to the other world. `api/numbers.py`, `orgs/carriers.py`, `routes/twilio.py`, `routes/trunks.py`; `docs/protocol/numbers.md` |
| `GET`·`POST /v1/carrier/outbound` · `POST /v1/agents/{slug}/dial` | **calling somebody back**: whether the org can place a call at all and one sentence per thing missing, the provisioning of the trunk it dials THROUGH (Twilio's termination label and a credential list minted once, or the peer the tenant declared, then the SFU's outbound trunk) or the plan alone with `?dry_run=true` — and the door that places one, `202` with the call it became, after the guards and the `dials` ledger row. `api/outbound.py`, `api/dialling.py`, `api/_placing.py`, `orgs/guards.py`, `orgs/dialling.py`, `orgs/outbound.py`, `routes/outbound.py`, `routes/dispatching.py`; `docs/protocol/numbers.md`, `console-api.md` §4 |
| `POST /v1/numbers/buy` | **a number the box buys for the org** on its own Twilio (`TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`, `TWILIO_API_SECRET`): Twilio's search for one in the country and area asked, the purchase, the box's trunk `pinecall`, then the SFU's trunk and a route flagged `managed` — the stock the `numbers` quota caps, refused `429` before the carrier is asked; production's alone, `404` on a sandbox. `api/managed.py`, the same steps as the import; `docs/protocol/numbers.md` |
| `GET /v1/usage` · `GET /v1/numbers` | the org's own tables on the tenant's key: its metered rows and totals (`usage`) and its doors with their source (`numbers`). `api/usage.py`, `api/routes.py` |
| `POST /v1/signup` | **a new org**, no key, where `PINECALL_SIGNUP` is on — off by default, its own flag and not `cloud`, because a box run for its own agents wants no stranger making one: the org, allowed what the policy plugged into `extensions.admitted` says in the same breath it is made (the runtime's own answer is no limit and no row), its first admin invited and accepted — the same path a person invited later walks — their first key and a login code for the browser. Throttled per client; a box of its own refuses. `docs/protocol/people.md` |
| `GET /.well-known/pinecall` | **discovery**, no key: `{version, world, elsewhere, cloud, signup, min_password, mail, brand, google}` — which runtime and which world answer here, where the other world's instance is, whether it is Pinecall's hosted gateway (`PINECALL_CLOUD`, which means a plan is billed) and whether it takes sign-ups at all (`PINECALL_SIGNUP`, off unless set — the two are separate facts). `api/discovery.py` |
| `GET /{path}` — the LAST route | **the console**: the built page from `src/pinecall/gateway/console/` (package data `scripts/console` copies in from the agents repo, git-ignored, shipped in the wheel) for every path that is not a door's, its assets as themselves, and a JSON 404 under `/v1/` and `/.well-known/` as before. One catch-all, and `tests/api/test_the_pages_are_served.py` pins that it is one and last. `api/pages.py` |
| `GET /v1/whoami` | the name on the key that knocked: the org, the key's id and label, the world this request runs in, its scopes, whether it may act in `production`, the person it was minted for (`subject`, `name`, `email`), whether they run the box (`operator`) and whether they are `visiting` — an operator inside an org they are no member of, whose key names `operator:<email>` and no member row, and verifies only while an active row of that address carries the flag (`auth/visiting.py`, `StandingKeys`; the switch is `api/login_orgs.py`) |
| **every tenant door** | reads the key in the instance's world, holding `pinecall-env` against it, then the corner (`pinecall-corner`) — `auth/world.py:as_asked` as it acts behind `opening`, `as_itself` as an identity behind the bare `KeyDep`, `403` with the sentence when the key may not open that world — and asks it for exactly ONE scope by the dep it takes (`api/_deps.py`, `opening(scope)` → `AppKeyDep`, `CallsKeyDep`, …); the read doors ask `calls` at `the_reader`, the verb doors ask `supervise` of a key; `403 this key does not open X: it opens …` (`auth/keys.py`, `not_opening`), and both sockets close with that sentence. `tests/api/test_scopes_at_the_doors.py` walks the app and refuses a door that declares none or two. A seat minted from a person's key carries `pinecall.subject` and `pinecall.name`, so a supervise verb is written down as theirs (`tokens/seating.py`, `supervise/aiming.py`). A browser calls one from another origin only as the mobile app: its two WebView origins and `PINECALL_APP_ORIGINS` are echoed under `/v1`, any other gets no CORS header (`api/app_origins.py`, Starlette's `CORSMiddleware` behind an allowlist) |

**What the process keeps in memory** (`api/_live.py`, `Live`): the open app sockets, the text
sessions running here, the tool calls in flight waiting on an app, and the calls being **served** —
each bound to the app socket that took it, with its subscription, the queue its commands wait in for
the worker, and the `CallContext` and `AgentConfig` the door that opened it knew, which is all a
lookup ever asks of a call (`Live.the_call` → `lookups.OpenCall`). None of it is durable and none of
it should be: "it is a fact about which sockets are open right now, not a fact about the world. The
world is the log." Decisions: *api*, *dispatch*, *supervise*, *whatsapp*, *eval-runner*.
A gateway that opens no Postgres pool holds no memory and no knowledge: a lookup finds nothing and
refuses nobody, and the knowledge and contact doors say so in one sentence each (503). It cannot
verify a key either, which it says at startup and at every keyed door — one runtime, one table.

## 5. The worker, process 2

`worker/main.py` builds one `AgentServer` from the settings — SFU URL and key pair **handed** to
livekit — registers `rtc_session(job, agent_name=<fleet>)`, the instance's `PINECALL_FLEET` (an
empty name would answer every room in the deployment: refused), keeps `PINECALL_IDLE_PROCESSES`
warm (livekit's one per CPU unless set) and reports **slots** (`active_jobs / PINECALL_MAX_JOBS`)
when measured, the CPU otherwise; livekit-server stops routing at 0.7 (`worker/load.py`, `warmed`).

`worker/entry.py`, `answer(ctx, worker)`, is the whole job:

1. `ctx.connect()` and the routes, in one wait; `worker/router.py` says who the job is for — the dispatch, then the
   number dialled, then the default (`worker/seat.py`: the caller). A production ring from a developer's own phone is
   **handed over**: a dispatch into the same room to the fleet `rings-for` names (their sandbox's), and the job ends.
2. The agent's config and the org's provider keys, in one wait, from the gateway
   (`worker/client.py`, the worker's **only** door — HTTP, the fleet's key; no database, no
   cache). Each is asked for the corner the dispatch named — the org, the world, the holder
   (`worker/router.py:whose`, `types/dispatch.py`) — so the one worker builds a session from THAT
   org's declaration and runs it on that org's keys; a phone call on the box's own trunk names no
   org, and `GET /v1/routes?number=&channel=` finds its one door across every org.
3. `POST /v1/calls`: the gateway opens the log and binds the call to an app socket.
4. The audio, when the agent's world keeps it (`record`): `worker/recordings.py` composes the one path a recording has, `worker/egress.py` asks LiveKit for a **room composite** of this room — audio only, one mix (never dual-channel: it drops the melody, measured), the shape that runs on the SDK — here, before the session, so the greeting is inside it, and stopped before the summary states the pointer. The ROOM, so the hold melody and a supervisor's voice are in it; a recorder that refuses is a call with no audio and the doctor's `egress` line is what says so.
5. `session/voice/kit.py` asks `providers/` for the three vendor objects the declaration names; `session/voice/session.py` builds the `AgentSession` — with livekit's **preemptive generation off**, on a spoken call as on a written one (`SPOKEN_PREEMPTION`): with it on, an end of turn landing inside a tool's execution window starts a whole new reply on a context the tool's answer is not in yet, and a call was measured asking "Is this a house?" and answering "Yes, a house." in its own voice under the same speech id — that round ran on 2,923 prompt tokens where every other ran on 4,300 to 4,900, and it paid for a second, discarded model call per turn. Half a second of latency is what it costs to be sure the agent is never both parts; **`VoiceBridge`** (`session/voice/voice.py`) sits between it and the platform.
6. `worker/commanding.py` streams the app's commands off `GET /v1/calls/{call}/commands` and
   applies each to the bridge, until `None` — the call ending.
7. Hang-up: the bridge writes `call.ended`, asks the gateway to remember the call
   (`POST /v1/calls/{call}/remember`, under `PINECALL_REMEMBER_BUDGET_S`), writes `call.summary`,
   hands its own log to the `Scorer` (`session/scoring.py` → `evals/score.py`), writes
   `call.score`, and the worker seals the call.

**The bridge** hooks livekit's session and turns its life into entries: `events.py` (every
transcript, state, turn, error → an entry), `metrics.py` (every measured block), `writing.py`
(the entries, in order, to the gateway), `tools.py` (every declared tool, once its announcement has played: out to the app's
process, the answer back, a `confirm` receipt heard before the model replies), `hearing.py` (the keyterms the ears are told to expect: the words the agent
declared and the names its state holds), `barge_in.py` (two words cut the agent off, never two
words of agreement), `supervising.py` (the six desk verbs), `commands.py` (say, reply, rewrite the
prompt, stop), `dead_end.py` (a failure whose cause cannot change ends the call instead of
retrying), `room/` (the room's events as facts; invite, mute, remove, send; the DataChannel to
browsers, projected public), `sip.py` and `transfer.py` (the caller's leg; a cold transfer by
REFER). Decisions: *worker*, *voice-bridge*, *room*, *sip*.

## 6. A session, on either channel

`session/` is one call on either channel, and what both share: the `Scorer` seam, the pending
tool calls, the supervise verbs, `clock.py` (today's date as a tool call the model appears to
have made, never a system message), `declaring.py` (our ToolSpec as livekit's tool).
**Voice** runs in the worker, in a room, with audio — and so does a browser's `chat` visit, the same
session with no ears and no voice (`spoken=False`, `worker/entry.py`), which keeps no recording.
**Text** (`session/text/`) runs in the **gateway** — WhatsApp, `/v1/chat`, `pinecall chat`, the
ring-1 runner — as one livekit `AgentSession` driven by hand, one turn per message, no room, no
ears, the same entries under the same names; it measures nothing itself, livekit's `LLMMetrics` is
the measurement. The **prompt** on both is a list of named blocks (`types/prompt.py`, `Blocks`) in
two regions, in one order — static blocks (cached by the vendor; `identity · knowledge · tools` by
default) · append-only history · the dynamic region at the end, which is the tenant's **view** of
its state and nothing else. The app writes a block by name with `prompt.set`; the static ones are
livekit's `instructions`, rewritten only when their joined text moved, and `providers/blocks.py`
builds each request: the history, then this turn's lookups, then the dynamic blocks, one message
each, and for Anthropic one `system` string per static block, so a rewritten `tools` block leaves
the others cached. The tenant never writes a prompt: the class is the prompt, `render(state)`, and
`knowledge` is what the world's settings say the agent knows by heart, in a static block.

**Memory and the knowledge base are two declared tools**, `recall` and `search` (`types/lookup.py`),
and the class's declaration is what brings each one: `memory` declares `recall`, `docs` declares
`search`. They stand in the request's `tools` array beside the app's own, and their answers reach
the model **as `tool_result` blocks, JSON-encoded** — the one place both vendors name for anything
that arrived from outside the conversation (`docs/security/prompt-injection.md`, a public contract).
With `docs.mode = "retrieved"` (the default) and whenever `memory` is declared, the session runs the
lookup itself and puts a real `FunctionCall` + `FunctionCallOutput` pair into the request, paired by
`call_id` so livekit's formatter groups it (`session/lookups.py`, the same shape `clock.py` puts
today's date in). **On a spoken call it starts while the caller is still talking**:
`session/voice/events.py` hands every interim transcript to `TurnLookups.heard_so_far`, and the
first one carrying four words (`WORDS_ENOUGH_TO_SEARCH_WITH`) starts one task per tool, asked with
the caller's words so far. One run per turn; livekit's `on_user_turn_completed` consumes it and
drops it — already back, it is read with no wait; still out, its tail is awaited under
`PINECALL_VOICE_LOOKUP_BUDGET_MS`; never started, because the turn was too short, it runs there and
then. A text turn has no interim and runs the whole lookup at turn end, under
`PINECALL_TEXT_LOOKUP_BUDGET_MS`, which is larger because nobody hears a chat's silence. Past the
budget that tool's pair is left out and an `error` entry (`recall_skipped`, `search_skipped`) says
why — per tool, so a `recall` that answered is used beside a `search` that did not; with `docs.mode
= "tool"` the platform runs nothing and the model calls `search` itself, through the same callable.
The pair is rebuilt every turn and never kept in the history, so the cached prefix never moves. At
hang-up, between `call.ended` and `call.summary`, the session's `Rememberer` writes what the call
taught about the contact; a miss is `remember_failed`, recoverable, and the call seals. **A lookup
is run by the gateway, never by the app**: the voice session's `Lookup` is the worker's gateway
client (`POST /v1/calls/{call}/lookup`, `/remember`), the text session's is the gateway's own
`lookups/`, in-process — one `Lookups(memory, knowledge, logs, calls, keys_of, quotas_of,
may_remember)` per process, implementing both protocols, that recalls the contact's facts (the
contact is `CallContext.remembered_as`: the resolved id, else the number on phone and WhatsApp, else
nobody — never what the model wrote in the tool's input), searches every base the world attached to the agent
(`config.bases`), each under its own `k`/`min_score`, writes `memory.ops` and `docs.sources` on the call's log with the turn's
`speech_id`, and names the embedder's vendor and URL in the error entry when it is down. The answers
are `{"facts": [{text, source, since}]}` and `{"chunks": [{path, heading, text}]}` and nothing else.
The last two arguments are the org's PLAN, asked of `orgs/` (which `lookups/` may not import): a
tool whose quota is `0` finds nothing, embeds nothing and writes no entry — a plan without the
feature is not a failure and never reads as one — and a hang-up whose org may keep no more facts
writes `memory.ops` with an op that kept none, its `credits.exhausted` one entry away in the agent's
log, and asks no model to extract what it could not store. **A tool runs in the tenant's process**:
the session sends `tool.call` to the gateway, which relays it down the app socket the call is bound
to, the tenant's `@tool` runs where it was written, `tool.result` rides back to the model.
Decisions: *text-session*, *prompt-blocks*, *memory*, *retrieval*, *livekit-context*,
*livekit-words*.

## 7. The path of a call, door by door

| door | arrives | becomes | answered by |
|---|---|---|---|
| **telephone** | the carrier's trunk → livekit-sip → a room, the SIP leg a participant | the dispatch names the fleet; `router.py` finds the route by the number dialled | the worker; a **VoiceBridge** |
| **web** | `POST /v1/tokens` with the org's key → a room token with the agent's dispatch and the scope inside, single-use | the browser joins; the dispatch spends the token | the worker; the log to the browser over the DataChannel, projected public |
| **WhatsApp** | Meta → `POST /v1/whatsapp/webhook`, signature over the raw bytes | one thread per (contact, number) = one text call, `whatsapp/routing.py` picks the agent | the gateway; a **TextSession**; `whatsapp/sending.py` puts the agent's words back on Meta's API |
| **a terminal** | `WS /v1/chat` (`pinecall chat`, `pinecall-runtime chat`) | a text call, `?app=<id>` binds it to the console that opened it | the gateway; a **TextSession** |
| **a call back** | `POST /v1/agents/{slug}/dial` with the org's key → the guards, then a dispatch into a room named by the call id | the worker places the SIP leg itself, through the org's outbound trunk — and a leg dialled INTO a live call (a warm transfer, `room.invite`) passes the same guards and the same ledger at `GET …/outbound-trunk?to=`, the stranger fence excepted | the worker; a **VoiceBridge**, once the far end answered |

Every door ends alike: `call.summary` (livekit's usage rows, cost from `providers/prices.py`), the
judges, `call.score`, the seal. Decisions: *tokens*, *dispatch*, *whatsapp*.

**An outbound call runs backwards**: the gateway mints the call id, passes the guards, writes the
head row and `call.dialing` (both numbers, and who asked) and dispatches a worker; `from` is the
org's number, `to` the far end — the **contact**, what memory files the call under. The job that
will answer places the leg (`worker/dialling.py`, before the session is built): `wait_until_answered` is the only way busy and no_answer are knowable at all, so a call nobody picked up writes `call.ended` with the protocol's own word for it and seals, with nothing said into a room the far end never entered and no bridge to unwind. The dispatch carries the trunk, the number, the one to show and the ceiling — only the gateway writes a dispatch, so the worker asks no door and invents no limit — and the channel is what the dispatch says and not what the SIP seat says, since there is no seat yet: this job is what creates it.

## 8. The log

`log/` imports no framework. `entry.py` (the envelope), `store/` (the Store protocol; Postgres,
the one door to a driver; memory for tests; the pool), `fanout.py` (every live reader, bounded
queues — a slow reader is dropped, an append never waits), `replay.py` (backlog, marker, live),
`reduce.py` + `room.py` (fold entries into State; the TypeScript reducer keeps the same rules),
`snapshots.py` (one reduction per call per seq, for the whole process), `projection.py` and
`pii.py` (§3), `filters.py` (what a reader asked for), `latencies.py` (livekit's names off the
two entries a turn lands as), `usage.py` (what an org consumed, folded from `call.summary` and
`call.score`), `writers.py` (which logs this process is writing), `facts.py` (one row per call — door, numbers,
contact, the persona being played (0046), end, cost, verdict, whether a person took part, e2e,
last words — folded by the store in the append that writes each entry; `store/index.py` the
questions a list, a day, an inbox and a persona's own runs ask across calls, `call_facts` in
Postgres, 0025). The `seq` is born under the
database in the same INSERT; ephemerals spend a seq and leave no row; `ts` is the runtime's
clock. **Compact the view, never the log.** Decision: *log*.
A log is sealed by whoever ran the call, and a worker that is **killed** seals nothing: the
gateway's **reaper** (`api/reaping.py`; `docs/protocol/console-api.md` §2) finishes, as `drained`,
every spoken call quiet for five minutes whose room the SFU no longer has (`routes/rooms.py`).

## 9. The tenants

`orgs/table.py` (the orgs and their quotas), `orgs/tuning.py` (an agent's tuning and the org's
lexicon, a row a version per world and corner — the version born in the INSERT, the primary key the
only lock, and `VersionMoved` when two writers read the same one), `orgs/resolving.py` (what a chain of corners resolves to: every knob from the nearest corner that SET it, an empty row supplying nothing — one definition, both stores read through it and no door resolves anything of its own), `admission.py` (may this org open one more call, hold one more agent, keep one more fact, push these chunks — one refusal vocabulary,
`credits.exhausted` in the agent's own log and the same sentence at the door), `meter.py` (every org's consumption, folded from the log as it grows, one
cursor per process), `vault.py` (a tenant's own provider keys, Fernet at rest, written at two
doors — the tenant's own and the operator's — and read back by exactly one, the worker's). `auth/keys.py` (sha256, no salt; the `api_keys` table and nothing beside it — `PINECALL_DEV_KEY`,
one key that needed no database and was then the ONLY key honoured, is gone, and with it the
second runtime a laptop used to be; its Postgres store is `auth/keys_postgres.py`), `auth/world.py`
(the instance's one world held against the request: a header is an assertion, a server's token
has its own; a person read as an identity or as it acts), `auth/persons.py` (every person's key minted in one place, the role's scopes whole), `auth/granting.py` (a key grants what it holds: a role whose preset opens no door the key does not, production access only from a key that has it — asked at the invitation, the row's PATCH and the role SSO seats a stranger with), `auth/bearer.py` (one parser of the header, one close code),
`auth/scopes.py` (which projection, and the room token that carries one call, one scope),
`auth/members.py` (the org's people and their invitations), `auth/passwords.py` (argon2id, the one
slow hash in the tree, for the one secret a person invents), `auth/codes.py` (one-use login codes,
this process's memory), `auth/throttle.py` (so many tries per name per minute at the password door),
`orgs/sso.py` (one OpenID provider per org, its client secret under the same vault key), `orgs/mail.py` (one SMTP account per org, its password the same way, and how its last letter went), `orgs/box.py` (**what the operator configured for the box itself**, 0035: one row a setting — `brand`, `mail`, `signin.<provider>` — its one secret under the same vault key, the value merged for a standing; the table exists without a vault key, since a brand is no secret. `mail/box.py` reads the box's mailbox off it, stored over the environment's; `mail/brand.py` the brand; `api/box_mail.py`, `api/box_brand.py` are the doors, [docs/protocol/the-box.md](docs/protocol/the-box.md)),
`auth/openid.py` (discovery, the code exchange, an id_token checked against the issuer's JWKS),
`auth/sso.py` (the sign-ins between the redirect and the callback, one use and ten minutes), `auth/identity.py` (what production answers about the person a spent code names, and a sandbox asking it over HTTP at `PINECALL_IDENTITY_URL`; `api/identity.py` mirrors the org and the member by production's ids, `Orgs.mirrored`, `Members.mirrored`) and `auth/peers.py` (the two questions one instance asks the other on a fleet key the other minted for it, `box peer`: whose a production ring is, and production's numbers; `api/peers.py`).
`routes/answering.py`: every number an org answers at, both worlds, which the outbound trunk and
the country fence read.

## 10. Evals: four rings, one score

| ring | what | runs where | in the tree |
|---|---|---|---|
| 1 | goldens over text: a starting state, what the caller says, what must come of it | the gateway, over the app that holds the agent (`api/evals/`) | `evals/goldens.py`, `headless.py`, `matrix.py`, `report.py`, `runs.py` |
| 2 | the same, spoken: one simulated caller on a real line, in the persona's own `tts` and `voice` or else a Cartesia voice that is not the agent's, a native speaker of the agent's declared language, waiting for the greeting before its first line and for each answer to land before the next — the gate opens on the caller's OWN newest transcript having landed after it fell silent (`listening.py`), never on any `agent.state` in the snapshot, because the `listening` that ends the PREVIOUS turn is already there and would open it over the answer; an interferer and packet loss if asked | the room is held by the gateway, the call by the worker | `evals/calling.py`, `dispatching.py`, `caller.py`, `speech.py`, `line.py`, `api/evals/voice.py`, `listening.py` |
| 3 | a finished call read back whole and checked **by code, with no model**: consent, provider errors, latency budget, the register scan, a replay | `POST /v1/evals/replay/{call}`, `pinecall eval` | `evals/checks/*` |
| 4 | **every finished call judged at hang-up**, the verdict an entry in the tenant's own log | the session's `Scorer`, on either channel | `evals/score.py`, `evals/judges/*` |

The judges are livekit's shape. The ring-4 panel is `ConsentJudge` (by code, off the gate lines),
`GroundedJudge` (every price, hour, date and name the agent stated, against the evidence),
`promises`, and `persona` on a simulated call whose caller wrote a rule (`evals/judges/persona.py`).
`RegisterJudge` (tú or usted, by code) runs in ring 1 when a golden declares `register`; it is not
on the ring-4 panel because `AgentConfig` declares no register yet. A judge that wants a model gets
one Haiku behind a ceiling (`PINECALL_JUDGE_CEILING_EUR`; zero means no judge asks), and
`call.score` records who was RUN and who ANSWERED. Decisions: *evals* and its chapters, *scoring*.

## 11. Who may import whom

The whole table, enforced by `tests/test_isolation.py`:

```
types      ← nothing                          (no IO, no framework)
extensions ← types                            the points a package beside us plugs policy into
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
mail       ← types, orgs                      the letters, and the SMTP server they are handed to
fleet      ← types                            every worker's heartbeat, and the loop over the clouds
api        ← all of the above                 never worker/
worker     ← all but extensions, whatsapp, memory, knowledge, lookups, mail   never api/ — over HTTP
cli        ← the verbs over any of them
```

The core never imports a tenant; a tenant never imports LiveKit. A package earns its directory by having a line in that table.

## 12. The box, and the line

One machine (`PINECALL_ROLE=all`), or a **hub** — gateway, SFU, SIP, Redis, Postgres, Caddy — and
**workers** dialling it by URL; on either, **instances** (production, a sandbox), each one env file,
one credstore and templated units: its own gateway, worker, database and keys, and a peer key of the other. Declared: cloud-init,
units, Quadlets, nftables, systemd credentials, a manifest Makefile. `infra/box/README.md`; *box*.
Everything the product does is here, open: orgs, keys, quotas, usage, routes, the log, the vault,
the operator API, the sign-up as a mechanism, the box. **Nothing that charges is**: no plan, no
price, no trial, no card. The two meet at `extensions/` — named points the runtime answers itself
until a package installed beside it registers another (`points.py`, `loading.py`,
`PINECALL_EXTENSIONS`); today one point, what a new org may do, and it speaks `Quotas`, never a
plan. It is how `sentry` and `getsentry` are cut: the open package holds every mechanism, the
private one plugs policy in, and the door never learns who answered. `pinecall/cloud`, private, is
that package for our box — the trial, the plans, Stripe fed from the meter — and never ships to a
customer, whose box runs the same code with the runtime's own answers. What spans many boxes — a
fleet dashboard, an admin over every tenant — is a service apart, over the operator API.
