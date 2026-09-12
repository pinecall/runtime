# Changelog

All notable changes to `pinecall`, the runtime. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers and tags are the
maintainer's call, so everything sits under Unreleased until one is cut.

## [Unreleased]

### Added
- **`seats`, the seventh quota.** How many people an org may hold: invited and active together,
  because an invitation sent is a seat taken, and a `disabled` member keeps their row and holds
  none — which is what frees one. `POST /v1/members` counts before it writes and answers the
  quota's own `429`, writing no `credits.exhausted` entry for the reason a push writes none: an
  invitation names no agent, and an agent's log is the only log an org has. A seat is charged only
  where a row will be made, so re-sending a link to somebody still invited is never the thing a
  full org cannot do. `0019`; `--seats` on `orgs quota` came free of `QUOTAS`.
- **An org issues its own keys.** `GET /v1/keys`, `POST /v1/keys` and
  `POST /v1/keys/{fingerprint}/revoke` on the org's own key (`keys`): the key a tenant's server
  runs on no longer has to come from the box's operator. A POST mints a key for a MACHINE —
  `app` and production when nothing is said, naming nobody, because people get keys by logging in
  — and answers it in the clear the once; the listing is fingerprints and never a key; a key may
  not issue a scope it does not itself open, and another org's fingerprint is the 404 a stranger's
  is.
- **`providers`, a scope of its own.** The vendor keys an org brought are `providers` from here;
  `keys` is the org's own API keys. `0017` hands `providers` to every row that held the old `keys`,
  so nothing a live key could do yesterday is refused today. `manager` and `admin` preset both.
- **`extensions/`, and no plan in the runtime.** The points a package installed beside the runtime
  plugs its policy into, named by `PINECALL_EXTENSIONS` and imported at startup — a name that does
  not import stops the start rather than admitting orgs without limits. One point today:
  `admitted(org, email) -> Quotas`, asked at the sign-up and written in the same breath the org is
  made. The runtime's own answer is no limit and no row. The free-trial numbers that lived in
  `api/signup.py` are gone: a trial is a plan, and a plan is the charging package's to spell. The
  cut is sentry's and getsentry's; ARCHITECTURE §12 says how.
- **`PINECALL_SIGNUP`.** Whether a stranger may make an org at this gateway, and it is **off
  unless set**: a box somebody runs for their own agents is never asked to close a door. Its own
  flag and not `cloud`, because those are two facts — a box of its own may want sign-ups, and a
  cloud may close them. `GET /.well-known/pinecall` now answers `{version, cloud, signup}`, which
  is what the console reads to decide whether to draw a way in. The gateway sends no CORS header
  at all: the console it serves is the same origin, and a site elsewhere links rather than posts.
- **A sign-up door.** `POST /v1/signup {org, name?, email, person, password}` — only where
  `PINECALL_SIGNUP` is on — makes the org allowed whatever `extensions.admitted` answers, its
  first `admin` active with that password, and answers their first key with a one-use login code
  for the console. Throttled five a minute per client; a gateway that takes none refuses `403`
  naming the setting.
- **Numbers the box buys for a tenant.** `POST /v1/numbers/buy {country, area_code?, agent}` finds
  one local voice number on the box's own Twilio (`TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY`,
  `TWILIO_API_SECRET`, the names `twilio_trunk.py` already reads; the gateway unit imports them),
  buys it, attaches it to the box's trunk `pinecall` and wires it as an import does; `?dry_run=true`
  names the number and pays for nothing. The route carries `managed: true`, and a new quota
  **`numbers`** (`orgs quota --numbers`, migration `0016`) caps how many the box buys for an org —
  imported numbers count against nothing. `credits.exhausted` may now name `numbers`.
