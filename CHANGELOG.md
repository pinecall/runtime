# Changelog

All notable changes to `pinecall`, the runtime. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers and tags are the
maintainer's call, so everything sits under Unreleased until one is cut.

## [Unreleased]

### Added
- **`docs/from-zero.md`: the walkthrough, run end to end.** A runtime on a machine with nothing on
  it, through to the clinic answering a written call — every command actually run against a
  database made for it, with the real output under each, including the refusals. It found two
  things on the way: `keys revoke` could not take the fingerprint `keys list` printed, and `orgs
  move` left an agent's numbers behind.
- **`make deploy` can rebuild a box that has only its operating system.** Three things it assumed
  cloud-init had done and never did itself, each found by wiping a real box and putting it back:
  `/opt/pinecall/app` is root's to make, so a deploy into a box that lost it died on a bare
  `Permission denied`; `pinecall-secrets` is `WantedBy=multi-user.target`, so the box's own
  keypair, ops key and vault key were made at BOOT and nowhere else — a box that lost them sat
  there with a postgres that could not read `media.env` until somebody rebooted it; and the media
  plane is four Quadlets that come up from their own `[Install]`, so on a box that had not
  rebooted since the units were written, LiveKit was simply down and the doctor said so. The
  deploy now makes the directory, starts the secrets, and starts — never restarts — the media
  plane.
- **`docs/the-runtime-cli.md` documents every group there is.** It had a section for
  `pinecall-runtime chat`, a verb deleted with the dev key it existed to spend, and none at all
  for `init` or `providers`. `migrate --post` was missing too — the escape hatch for a migration
  too slow for the five seconds a unit gives it at startup, which is exactly the flag somebody
  reaches for under pressure. Checked by walking every group's own `--help` against the page.
- **`pinecall-runtime init`: the first org and the first person, in one command.** What replaces
  the magic key on a fresh runtime — it makes the org, invites its first admin, makes them an
  operator of this box (somebody has to be able to make the second org), and prints the link that
  opens the password screen and the two lines to type next. `--org` defaults to `default`, the org
  the schema seeds, so a runtime that has just been migrated needs nothing but a person. Running
  it twice carries on to the person rather than stopping at the org, because it is the verb
  somebody runs twice while reading the README.
- **`PUT /v1/numbers/{number}/env`: a number moves between the worlds.** An org buys ONE number,
  so a team wanting to try a new agent on the real line had nowhere to try it — a second number is
  a second bill, and a third world would be a third of everything. The move is one row
  (`routes.env`), so it answers from the next call on, and the carrier account and both trunks are
  untouched: a call arrives at this box whichever world answers it. The one numbers door that does
  not work in the key's world alone, because crossing the two is what it is for.
- **An admin and the box operator see every sandbox corner.** A sandbox agent is held per person,
  which is what stops two developers taking each other's `pinecall run` — and it also meant nobody
  could see anybody else's: a tenant's admin had no way to tell what their team was running, and
  the operator of the box had none either. `GET /v1/agents` now answers a key that opens `team`
  with one row per CORNER instead of one per slug, and every row carries `holder`, the member
  whose copy it is — absent for the org's own, which is what a machine key holds. Which rows a
  reader gets is the key's own answer (`sees_every_corner`, `auth/keys.py`): whoever may see who
  the team IS may see what the team is RUNNING. `holder` is the same `{holder, name}` the line
  door answers with, because the id alone names nobody a page can show.
- **`orgs move <agent> <org>` takes the agent's NUMBERS with it.** A slug belongs to the org that first registered it for as long as
  its log exists, and nothing could move it — so an agent registered from a terminal pointed at
  the wrong key belonged to that org for good, with every call it went on to take. A box walks
  into it by construction: its own worker and operator keys are issued into `default`, so the
  first agent anybody runs there lands in `default` too. The verb moves the agent's own log and
  one head row per call; it is refused while somebody holds the slug, and 404s one nobody ran.
- **Every vendor LiveKit reaches, not five.** `providers/catalog.py` is one table of the
  forty-five vendors livekit-agents 1.8 ships a plugin for, and `providers/plugin.py` builds any
  of them out of the plugin's own constructor signature — so `stt: cartesia`, `tts: rime`,
  `llm: groq` need no file here and no edit when livekit adds one. The five under
  `providers/llm/`, `stt/` and `tts/` stay: they are the vendors this build has an opinion about.
  `pip install pinecall[runtime,providers]` (and `providers-big` for AWS, Azure, Google and
  Speechmatics) is what makes a row real; a vendor with no plugin is refused by name with the one
  command that installs it, and a vendor that wants more than a key — an endpoint, a client pair —
  is refused in its own words, before the call rather than mid-turn.