- **A tenant's own numbers.** `PUT /v1/carrier` brings the org's Twilio account (verified once) or a
  SIP peer, sealed under the vault key; `GET /v1/numbers/available` lists what the account owns;
  `POST /v1/numbers {number, agent}` imports one — the carrier's trunk `pinecall-<org>` pointed at
  `sip:<PINECALL_DOMAIN>:5060`, the number attached, the org's LiveKit inbound trunk admitting it
  from the carrier's networks, the route — each write looked up before it is made, and
  `?dry_run=true` answers the plan alone. `DELETE /v1/numbers/{number}` lets one go. Migration
  `0015`; `PINECALL_DOMAIN` is now read by the gateway. `docs/protocol/numbers.md`.
- **The org's floor.** `GET /v1/sessions?limit=` lists the org's newest calls across every agent,
  the same rows as an agent's own list, each now naming its `agent`; `GET /v1/events` streams the
  floor changing — an agent registered or detached, a call ringing, dialing, started, ended — as
  SSE, live only, each frame the entry of its own log. Both on a key with `calls`.
- **The org's tables on the tenant's key.** `GET /v1/usage` (the org's metered rows and totals,
  scope `usage`), `GET /v1/numbers` (its doors in the key's world with their source, scope
  `numbers`), and `POST /v1/login/env {env}` — a person's key mints the same person's key in the
  other world, which is how the console's Production/Development toggle works.
- **`agent.detached`.** A socket that held an agent and went is written to the agent's own log:
  which socket, which world, and whether anybody still holds the agent there.
- **The gateway serves the console.** `GET /` and every path that is not a door's answer the
  built page from `src/pinecall/gateway/console/` — package data `scripts/console` builds from
  the agents repo and copies in, git-ignored, carried by the wheel and by `make deploy`, which
  now runs it first. `/v1/*` and `/.well-known/*` paths nobody declared are still JSON 404s, and
  a gateway nobody built the console into says so in a sentence. `GET /.well-known/pinecall`
  answers `{version, cloud}`; `PINECALL_CLOUD` says whether this is Pinecall's hosted gateway.
- **The console's directory verbs go over the app socket.** `POST /v1/agents/{slug}/dev/{family}/{verb}`
  relays a console's ask — a written call to the class, the personas and a simulation, the goldens
  and a suite, the knowledge folder, the memory goldens, a promoted candidate, drift, a
  reproduction — as an ephemeral `dev.request` down the socket of the `pinecall run` holding the
  agent, and answers with the `dev.answer`'s result or refusal verbatim. What `pinecall ui` served
  from its own process under `/ui/*` now comes through the gateway. `docs/protocol/dev-verbs.md`.
- **The doors enforce scopes, and the seat is named.** Every tenant door asks the key for exactly
  one scope — `app`, `calls`, `talk`, `supervise`, `pipeline`, `knowledge`, `memory`, `evals`,
  `keys`, `team` — and refuses with `403 this key does not open X: it opens …`; both sockets
  close with the same sentence. A test walks the app and fails on a door that declares none or
  two. A supervisor's seat minted from a person's key carries the member's id and name, so
  `supervisor.*` entries say who; `GET /v1/whoami` and the seat's answer carry `subject` and
  `name`.
- **Members and login.** An org's people are rows: `POST /v1/members` invites one with a one-use
  token that dies in a week, `POST /v1/invitations/{token}` accepts it with a password (argon2id at
  rest) and answers the person's first key, `POST /v1/login` mints a key for a person and a device
  from org, email and password — one `401` sentence for every wrong thing, five tries a minute per
  name — and `POST /v1/login/codes` mints a one-use code a key holder hands a browser so no key
  ever rides a URL. Roles are presets of key scopes: `qa` · `supervisor` · `manager` · `admin` ·
  `developer`. Disabling a member revokes their keys. Migration `0014`; `argon2-cffi` joins the
  dependencies.
- **The key knows where and who.** An API key is issued into one of two worlds — `production` or
  `development` (`keys issue --env`) — and the gateway namespaces its registry and its routes by
  it: the same slug is held once in each, `GET /v1/agents` and `GET /v1/routes` answer the key's
  world, a call opened on the other world's route is `403`, and a development key claiming a number
  production holds is refused with the world named. `agent.registered` and `call.started` carry
  `env`. The key also carries `scopes` (the doors as they are grouped; `--scope`, repeatable, every
  scope when left out), and `subject` and `name` for a person's key; `GET /v1/whoami` answers all
  of them. `routes add --env` types a number into a world. Migration `0013` leaves every existing
  key production's with every scope. The dev key opens development. The worker's tools door now
  takes the org's key like every other worker door.
- **A call's first entry names the run that opened it.** `call.ringing`, `call.dialing` and
  `call.started` carry `run`: the eval run's id, or null for a person. It replaces a caller id
  prefix (`eval_…`) that three processes read as a marker — the worker, to greet nobody on a call
  that opens mid-conversation; the tenant's CLI, to seed the golden's state; the gateway, to mint
  it in two places. The fact is now on the dispatch (`run`) and on the wire, and a golden's caller
  is the visitor id every caller with none gets.
- **The worker reads `~/.pinecall/dev`.** A gateway on a dev key leaves its door there and the
  tenant's CLI already read it; the worker now does too, from `pinecall.auth.dev_file`, and knocks
  with that key when its gateway is that door — saying out loud that an exported
  `PINECALL_API_KEY` is being ignored. Which key a worker sends no longer depends on whether the
  url happens to be loopback.
- **What a broken golden's model was asked rides the cell.** `Spoken` and `Run` carry `asked`;
  the row's JSON writes it only under a cell that broke, and writes `null` when the run kept no
  requests — a spoken run builds them in the worker — where an empty list used to stand for both.
- **A declared greeting is spoken.** `AgentConfig.greeting` had been on the wire since ms-2 and no
  session ever read it: a string stored, overridden at the pipeline door, drawn in the console, and
  never said out loud. It is now a `Greeting` — exactly one of `say` (the words, read out as
  written, no model in the loop) or `reply` (what the model is told before it finds its own, which
  the caller never hears) — and both doors run it the moment the session can speak. They are
  `agent.say` and `agent.reply` declared instead of called; nothing new was invented on the wire.
  A class that declares nothing waits for the caller, as before.
  `session/greeting.py` holds the choice once and each door hands it its own pair of verbs.
- **The write side of memory is held to goldens of its own**, which is the half that persists: the
  hang-up makes ONE model call and it can miss what mattered, invent a fact, leave two versions of
  one fact standing, or write a category the tenant listed as never-keep.
  `POST /v1/agents/{slug}/memory/extraction` takes cases — a call already written down, both
  speakers, plus what memory already holds — runs that very extraction per case on the org's own
  model and keys, and asks four questions of what came back, all by code and none by comparing
  sentences: every category named got a fact (the words are the class's own `memory.remember`, so a
  golden that names one it never declared is refused), none went under a `forget` category, no
  fact's TEXT carries a value the call showed must not survive (`never_says`, matched on the folded
  words and on the digits alone, so a card number is caught however it was grouped), and exactly
  the held facts the call contradicted were superseded — the mirror included, which is what catches
  a model that replaces whatever it touches. A case may also PLANT sentences: planting one is the
  assertion that admission refuses it. The answer carries what memory would have kept beside what
  admission dropped. `memory/goldens.py`, `api/extraction.py`; `memory/extraction.py` now offers
  `answered()` beside `extracted()` so both halves of the step can be judged.
- **Memory can be held to a golden**, the way a base already can, and it is the only thing that
  says `recall` returned the wrong facts: a ring watches a conversation and only ever sees the
  facts memory handed over, never the better one it missed. `POST /v1/contacts/memory/eval` takes
  questions that bring their own facts — `{holds, asks, expects}` — writes each question's facts to
  a scratch contact of the org, recalls, deletes them, and answers `recall_at_k` and `ndcg_at_10`
  by code with no model, plus every question it did not answer whole. Writing them is what makes
  the figures the real ranking: the same two index scans, the same fusion, the same embedder a call
  uses. A fact answers when what came back CONTAINS what was expected, folded for case, accents and
  whitespace, because a fact is a sentence a model wrote and a golden names the substance.
  `memory/scoring.py`; the arithmetic behind both figures is now one home, `types/goldens.py`,
  shared with the base's golden and generalised once so a question may expect several facts.
  `Memory.hold` is the write with no model in it. `docs/retrieval/spec.md` has the contract.