- **`livekit` is a vendor.** LiveKit Inference fronts OpenAI, Google, Deepgram, Cartesia,
  AssemblyAI, Inworld, xAI and the rest on the box's own LiveKit project: no vendor key, no extra,
  nothing to install. `llm: livekit` + `openai/gpt-5-mini`, `tts: livekit` + `cartesia/sonic-3`.
- **Aliases.** `11labs`, `claude`, `gemini`, `grok`, `gpt`, `dg`, `mistral` and the rest resolve
  to one canonical name at every door — a declaration, an override, a stored key — so a tenant who
  brought a key under one spelling reads it back under the other.
- **`GET /v1/providers`** and **`pinecall-runtime providers`**: the whole catalogue with what each
  vendor still wants on THIS box — plugin, key, or nothing. Never a key, not even a prefix. The
  same rows ride inside the pipeline report, so the console's two screens cannot disagree.
- **A `tts` knob at the pipeline door.** The speaking stage was the one an operator could not
  move: the voice and its model could be turned, the vendor could not. `tts: cartesia/sonic-3`
  turns it like the other two (migration `0022`).
- **A price for the other forty vendors.** `providers/published_prices.json` — a thousand models
  off [mahimailabs/voice-prices](https://github.com/mahimailabs/voice-prices), dated per row and
  regenerated by `scripts/refresh-prices` — sits behind the hand-read table, which still wins
  where the two disagree. A call spoken by Cartesia used to read `unpriced`, which in a summary is
  a bill nobody can see.
- **The migrations got a guard.** `schema_migrations` keeps a **sha256** and refuses a checkout
  whose applied file changed: the rule against editing one is now a gate, and it is the only thing
  that catches two databases silently disagreeing. Every run takes an **advisory lock** first,
  sets a **5 s statement and 1 s lock timeout** per migration, and **says which database** it is
  talking to; `migrate status` asks the database rather than listing the disk, `migrate plan`
  touches nothing, and a `.post.sql` never runs at startup. `migrations.lock` names the last
  migration that landed — bumping it makes two branches adding `0022` conflict in git, and it is
  the baseline for **squawk**, which gates every unlanded migration in `scripts/lint`. And
  `tests/migrations.py` builds a schema as a box HAD it, with rows, so 0021 is proven not hoped.
- **`GET /v1/whoami` carries the org's `slug`** beside its id: `org` is what doors take, `slug` is
  the word its people read, and `pinecall login` was printing the id at them.
- **A terminal is signed in from a browser.** `pinecall login` holds no key and the person at it
  has none to paste, so the two meet at a word: four doors under `/v1/login/pairings` — the
  terminal mints one and polls, the browser reads and approves. That mints the TERMINAL's own key
  — same person, sandbox, labelled as that machine — and a password is typed into a page and
  never into a shell. `docs/protocol/people.md`.
- **In the sandbox, a contact's facts and a knowledge base are one DEVELOPER's** (`0021`). 0018
  gave both tables the world they were written in; the sandbox was still one pile shared by the
  team, so one developer's `knowledge push` replaced what the other two were testing against and
  one test call's extracted fact arrived in another's. Both now carry whose corner wrote them.
  Knowledge **falls back** — a developer who has pushed nothing reads the org's, because nobody
  joins a team to an empty base — while a push, a drop, and memory never do. The quotas still
  count every corner: the rows are the org's.
- **Where a ring lands in the sandbox, in two steps.** An org shares one sandbox number, so
  three developers on one agent meant the newest `pinecall run` silently took the others' calls,
  answered in a colleague's scrollback with nothing saying so. Now a developer says which number
  they call FROM (`PUT /v1/line/from`) and every call they make lands in their own corner — three
  of them test at once with no coordination — and a number nobody claimed falls back to the
  agent's **line** (`GET/POST/DELETE /v1/agents/{slug}/line`), which the first corner to hold it
  takes and which is handed on when that terminal closes. Neither is a row: both are only
  meaningful next to a socket. Production has one corner. `api/agents/doors.py`.
- **The operator invites an org's first person.** `POST /v1/ops/orgs/{named}/members` and
  `pinecall-runtime orgs invite <org> <email> --name … [--role]`: how a tenant exists at all on a
  gateway that takes no sign-up — the box makes the org and invites its admin, and prints a
  **link** once that opens the console's password card. The operator holds a token and never a
  password, the invitation takes none of the org's seats, and there is no door that changes or
  disables a member from the box: an invitation is inert until the person it names accepts it.
- **The operator's page, served at `/admin`.** `api/console.py` becomes `api/pages.py` with a
  `Page` each, `/admin` declared before the catch-all so it is the box's page and never a screen
  of the tenant's console. `GET /v1/ops/whoami` (`{operator, version, domain}`) is what the page
  proves its key at; `GET /v1/ops/fleet` carries `stale_after_s`, the hub's own threshold. The
  page is the agents repo's `src/cli/ui/admin/`; `scripts/console` builds and copies both.
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
- **`PINECALL_SIGNUP`, and the door it opens.** `POST /v1/signup {org, name?, email, person,
  password}` makes an org allowed whatever `extensions.admitted` answers, its first `admin` active
  with that password, and answers their first key with a one-use login code for the console —
  throttled five a minute per client. Only where the setting is on, and it is **off unless set**: a
  box somebody runs for their own agents is never asked to close a door, and a gateway that takes
  none refuses `403` naming the setting. Its own flag and not `cloud` — a box of its own may want
  sign-ups, and a cloud may close them. `GET /.well-known/pinecall` answers `{version, cloud,
  signup}`, which is what the console reads to decide whether to draw a way in. The gateway sends
  no CORS header at all: the console it serves is the same origin, and a site elsewhere links.
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
  other world, which is how the console's Production/Sandbox toggle works.
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
  `sandbox` (`keys issue --env`) — and the gateway namespaces its registry and its routes by
  it: the same slug is held once in each, `GET /v1/agents` and `GET /v1/routes` answer the key's
  world, a call opened on the other world's route is `403`, and a sandbox key claiming a number
  production holds is refused with the world named. `agent.registered` and `call.started` carry
  `env`. The key also carries `scopes` (the doors as they are grouped; `--scope`, repeatable, every
  scope when left out), and `subject` and `name` for a person's key; `GET /v1/whoami` answers all
  of them. `routes add --env` types a number into a world. Migration `0013` leaves every existing
  key production's with every scope. The dev key opens the sandbox. The worker's tools door now
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
  `POST /v1/agents/{slug}/memory/extraction` takes cases — a call already written down, plus what
  memory already holds — runs that very extraction per case on the org's own model and keys, and
  asks four questions of what came back, all by code: every category named got a fact (the words
  are the class's own `memory.remember`), none went under a `forget` category, no fact's TEXT
  carries a value the call showed must not survive (`never_says`, matched on the folded words and
  on the digits alone), and exactly the held facts the call contradicted were superseded — the
  mirror included, which catches a model that replaces whatever it touches. A case may also PLANT
  sentences: planting one is the assertion that admission refuses it. `memory/goldens.py`,
  `api/extraction.py`.
- **Memory can be held to a golden**, the way a base already can, and it is the only thing that
  says `recall` returned the wrong facts: a ring watches a conversation and only ever sees the
  facts memory handed over, never the better one it missed. `POST /v1/contacts/memory/eval` takes
  questions that bring their own facts — `{holds, asks, expects}` — writes each question's facts to
  a scratch contact of the org, recalls, deletes them, and answers `recall_at_k` and `ndcg_at_10`
  by code with no model, plus every question it did not answer whole. Writing them is what makes
  the figures the real ranking: the same two index scans, the same fusion, the same embedder a
  call uses. A fact answers when what came back CONTAINS what was expected, folded for case and
  accents, because a fact is a sentence a model wrote and a golden names the substance.
  `memory/scoring.py`; the arithmetic behind both figures lives once in `types/goldens.py`.
  `Memory.hold` is the write with no model in it. `docs/retrieval/spec.md` has the contract.
- `Golden.memory`: a ring-1 golden may open its call already knowing things about the caller.
  `evals/remembering.py` answers those facts to the `recall` tool for that call and nothing else
  moves — the tool call, the result and the request are real, the memory table is neither read nor
  written, and `remember` at hang-up is still the gateway's so a run writes no fact about
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
  reach the model as `tool_result` blocks. With `docs.mode = "retrieved"` (the default) and
  whenever `memory` is declared, the session runs the lookup itself and puts a real `tool_use` /
  `tool_result` pair into the request — on a spoken call while the caller is still talking, under
  `PINECALL_VOICE_LOOKUP_BUDGET_MS` (250), and on a written one at turn end under
  `PINECALL_TEXT_LOOKUP_BUDGET_MS` (3000); with `docs.mode = "tool"` the model calls `search`
  itself. At hang-up the call is remembered under `PINECALL_REMEMBER_BUDGET_S` (8.0).
  `AgentConfig` declares `knowledge`, `docs` and `memory`; the worker asks
  `POST /v1/calls/{call}/lookup` and `/remember`. A lookup that did not run is a recoverable
  `error` entry (`recall_skipped`, `search_skipped`, `remember_failed`), and the call goes on.
- **A spoken call's lookups start while the caller is still talking.** `recall` and `search` ran
  when the turn ended, inside the caller's silence, and on a live two-turn call every one of them
  was skipped: the same door that answers in 111 ms idle took up to 1085 ms against the reply the
  session was already generating, and 250 ms of a telephone line is all a turn can spend. Now the
  first interim transcript carrying four words starts the run (`TurnLookups.heard_so_far`), one
  per turn; the end of the turn collects it — nothing to wait for when it is back, its tail under
  budget when it is not. The budget is per tool and per channel now:
  `PINECALL_LOOKUP_BUDGET_MS` is gone, replaced by `PINECALL_VOICE_LOOKUP_BUDGET_MS` (250) and
  `PINECALL_TEXT_LOOKUP_BUDGET_MS` (3000). Measured over six two-turn calls each way, the caller
  waited 125–251 ms per turn before and 0 ms on five turns of six after.
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
  replaces the base whole in one statement, and a search fuses both indexes by reciprocal rank.
  The row in `knowledge_bases` says which model wrote the vectors.
- The lookup itself (`lookups/`): one `Lookups` per gateway is the session's `Lookup` and
  `Rememberer` for every text call in-process and, over `POST /v1/calls/{call}/lookup` and
  `/remember`, for every spoken one. `recall` reads the contact from `CallContext.remembered_as`
  and never from what the model wrote in the tool's input; both are written on the call's log.
- The knowledge base's doors on the tenant's key (`PUT`/`GET`/`DELETE /v1/knowledge[/{base}]`,
  the base replaced whole) and a contact's (`GET`/`DELETE /v1/contacts/{contact}/memory`).
- The licence where an operator meets it: the copyright line filled, a License section in
  `README.md`, and `license-files` putting the text in the wheel and the sdist.

### Changed
- **`PINECALL_DEV_KEY` is gone, and with it the second runtime a laptop was.** One string in the
  gateway's own environment that needed no database and, when set, was the ONLY key the gateway
  honoured: every call org `default`, the `api_keys` table not read, `~/.pinecall/dev` written at
  every start so the CLI beside it needed no login, a LiveKit pair derived from it that signed
  tokens opening no room, and a worker that took that file's key over the one it was issued. It
  bought five minutes at the start and charged them back in every hour after — two sets of keys,
  two orgs, two behaviours, and no way to see which you were on. A worker that died for fifteen
  minutes on `GET /v1/routes: 401` (2026-09-11) was that, and so was a deployed one sending
  `Bearer ""`. A laptop now runs the same Postgres, the same migrations and the same issued keys a
  box does; `pinecall-runtime init` is the way in, and `pinecall-runtime chat` — which existed
  only to spend that key — is gone with it. A gateway with no database verifies nothing, says so
  at startup, and answers every keyed door `503` rather than coming up looking healthy.
- **The world things are written in is `sandbox`, not `development`** (`0023`). The word was doing
  two jobs — naming a world, and naming "mine" — so a team that wanted a shared staging deployment
  had nowhere to put it, and a person reading `env: development` could not tell a laptop from a
  box. The migration rewrites the five tables that carry the column and their CHECKs; `--env
  development` is gone from `keys issue` and `routes list`, and `is_a_deployment(env)` is now the
  one question the three places that used to compare against `production` ask, so the day a second
  shared world exists they already mean the right thing. A shared staging needs no third world: a
  machine key in the sandbox names nobody's corner, and that is exactly what every member sees.
- **A sandbox registration is ephemeral.** `agent.registered` and `agent.detached` were appended to
  the agent's log in both worlds, and an agent's log is one log for all of them — so a laptop
  reconnecting all afternoon buried the deployed agent's history in its own noise. In the sandbox
  the entry is marked ephemeral; production is unchanged.
- **A tenant can deploy.** `POST /v1/keys` measured the ask against the asking key's scopes, and a
  person's key in production does not carry `app` — so an admin asking for the key their own
  server runs on was refused, in both worlds, and only the box operator could mint one. The bound
  is now the person's ROLE. A key naming nobody is bounded by itself, as before.
- **`PINECALL_API_KEY` is `PINECALL_WORKER_KEY`.** One name was three things: the worker's
  credential, a key source in the v2 CLI, and the variable v1's SDK exports — so a laptop with
  v1's export still live registered agents into whatever org that key named, silently. The
  runtime's half of the collision is gone. A box that ran before the rename mints a fresh worker
  key on the next deploy and leaves the old credential behind; infra/box/README.md says what to
  remove.

- **A voice id belongs to whoever was named.** A declaration that names its TTS provider has its
  voice passed through as that vendor's own id: Cartesia writes a uuid, Rime a word, Hume a
  sentence, and this build knows only ElevenLabs' shape. A declaration that names NO vendor is
  judged exactly as before — a curated name or a twenty-character id — because the vendor it will
  speak with is the one whose shape is known.
- **A bare word at a model knob that names a vendor IS the vendor.** `stt: cartesia` moves the
  stage and keeps that vendor's own default model; `claude-sonnet-4-5` alone still means a model
  on the vendor already in use. No alias is also a model name, so the two readings cannot collide.
- **The ElevenLabs model list is ElevenLabs'.** It used to be applied to every TTS model an
  operator typed, which refused `sonic-3` on Cartesia.
- **The doctor asks the catalogue.** A role is down when NO catalogued vendor of that role has a
  key, so a box running on Cartesia and Groq reads green without a row being added anywhere.
- **A contact's facts and a knowledge base are one world's.** `contact_memories`,
  `knowledge_bases` and `knowledge_chunks` carry `env` (`0018`, everything already written is
  production's), and every read and write says which: a test call on a laptop no longer writes
  facts into the memory a production call reads under the same number, and a `knowledge push`
  with a sandbox key replaces the sandbox base and never the telephone's — promoting is
  the same push made with the box's key. The `kept` counts the quotas read take both worlds,
  because a row a laptop wrote is a row on the same disk.
- **An agent is held per person in the sandbox.** The registry's name for a holding is
  `(env, holder, slug)`: nobody's corner in production, where what is deployed is the org's, and
  the member the key was minted for in the sandbox. Two developers of one tenant now each run the
  same agent on their own laptop and neither takes the other's — before this the second
  `pinecall run` replaced the first, and every `pinecall chat`, every suite and every config read
  followed whoever had started last. A sandbox key that names nobody (CI's) holds the org's
  own, which is what a developer holding none falls back to. A **dialled** door is namespaced by
  neither: a number exists once in a world, so the shared sandbox number is answered by the
  newest run, and `GET /v1/agents` lists what the reader can actually reach — never another
  developer's socket.
- **A person's key does not hold `app` in production.** Holding an agent is a deployment, and a
  deployment is a process on a box, not a laptop that happens to be logged in — so two developers
  can no longer take production's agent from each other by running it. Every key minted for a
  person carries their role's preset in the sandbox and that preset less `app` in production: at
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
  per document — and `PgKnowledge.put` groups the pieces by FILE, so a chunk is embedded while the
  model sees its neighbours instead of alone.
  `providers/embed/perplexity.py` is one client for both of Perplexity's models: a name carrying
  `-context-` goes to `POST /contextualizedembeddings` (a document at a time, windowed), anything
  else to `POST /embeddings`. The encoding belongs to the vendor: Perplexity takes `base64_int8`
  and refuses `float`, OpenRouter's mirror answers floats. All three replies are unnormalised, so
  every vector is stored at unit length.
  `EMBED_PROVIDER` (`tei` · `perplexity` · `openrouter`, default `tei`), `EMBED_MODEL`,
  `EMBED_BASE_URL` and the two keys; `embedder_for(settings, http)` is the one place a provider
  name is switched on, and the doctor says which this box embeds with. TEI's CPU image has no
  arm64 build, so on an Apple Silicon laptop this is the only way to retrieve at all.
  `docs/decisions/retrieval.md`.
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
- `PUT /v1/knowledge/{base}` answered a bare `500` when the embedder was down, with the whole
  reason in the gateway's log and nothing at all to the tenant. `api/_refusals.py` maps
  `EmbedderUnreachable` to **503** and `WrongWidth`/`WrongModel` to **409** at every door, each
  carrying the exception's own sentence. The lookup door is deliberately not among them: a lookup
  that could not run is still `search_skipped` on the call's log and the turn goes on.

### Removed
- `PINECALL_TEXT_SEARCH_CONFIG`: nothing read it. The language BM25 stems in is the index's own,
  fixed in `0008_memory` and `0009_knowledge` (`spanish`).
- `doctor --bench`: it printed that no embedder was wired. The embedder is wired; the `embedder`
  line of the report names the provider and the model and says what a down one costs (a skipped
  lookup, said in the call's log).
- `LeakageJudge`: it had no user in the tree, and a judge given a declaration nobody wrote would be
  judging a rule nobody wrote. The idea returns with the milestone that declares what another
  tenant owns.