- `Golden.memory`: a ring-1 golden may open its call already knowing things about the caller.
  `evals/remembering.py` answers those facts to the `recall` tool for that call and nothing else
  moves — the tool call, the result and the request are the real ones, the memory table is neither
  read nor written, and `remember` at hang-up is still the gateway's so a run writes no fact about
  a caller nobody called as.
- The runtime, from zero: one distribution, two processes (gateway, worker) on livekit-agents 1.8;
  the log with a seq born under the database; orgs, hashed API keys with scopes, quotas, usage,
  routes, the provider-key vault, the token door, WhatsApp's webhook, the operator API.
- Sessions on both channels — voice in the worker, text in the gateway — writing the same log
  under the same names, with every metric livekit measures.
- Evals: the four rings on `livekit.agents.evals`; every finished call judged at hang-up
  (`call.score`), consent and grounded on the panel; ring-3 checks by code; ring-1/2 goldens.
- The CLI: `gateway`, `worker`, `sessions`, `chat`, `orgs`, `routes`, `keys`, `migrate`, `doctor`, `box`.
- The declared box: cloud-init on any provider, systemd units, Quadlet containers, nftables,
  every secret an encrypted systemd credential; roles `all` / `hub` / `worker`; worker slots
  (`PINECALL_MAX_JOBS`).
- The deploy as a Makefile: `make deploy` (rsync, `make -C infra/box install`, `uv sync`, restart
  by role, health, doctor), `make secret`, `make worker-secrets`, `make doctor`, `make status`, `make logs`.
- The doctor knocks at every vendor with the key the box holds and fails a deploy on a dead one,
  naming the variable and never the value; it knows the box's role (`PINECALL_ROLE`).
- `ARCHITECTURE.md`, `docs/protocol/` (operator API, the token door, the projections).
- The seam memory and retrieval land on: **two declared tools the platform runs**, `recall` and
  `search`. The class's declaration brings each one — `memory` brings `recall`, `docs` brings
  `search` — they stand in the request's `tools` array beside the app's own, and their answers
  reach the model as `tool_result` blocks, JSON-encoded. With `docs.mode = "retrieved"` (the
  default) and whenever `memory` is declared, the session runs the lookup itself and puts a real
  `tool_use` / `tool_result` pair into the request — on a spoken call started while the caller is
  still talking, under `PINECALL_VOICE_LOOKUP_BUDGET_MS` (250), and on a written one at turn end
  under `PINECALL_TEXT_LOOKUP_BUDGET_MS` (3000); with `docs.mode = "tool"` the model calls
  `search` itself. At hang-up the call
  is remembered under `PINECALL_REMEMBER_BUDGET_S` (8.0). `AgentConfig` declares `knowledge`
  (`{path, text}`, the file's own words in a static block), `docs` and `memory`; the worker asks
  `POST /v1/calls/{call}/lookup` and `/remember`; TEI is the embedder (`providers/embed/tei.py`,
  refused by name when it is not 1024 wide). A lookup that did not run is an `error` entry
  (`recall_skipped`, `search_skipped`, `remember_failed`), recoverable, and the call goes on.
- **A spoken call's lookups start while the caller is still talking.** `recall` and `search` ran
  when the turn ended, inside the caller's silence, and on a live two-turn call every one of them
  was skipped: the same door that answers in 111 ms with nothing else happening took up to 1085 ms
  against the reply the session was already generating, and 250 ms of a telephone line is all a turn
  can spend. Now the first interim transcript carrying four words starts the run
  (`session/voice/events.py` → `TurnLookups.heard_so_far`), one per turn, asked with the caller's
  words so far; the end of the turn collects it — nothing to wait for when it is back, its tail
  under budget when it is not, the whole run when the turn was too short to have started one. The
  budget is now per tool and per channel: `PINECALL_LOOKUP_BUDGET_MS` is gone, replaced by
  `PINECALL_VOICE_LOOKUP_BUDGET_MS` (250, a tail on a line somebody is listening to) and
  `PINECALL_TEXT_LOOKUP_BUDGET_MS` (3000, a whole lookup nobody hears), and a `recall` that answered
  is used beside a `search` that did not. Measured at 250 ms over six two-turn calls each way, the
  caller waited 125–251 ms per turn before and 0 ms on five turns of six after.
  `docs/decisions/retrieval.md`.
- Memory itself: `memory/` and `0008_memory.sql`. `PgvectorMemory` keeps a contact's facts in
  `contact_memories`, bi-temporally — an update is a new row that supersedes the old one, an
  invalidation an end date, nothing is deleted but by `forget`, the right to be forgotten.
  `recall` runs cosine over `halfvec(1024)` and BM25 through pg_textsearch (`spanish`), fuses the
  two by rank (RRF, k=60), weighs recency (half-life 90 days) and confidence, and answers the best
  k at 0..1 with no model; `remember` is one call to the org's own model at hang-up, answering
  add / update / invalidate ops, parsed strictly and policed by the tenant's `MemoryPolicy`
  (a `forget` category never reaches the table, and a fact that names one of the class's own tools
  is refused before it — admission at write time, checked against `AgentConfig.tools`).
- The knowledge base (`knowledge/`, migration `0009_knowledge`): a tenant's Markdown files
  chunked by heading under ~350 tokens, each chunk under its heading path, embedded in batches
  and kept in `knowledge_chunks` with an HNSW index by cosine and a BM25 index in spanish; a push
  replaces the base whole in one statement; a search fuses both indexes by reciprocal rank
  (k=60, thirty candidates a branch) and hands the model `{path, heading, text}` per chunk.
  The row in `knowledge_bases` says which model wrote the vectors, so an `Embedder` now names
  its model (`model()`, what TEI's `/info` reports).
- The lookup itself (`lookups/`): one `Lookups` per gateway is the session's `Lookup` and
  `Rememberer` for every text call in-process and, over `POST /v1/calls/{call}/lookup` and
  `/remember` (worker-only, 404 in the events door's words for another org's call), for every
  spoken one. `recall` reads the contact — `CallContext.remembered_as`: the resolved id, else the
  number on phone and WhatsApp, else nobody and nothing is written, and never what the model wrote
  in the tool's input — `search` searches the agent's `docs.base` under its declared `k`/
  `min_score`, and the gateway writes `memory.ops` and `docs.sources` on the call's log with the
  turn's `speech_id`; at hang-up `remember` reads the call's turns off its log and runs on the
  org's own model and keys. The embedder is reached lazily and, down, is named in the error entry
  (`TEI at … did not answer`). Ring 1's sessions are built with the same `Lookups`, so a golden
  carries its sources, and the grounded judge reads them.
- The knowledge base's doors, on the tenant's key: `PUT /v1/knowledge/{base}` (the base replaced
  whole), `GET /v1/knowledge`, `DELETE /v1/knowledge/{base}` (404 for a name never pushed); and a
  contact's, `GET /v1/contacts/{contact}/memory` (the history, current first) and `DELETE`
  (forget, the right to be forgotten). On a dev key each answers 503 with its sentence
  (`this gateway keeps no knowledge: it runs on a dev key`, `… no memory …`).
- The licence is spelled out where an operator meets it: the Apache-2.0 copyright line is
  filled (`Pinecall`), `README.md` has a License section, and `license-files` puts the text
  itself in the wheel and the sdist, so an install carries its licence.

### Changed
- **A contact's facts and a knowledge base are one world's.** `contact_memories`,
  `knowledge_bases` and `knowledge_chunks` carry `env` (`0018`, everything already written is
  production's), and every read and write says which: a test call on a laptop no longer writes
  facts into the memory a production call reads under the same number, and a `knowledge push`
  with a development key replaces the development base and never the one the telephone answers
  from — promoting is the same push made with the key the box runs on. The two `kept` counts the
  quotas read take both worlds, because a row a laptop wrote is a row on the same disk; the push
  door does the "less the base being replaced" arithmetic itself, and `Knowledge.kept` lost its
  `besides` for it.
- **An agent is held per person in development.** The registry's name for a holding is
  `(env, holder, slug)`: nobody's corner in production, where what is deployed is the org's, and
  the member the key was minted for in development. Two developers of one tenant now each run the
  same agent on their own laptop and neither takes the other's — before this the second
  `pinecall run` replaced the first, and every `pinecall chat`, every suite and every config read
  followed whoever had started last. A development key that names nobody (CI's) holds the org's
  own, which is what a developer holding none falls back to. A **dialled** door is namespaced by
  neither: a number exists once in a world, so the shared development number is answered by the
  newest run, and `GET /v1/agents` lists what the reader can actually reach — never another
  developer's socket.
- **A person's key does not hold `app` in production.** Holding an agent is a deployment, and a
  deployment is a process on a box, not a laptop that happens to be logged in — so two developers
  can no longer take production's agent from each other by running it. Every key minted for a
  person carries their role's preset in development and that preset less `app` in production: at
  login, at an accepted invitation, at sign-up, and at `POST /v1/login/env`, which now reads the
  member's role rather than the scopes of the key that asked, and refuses a key whose member is
  gone or disabled. What holds a deployed slug is a key issued for a machine.
- **One rule for "a call a run opened has no opening".** Both sessions ask `the_greeting_for`
  with the call's `run`; the eval runner no longer rewrites the class's config with `greeting=None`.
  The three first entries of a call (`call.ringing`, `call.dialing`, `call.started`) are built in
  one module, `session/first_entries.py`, instead of three copies.
- **The evals surface test and nine evals test files are under the `unit` mark**, so `pytest -m
  unit` runs them: 120 tests the gate had been skipping, one of them red (`__all__` had grown by a
  judge the pinned list did not have). `NoVoice` left the public surface; nobody imported it.
- **The simulated caller is on the line before anybody picks up.** Its track is published at
  connect, at 48 kHz, and only then is the agent waited for: a track opened and pushed into in one
  breath handed the agent a line already playing, and the 1.7 s it took to subscribe were the whole
  first sentence (`identifica-al-paciente`, one spoken run in three). `evals/speech.py` returns
  every line at that one rate — espeak-ng's own rate is resampled by livekit's `AudioResampler`.
- **The spoken run decodes `agent.state` through the protocol** (`AgentStateChanged`, typed
  `AgentState`), instead of reading a raw dict key against a bare string.
- **A base can be held to a golden**, which is the only thing that says the index missed a BETTER
  passage — the judge that runs on every call can only weigh what the model was given.
  `POST /v1/knowledge/{base}/eval` takes the questions and the chunk each should have found, and
  answers `recall_at_k` and `ndcg_at_10` computed by code with no model in the loop, plus every
  miss with what came back instead. A base listing now names the embedder that wrote its vectors,
  so a tenant learns of a mismatch from the list and not from a 409 at the next turn.
  `docs/retrieval/spec.md` is the whole contract: the four numbers already in the log, what each
  targets, and the mapping onto OpenTelemetry GenAI's retrieval, memory and embeddings spans.
- **What a lookup found is a tool result, not a piece of the prompt.** The view's markers
  (`<!-- memory: … -->`, `<!-- retrieved: … -->`, `<!-- knowledge: … -->`) are gone, and with them
  `types/markers.py`, `TurnFills`, the `Filler` protocol and `POST /v1/calls/{call}/fill`. They
  spliced a model-written fact and a chunk of somebody's document INTO the tenant's own view, and
  the view travels wrapped in `<instructions>`: one blob, three authorities, presented as an
  instruction. Both vendors say not to. Now `recall` and `search` are declared tools whose
  descriptions say what the content is and where it came from, their answers are JSON objects
  inside `tool_result` blocks, and the dynamic region of the prompt is the view and nothing else.
  `docs/security/prompt-injection.md` is the contract and is public. `filling/` is `lookups/`,
  `PINECALL_FILL_BUDGET_MS` is `PINECALL_VOICE_LOOKUP_BUDGET_MS` / `PINECALL_TEXT_LOOKUP_BUDGET_MS`,
  and `memory_skipped` / `retrieval_skipped` are `recall_skipped` / `search_skipped`.
- **A tenant brings its own provider keys, with its own API key and no operator.**
  `PUT /v1/provider-keys/{vendor}` · `GET /v1/provider-keys` · `DELETE /v1/provider-keys/{vendor}`
  take no org — the key IS the org — and the listing is vendor names and never a value. The vault,
  the mechanism and the one door that answers with a key are unchanged; what is new is that the
  operator is no longer the only writer. `/v1/ops/orgs/{org}/provider-keys` stays for the cloud and
  for the operator of a box. `pinecall keys add|rm|list` in both languages reads the key from
  stdin, never from argv.
- **A box can run its own embedder.** `pinecall-tei` (bge-m3) is a Quadlet unit installed only
  where `EMBED_PROVIDER=tei` in `box.env` asks for it; a hub that embeds at Perplexity or
  OpenRouter takes its key as an encrypted systemd credential instead, and a worker embeds nothing
  because the gateway is what looks up. On a hub an embedder that is down is the doctor's verdict now,
  not its advice, and the line names what to fix. A hub that becomes a worker STOPS the media
  plane it may not disable: `systemctl disable` refuses a generated unit before it would have
  stopped anything, so the containers were outliving the role that owned them.
- **Two quotas more, of the same kind, so a plan can switch memory and the knowledge base off:**
  `memory_facts` and `knowledge_chunks` on `quotas` — NULL is no limit (what a self-hosted box
  has), `0` refuses everything, N is a cap. `orgs quota` gains `--memory-facts` and
  `--knowledge-chunks`; `PUT /v1/ops/orgs/{org}/quotas` gains both fields and
  `GET /v1/ops/orgs/{org}` answers `holding` beside them. A knowledge push past the cap is refused
  429 before a row is written; a hang-up past it writes no fact and asks no model; a lookup at `0`
  answers the empty object of its own shape (`{"facts": []}`), embeds nothing and writes no entry;
  reading and forgetting a contact's memory are refused by no quota.
- **The embedder is configurable and multi-model, and the knowledge base is embedded
  CONTEXTUALLY.** `Embedder` gains `embed_documents(documents)` — one vector per chunk, one list
  per document, the order given being the contract — and `PgKnowledge.put` groups the pieces by
  FILE, so a chunk is embedded while the model sees its neighbours instead of alone.
  `providers/embed/perplexity.py` is one client for both of Perplexity's models: a name carrying
  `-context-` goes to `POST /contextualizedembeddings` (a document at a time, in windows of
  24 000 estimated tokens against the endpoint's 32 768, which it counts over the whole window),
  anything else to `POST /embeddings`. The encoding is named in every request and belongs to the
  vendor, measured against both: Perplexity takes `base64_int8` and refuses `float`, OpenRouter's
  mirror of the same model answers floats. All three replies are unnormalised, so every vector is
  stored at unit length. OpenRouter is the same class with another base URL, key, model and
  encoding — it serves no contextual door.
  `EMBED_PROVIDER` (`tei` · `perplexity` · `openrouter`, default `tei`), `EMBED_MODEL`,
  `EMBED_BASE_URL` and `PERPLEXITY_API_KEY` / `OPENROUTER_API_KEY`; `embedder_for(settings, http)`
  is the one place a provider name is switched on, and the doctor's `embedder` line says which
  provider and model this box embeds with. TEI's CPU image has no arm64 build, so on an Apple
  Silicon laptop this is the only way to retrieve at all. `docs/decisions/retrieval.md`.
- A vector is only comparable to vectors of the same model, and both tables now say so out loud:
  `knowledge.search` refuses a base another model pushed (`base clinica-norte was pushed with
  pplx-embed-context-v1-0.6b; this gateway embeds with BAAI/bge-m3: push it again`), and
  `0010_memory_model.sql` puts `model` on `contact_memories`, which the DENSE branch of a recall
  filters on — BM25 is untouched, so an older fact is still recalled by its words.
- The prompt is named blocks in two regions: `AgentConfig.prompt` declares the layout (default
  `identity · knowledge · tools`, the history, `view`), `prompt.set {name, text}` writes one block,
  `prompt.changed` and `State.prompt` are keyed by name, and `AgentConfig.instructions` is gone —
  the identity block is written like every other. For Anthropic each static block is its own
  `system` string, so a rewritten `tools` block leaves `identity` and `knowledge` cached.
- `tools.set` no longer re-declares the model's tools (a changed tool definition empties the
  provider's whole cache): the agent keeps every declared tool for the call, the visible subset is
  enforced in the runtime's own callable, and a call to a closed tool comes back to the model as
  `<name> is not available now` with an `error refused` entry in the log, never reaching the app.
- `Rememberer.remember(call)` answers how many memory ops were written; the worker's client reads
  it off `POST /v1/calls/{call}/remember`.
- Reciprocal rank fusion, its two constants and the halfvec text literal have one home each
  (`types/fusion.py`, `providers/embedder.py:as_halfvec`); memory and the knowledge base both
  import them, and a tie in a fused order is settled by id on both.

### Fixed
- **Four things the two-worlds cut got wrong, caught on review.** The agent quota counted only
  the slugs one developer could reach, so two developers each holding a different agent slipped
  past a plan of one: it counts the org's slugs across every world and corner now (`slugs`, which
  had been written and never wired). `POST /v1/tokens` and `GET /v1/routes` read the org's corner
  only, so a developer was refused a token for the agent their own `pinecall run` held: both read
  the key's corner. And `GET /v1/agents/{slug}/config` asked for `app` alone, which a person no
  longer holds in production, so the console's state panel fell back to the default in silence:
  the door opens to `app` or `calls`, the one door that does, named by path in the test that pins
  every other door to exactly one.
- **`simulate --voice` no longer talks over the agent.** The persona slept six fixed seconds
  between its lines while the golden runner waited for `agent.state: listening`; a turn that runs a
  tool takes thirteen, and the recordings had the caller speaking over the answer. The one wait is
  `api/evals/listening.py`, required of every spoken caller — the fixed silence is gone.
- `PUT /v1/knowledge/{base}` answered a bare `500 Internal Server Error` when the embedder was
  down — the whole reason, vendor and URL included, went to the gateway's log and nothing at all
  to the tenant. `api/_refusals.py` maps `EmbedderUnreachable` to **503** and `WrongWidth` /
  `WrongModel` to **409** at every door, each carrying the exception's own sentence, in one table
  rather than a catch per endpoint. The lookup door is deliberately not among them: a lookup that
  could not run is still `search_skipped` on the call's log and the turn still goes on.

### Removed
- `PINECALL_TEXT_SEARCH_CONFIG`: nothing read it. The language BM25 stems in is the index's own,
  fixed in `0008_memory` and `0009_knowledge` (`spanish`).
- `doctor --bench`: it printed that no embedder was wired. The embedder is wired; the `embedder`
  line of the report names the provider and the model and says what a down one costs (a skipped
  lookup, said in the call's log).
- `LeakageJudge`: it had no user in the tree, and a judge given a declaration nobody wrote would be
  judging a rule nobody wrote. The idea returns with the milestone that declares what another
  tenant owns.
