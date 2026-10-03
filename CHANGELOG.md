# Changelog

## Unreleased

- **A worker that counts its calls takes every one of them.** LiveKit refused it at 0.7 of its
  slots, as it does a CPU reading, while the gateway counted the last 30 % of seats free: on a
  worker of 8 the seventh call rang in silence. The worker's line is now every slot taken (its
  `load_threshold`), the same line the roster calls it full at and overflow opens on.
- Every machine of the cell is a `pinecall-runtime cell` verb, as the box is `box up`: on the box
  `cell allow-replica`, `allow-gateway`, `allow-worker` (an address or the fleet's range), their
  `forget-…`, `gateway-credentials`, `worker-settings` and `worker-credentials`; on the machine,
  `sudo uvx --from pinecall==<version> pinecall-runtime cell join-worker | image-worker |
  release-worker | join-gateway | release-gateway | join-replica`, which copies the package's
  `infra/` there first. Nobody copies `infra/` by hand or calls a script by its path any more; the
  scripts stay as what the verbs run. `pinecall-runtime --version` says the version to install.
- The curl examples are gone, from the repository and from the docs site.
- **The cloud is Terraform's** (`infra/terraform/`): the state in a versioned bucket, one root
  module per environment, `make tf-plan`/`tf-apply`/`tf-check` (CI runs the last). Production was
  imported whole — the box and its replica, the fleet's subnet, the firewall rules, the static
  address, the two buckets with their IAM users and policies, SES, the box's four names in Route 53
  — and a plan says `No changes.`.
- **A fleet machine on Google Cloud reads its credentials from Secret Manager** as its own
  identity: a worker service account per world, five secrets declared with no value
  (`modules/secrets`), written by the box with `pinecall-runtime cell publish-secrets`.
  `pinecall-runtime cell enroll` replaces the bash enroll: a join token, or Secret Manager, once.
- **Production's fleet is a managed instance group** (`modules/fleet-gcp`): the image built by
  Packer (`make image`, `infra/packer`), no public address (a NAT for the fleet's subnet), healed
  on the worker's health port, grown by its autoscaler on the fleet's calls; it never shrinks
  itself. `fleet loop --grow-at-most 0` is the loop of such a cloud: it tells the cloud the calls
  (`measure`) and lets go of the one too many once drained; `infra/fleet/gcp-mig.py` is its
  script, and `pinecall-fleet-loop@<world>` runs it on the box, configured from the box's
  metadata. The box's VM acts as `pinecall-fleet`, which may touch the fleet's machines alone.
- **The runtime whole on a laptop**: `make local` starts the box's Postgres, Redis and LiveKit in
  docker (`infra/local/`), migrates and writes `.local/env` once; `make local-gateway` and
  `make local-worker` run both from the checkout. No cloud account, no key of a box.
- **A fleet on AWS, the same shape as Google Cloud's**, written and validated, applied by no box:
  `modules/secrets-aws` and `modules/fleet-aws` (an Auto Scaling group grown by target tracking on
  `Pinecall/fleet_calls`, its scale-in off), `infra/fleet/aws-asg.py` for the loop, an
  `amazon-ebs` source in `infra/packer`; `cell enroll` and `cell publish-secrets` read and write
  Secrets Manager on AWS. `infra/fleet/aws`, the loop that made EC2 machines itself, is gone.
- **The voice lab is Terraform's** (`environments/lab`): `infra/lab/measure.py` makes the box, the
  generator and the worker machine with `terraform apply` at the sizes under test, resizes the box
  by the same apply, and destroys all of it with `terraform destroy`; the machines' configuration
  is `infra/lab/configure.sh`, one verb a step.

## 0.1.5 — The fleet's image carries no credential: each machine joins on a key of its own (2026-10-02)

- The fleet's image carries no credential. A machine the loop makes spends a join token at its first
  boot (`POST /v1/fleet/join`, `pinecall-join.service` → `worker.sh enroll`) for a fleet key of its
  own, the LiveKit pair and the store's secret, sealed to that machine; the loop mints the token
  (`POST /v1/ops/fleet/join-tokens`: a key of scope `join`, named for the machine, ten minutes, spent
  once) before each `create` and revokes the machine's keys after each `delete`
  (`DELETE /v1/ops/fleet/{worker}/keys`). `worker.sh image` prepares the machine the image is frozen
  from, from `primary.sh worker-settings` (no secret); `infra/fleet/first-boot` is the user-data the
  three cloud scripts hand over; `fleet loop` requires `--fleet` unless `--dry-run`.
  Measured on Google Cloud against the sandbox fleet: a machine made from such an image was
  `accepting` 99 s after the loop asked for it, and deleted with its key revoked 86 s after the
  cordon. The recipe is `infra/fleet/README.md`.
- The fence lets in a range (`primary.sh allow-worker 10.100.0.0/24`), the subnet the fleet loop
  makes its machines in; `infra/fleet/gcp` takes `PINECALL_FLEET_SUBNET`.

## 0.1.4 — Workers on machines of their own, the recording made by the call itself, a box measured with real audio (2026-10-02)

- Workers on other machines: `primary.sh allow-worker <address>` on the box (the fence, LiveKit's
  API and the gateways' balancer at the box's own address, `PINECALL_HERE`), then the fleet's
  credentials through a pipe into `worker.sh join <box> <wheel> <world> [calls]` on the other
  machine; `worker.sh release` for a new wheel. A worker machine reaches the box's LiveKit and
  gateways and nothing else of it. Measured on 8 vCPU: 24 calls at once at 3.5 cores (~0.15 vCPU a
  call, the recording included), first audio p95 1.3 s, every turn answered; the box spends ~0.1 a
  call on their media. `docs/scaling.md`, "A machine of workers alone".
- A call's recording is made by its own session, not by an egress process per call: livekit's
  recorder for the caller (left) and the agent (right) at 24 kHz, and every other voice of the room
  — a supervisor who took over, the far end of a warm transfer, the hold melody — laid in on the
  right where it sounded when the file closes, a stretch at a time. The worker seals it under the
  call's key and moves it to the bucket before the seal. Egress is no longer asked for anything
  (track egress was built and measured dearer: ~0.1 vCPU and ~170 MB a track). The lay-in costs
  ~1/40 of the call's length on a core; a call of half an hour or more with another voice may run
  past the worker's minute to seal, said on the box page.
- LiveKit's API is published on the box's own address by `install.sh`, behind the fence, so letting
  a worker or gateway machine in restarts nothing (a box from before gets one restart, said).
- The object store's file is `/etc/pinecall/store.env` (was `backup.env`): backups and recordings
  both read it. `box upgrade` (and `install.sh`) moves a box's `backup.env` there, once, and says so.
- The four alerts are evaluated on the box by Prometheus and mailed by Alertmanager
  (`infra/box/alerts.sh`, `alerts.sh test` proves the path).
- The box measured whole with real audio and no vendor (`infra/lab/`: the vendors faked on their own
  wire, SIP callers playing a recording): a 4-vCPU box with everything on it holds 6 calls at once.
  Three fixes it found: the plugins are preloaded in the worker's forkserver (ring to live 0.1 s, was
  3–5 s idle and up to 74 s loaded), SIP's RTP range matches the fence, and each world's seats are
  its own.
- Insights count a window of 1, 7 or 30 UTC days, of the scope or one agent.
- A chat's opening is kept, so the gateway holding the app's socket serves it.
- `replica.sh join` works on a machine made a minute ago.

- The box's workers count their calls (`PINECALL_MAX_JOBS`, one per vCPU each, written by `install.sh`)
  instead of reading the machine's CPU: on CPU, a burst of calls left rooms with no agent, LiveKit never
  offering them again (measured: 5 of 10 at 20 % CPU; counting, 14 of 15). A call costs ~0.24 vCPU.
- The disaster drills, run: a restore to a minute in 79 s with RPO under a minute, and a failover
  to the replica in 17 s with no write lost, a box again in under 3 minutes (`docs/a-box-in-production.md`).

## 0.1.3 — The server side, measured: gateways on many machines, a cluster of media, and the numbers (2026-10-02)

- A model that says nothing for 12 s (or the agent's `llm_timeout_s`) no longer leaves the caller in
  silence: the turn is cut and the caller hears a short sentence asking them to say it again, in the
  agent's language. Unset used to mean no deadline at all.
- LiveKit as a cluster, measured: two nodes on the box's Redis carried 400 voice calls with no packet
  lost, and a worker on one node took rooms placed on the other. How a node is added is on the box page.
- Gateways on machines of their own: `infra/cell/primary.sh allow-gateway` on the box (pg_hba, the
  fence, Postgres, Redis and LiveKit's API published on its address for those machines alone, Caddy
  sending them calls), `gateway.sh join` on the other machine (the credentials through a pipe). Redis
  asks a password of everyone (drawn by `install.sh`, `PINECALL_REDIS_URL` a credential). Measured: a
  gateway machine killed for two minutes under 1 200 calls lost none of them.
- What a container is published on beyond loopback is written into its installed file: podman 4.9
  reads no `.container.d` drop-ins, so the replica's Postgres (`primary.sh allow`) was never
  published on the box's address. Fixed for it and for gateway machines.
- A gateway that dies with a worker's request in flight loses no call: an open is asked again while
  the gateway is away and is the same call by its id (one claim, one opening, one ringing), and a seal
  asked again that finds the call sealed is done. The seal's lease is 15 s renewed while sealing (was 120 s):
  a gateway that dies sealing lets the call go in seconds, and a knock waiting on it takes the seal
  over, instead of the worker giving up and the reaper sealing the call with no usage. The lease
  names its holder (migration 0085): only it renews or gives it back, and a summary is written once
  per log, so two gateways sealing one call price it once. Measured: 1 200 calls held, a gateway killed every
  minute: 0 logs wrong, and the only refusals left were opens and seals in flight at the kill.
- `pinecall-runtime load` counts refusals by status and door (`502 POST /v1/calls`), not by status.
- A log read is built from its rows without validating them again (`log/store.py` `entry_of`): a seal
  reads its whole log three times. Measured from a generator machine at 300 calls held: append p50
  60 → 38 ms, p99 850 → 430 ms, seal p50 330 → 200 ms.
- The append path costs a gateway a third less of its core (100 calls held: 155 % → 100 % on two
  gateways; append p50 161 → 14 ms, p99 1.4 s → 94 ms): a group's rows go to Postgres as one JSON
  document instead of six arrays adapted a value at a time, the entries socket and the relay read and
  write JSON in pydantic's own parser, and only the types the facts fold lock a call's facts row.
- A worker's batches of a call go down one WebSocket for the call's life (`WS /v1/calls/{call}/entries`)
  instead of a request per batch: no routing, headers or key per batch on the gateway. A refusal is a
  frame and the socket stays; a lost socket is opened again and the batch asked again with the same
  `after`; a gateway of before (no such door) gets every batch by request, as before.
- A gateway remembers a key it verified for five seconds (`tenancy/remembered.py`): verifying
  was a round trip per request, 15 % of its core under load. A key revoked at a door is forgotten
  on every gateway at once, said on the signal, and so is a person whose role, agents, production
  access or operator standing changes; one revoked at a shell opens for those seconds.
- A summary or a score written before money in dollars (`eur`, `judge_cost_eur`, the euro's `rate`)
  is read again, its euros as the same number of dollars, as migration 0007 kept the facts: 1113
  of box.pinecall.io's 1154 summaries had become unreadable. `facts rebuild` and doctor's sample
  leave a call whose log holds an entry the wire still refuses as it was folded, instead of
  refolding it to nothing.
- The providers row may name the language a vendor's ears are told (`tuning."stt/<vendor>".options.language_code`),
  and it wins over the call's base code: NVIDIA's streaming ASR takes `es-US`, not `es`.
- How a spoken turn ends is the ears' `tuning` too: `turn_model` picks the local model that reads
  the end of the caller's turn off the audio, `v1-mini` (livekit's, the default) or `smart-turn-v3`
  (Daily's Smart Turn v3, a new dependency, 8 MB of ONNX on the worker's CPU).
- The open stack: `infra/models/` holds three model servers for one NVIDIA GPU (`compose.yaml`:
  Whisper large-v3-turbo on Speaches, Gemma 4 12B and bge-m3 on Ollama, Kokoro-82M) and the
  providers row that points a box at them (`providers.json`); the wheel carries it as
  `pinecall/infra/models/`. `docs/the-open-stack.md` walks it from a bare GPU and has the numbers:
  about 1.6 s from the caller's last word to the agent's first on an RTX 3090, and why NVIDIA's
  streaming ASR is not the ears (its NIM drops sentences after the silences of a real call).

## 0.1.2 — A box from the package itself

- `pinecall-runtime box up --domains …` makes the machine it runs on a box, with no checkout: the
  wheel carries `infra/box` and `infra/postgres` as `pinecall/infra/`, and `box up` installs the
  system's packages, runs `install.sh` and releases this version from PyPI. `box upgrade` brings a
  box to the version it is run from, its names kept. `sudo uvx --from pinecall pinecall-runtime box up`.
- `release.sh` releases a PyPI version or a wheel's path (`PACKAGE=`) besides a built wheel (`WHEEL=`).
- `/usr/local/bin/pinecall-runtime` on a box runs any operator verb with the box's credentials.
- A box made from the package encrypts its backups to the key `--backup-key` gives, or makes none:
  the package carries no backup key, and `install.sh` enables the backup only with one.

## 0.1.1 — The runtime written again, in production

The runtime written again from a blank page, and the one that runs `box.pinecall.io` since
2026-09-29. `pip install pinecall` installs the gateway, the worker and `pinecall-runtime`, the
console and the widget inside; `docs/from-zero.md` walks a box to its first call.

- The skeleton: the domain types, the error hierarchy, the settings, the wire, the database pool
  and the migration runner, the schema as one migration, and the rules `make check` enforces.
- The call's log: an append-only store whose seq is born in one upsert (a sealed log refuses an
  append with 409), the facts of each call folded in the same transaction, the reducer the
  TypeScript and Ruby SDKs share (held to the protocol's golden log at every cut), the two
  projections (the tenant's, with `pii` fields masked when read, and the public one), and one
  in-process fanout whose slow reader gets a `log.gap` with a snapshot.
- Fixed: the usage feed counted every call's tokens and characters as zero; it read rows named
  `llm`/`tts` while a summary carries `llm_usage`/`tts_usage`.
- Fixed: a reader of a log sealed without `call.score` waited for ever; the stream asks the store
  whether the log is sealed before it reads the backlog.
- The suites run on a local Postgres (`make test`: colima, the image of `infra/postgres/`, one
  schema per test); `make test-sandbox` runs them on the sandbox database.
- Every vendor livekit ships is a vendor here: each installed plugin that exports an LLM, an STT
  or a TTS, and LiveKit Inference, built through one path with its constructor's own argument
  names. The runtime keeps no list of vendors, models or voices; a model or a voice a vendor does
  not have is that vendor's own error, in the call's log. `pinecall[voice]` installs all but
  four; `pinecall[voice-big]` adds aws, azure, google and speechmatics.
- An org's own key runs any installed vendor and any model of it; the box's keys run what
  `quotas.lends` lends the org. What the box offers, each vendor's options, the defaults, the
  voices per language and the prices are one row of the database, edited from the console.
- Voice and written calls run on the same livekit session: the same turns, tools, prompt,
  supervisor verbs and entries. A written call's `agent.reply` is instructions for one turn, as a
  voice call's always was.
- One gateway and one database serve production and the sandbox. A person signs in once and
  has one key for both worlds (production only with production access); a server has a key per
  world, `pc_live_` or `pc_test_`. Test calls stay off production processes because each world
  has its own fleet of workers, and every call is dispatched to the fleet of its world. Quotas and
  an agent's hold melody are kept per world (migration `0002_worlds.sql`).
- What a new org is given is the box's `admission` setting, edited from the console; a box with
  none limits nothing. No extension package is loaded.
- `PINECALL_VAULT_KEY` is required: every secret is sealed under it, and the gateway does not
  start without one. `PINECALL_WORLD`, `PINECALL_ELSEWHERE_URL`, `PINECALL_IDENTITY_URL`,
  `PINECALL_SANDBOX_URL`, `PINECALL_SANDBOX_KEY`, `PINECALL_PEER_KEY` and `PINECALL_EXTENSIONS`
  are gone.
- Orgs, people, keys, sign-in (password, one-use codes, pairing, sign-up, an org's own OpenID
  provider), tuning and lexicon versions per corner, widgets, hold
  melodies, personas, caller codes and mail. Usage is counted from the calls' summaries when a
  call or a turn asks to start; nothing is held in memory.
- Fixed: the tuning and the lexicon a call is built on are read in one query, so a write between
  two reads no longer records a version the call did not run on.
- Fixed: a written call counted each turn twice (the caller's and the agent's); a tool's
  structured output reached the model as Python's repr instead of JSON.
- The gateway and the worker. One gateway serves both worlds: the app socket, the worker's doors,
  the log's readers (a page or a stream), the supervisor's seats and verbs, a visitor's room token
  with the dispatch to its world's fleet signed inside, caller codes, and text calls on
  `WS /v1/chat`. A worker is livekit's `AgentServer` registered under its fleet's name
  (`pinecall`, `pinecall-sandbox`); it resolves whose call a job is from its dispatch or the number
  dialled, opens the log, and runs the session. Workers report to the gateway every five seconds;
  a full production fleet is answered by the overflow, a full sandbox refuses at the token door.
  A call the worker lost is sealed by the gateway's reaper. The vendors a call ran on the box's
  key are kept with its facts (migration `0003_lent.sql`).
- The box: `infra/box/` makes a machine from nothing (cloud-init, `install.sh`, Quadlet containers
  for LiveKit, SIP, egress, Redis and Postgres, the units); `make deploy` builds the console into
  a wheel and releases it; `pinecall-runtime` has `gateway`, `worker start`, `worker overflow`,
  `migrate up`, `keys fleet` and `doctor`.
- Fixed: a tool of a call whose agent nobody holds waited until its deadline; it is refused at
  once.
- Numbers and WhatsApp. An org holds many carrier accounts (Twilio accounts, SIP peers, WhatsApp
  numbers at Meta), sealed under the vault key; a number is imported from one of them ("we hook
  it": the account's trunk pointed here, found by where it points) or hooked by the org itself
  ("you hook it": admitted from its networks, nothing outside touched). On the SFU an org has one
  trunk per fence and one dispatch rule per world, so a number moves between worlds by a door
  (`PUT /v1/numbers/{number}/env`) without touching its trunk. The box buys numbers into either
  world on its own Twilio account, capped by that world's `numbers` quota. The door paths and
  answers are v1's, so the console reads them unchanged. `0004_carriers.sql`.
- Dialling out needs no trunk on the SFU: every leg is dialled with its trunk inline, so an emptied
  SFU dials on. A Twilio account's termination is one per account and box, with a credential per
  org on it. The dial guards count and write the ledger row in one transaction under the org's
  lock, and a number is a stranger to a world it never called.
- WhatsApp conversations are keyed by the org, the world, the number and the contact; a message
  whose agent nobody holds waits on the agent's log and is answered when an app declares the
  agent, with no polling. The box's Meta app and Twilio account are sealed rows of
  `box_settings`, not variables: `PINECALL_WHATSAPP_APP_SECRET`, `PINECALL_WHATSAPP_VERIFY_TOKEN`
  and the three `TWILIO_*` are gone.
- Fixed: a number the box bought was never admitted again when LiveKit's Redis was emptied, and a
  purchase rewrote an org's SIP peer trunk with Twilio's fence; the reconcile at start rebuilds
  every routed number with its own fence.
- Fixed: a supervisor's `end` on a WhatsApp conversation left it open; the conversation closes
  with its call.
- A WhatsApp account's number is listed from Meta beside the Twilio numbers and imported on its
  account; an agent answers at as many numbers as the org routes to it, of any kind.
- The account doors, on the one gateway, at v1's paths and in v1's shapes: `/.well-known/pinecall`,
  `whoami`, sign-in with a password or a one-use code, the orgs a person opens and the switch
  between them, a terminal paired from a browser, a forgotten password, an invitation accepted,
  the org's members and keys, sign-up, the org's identity provider and its mailbox, and
  `/v1/ops/whoami`. The sandbox asking production who a person is (`POST /v1/login/redeem`) is
  gone with the second instance; box-wide Google sign-in answers `503`. A key minted for a device
  is labelled with it, and a login code gives a copy of the key that minted it, the person
  included, dying when it dies.
- Evals: a suite of goldens runs through the app that holds the agent, one written call per
  golden and model, judged into a matrix stored as it goes (`POST /v1/evals/run`); a finished call
  is checked by code (`/replay`) or judged again (`/judge`); a persona is played by a model, in
  writing (`/caller`) or on a spoken line (`/voice`). A persona names the agents it may call. Every
  call is judged at hang-up when its org judges, the box names a judge model and the ceiling is
  above zero; the judge's tokens are counted and priced. Eval runs belong to an org and a world.
- Money is in US dollars, the currency providers price in: a call's cost, each priced line, the
  judge's cost, a persona run's cost, the org's monthly budget and the judging ceiling
  (`PINECALL_JUDGE_CEILING_USD`). Nothing is converted at a rate; the providers row carries no
  exchange rate. A persona names the agents it may call on the wire too.
- Retrieval in the call: a turn's recall and search run on the gateway for the worker
  (`POST /v1/calls/{call}/lookup`) and for a written call alike, every attached base searched in
  one pass, the contact's facts recalled when the agent keeps memory, `docs.sources` and
  `memory.ops` written on the log where they ran; a lookup that cannot run is a recoverable
  `<tool>_skipped` and the turn goes on. The seal writes what the call taught into the contact's
  memory between `call.ended` and `call.summary`, within `PINECALL_REMEMBER_BUDGET_S`, and a
  hang-up that cannot remember says so and seals all the same (`POST /v1/calls/{call}/remember`
  does the same on request).
- The rest of the doors, so every door of v1 answers: the org's meters (`/v1/usage`,
  `/v1/insights`, `/v1/limits`), an agent's settings and the org's lexicon versioned per world and
  scope, the pipeline report and the hold melody (uploads converted once to Ogg Opus), the widget,
  the providers catalogue as each org may run it, the org's own vendor keys, a vendor's voices and a
  sample, and the operator's `/v1/ops/*`: orgs, their people, keys, quotas per world, dial guards,
  the SSO break-glass, routes, the fleet and its cordons, every org's floor and meter, the box's
  mail and brand. The fleet loop (`fleet/hub.py`) grows and shrinks a fleet through a cloud script.
  `pinecall-runtime` gained `init`, `orgs`, `keys`, `routes`, `fleet`, `sessions`, `providers`,
  `memory reembed`, `migrate status` and `migrate plan`. Box-wide Google sign-in answers 503.
- The box's own configuration has doors: the providers row (`/v1/ops/providers`), the box's vendor
  keys (`/v1/ops/provider-keys/{vendor}`), admission (`/v1/ops/admission`) and the fleet of each
  world (`/v1/ops/fleets`); `pinecall-runtime providers seed` writes a box's first providers row.
  `infra/fleet/` holds the clouds a fleet grows on (gcp, aws, hetzner).
- The pages, every one under the name the site syncs: the gateway API and every door in one table,
  tokens, codes, projections (PII masked when read), the line, a deploy that never cuts a call, the
  smallest app, people, the operator's doors, the box's settings and floor, settings, pipeline,
  providers, dev verbs, the console's reads, retrieval, scaling, charging for it, a box in
  production, from zero, prompt injection.
- A call's cost counts everything it billed: each leg on the phone network in minutes begun,
  priced by the longest prefix of its number (`CostRow.unit` takes `minutes`), and the tokens of
  the model that writes memory at hang-up. The box's prices ship as `infra/box/prices.csv`, the
  operator's to edit, applied with `pinecall-runtime providers prices`.
- A name per world: `PINECALL_DOMAIN` is production's and `PINECALL_SANDBOX_DOMAIN` the sandbox's,
  both one gateway. The name a request comes in by is its world: the console served at the
  sandbox's name is the sandbox's (the page carries `pinecall-world` and `pinecall-elsewhere`),
  `pinecall-env` may only agree (on a door and on the app and chat sockets alike),
  `/.well-known/pinecall` says `world` and `elsewhere`, a browser
  joins LiveKit at its own name, and a number imported in a world points its carrier at that
  world's name.

- The box's floor (`GET /v1/ops/events`) says each frame's world: `env` is the world of the call,
  `null` on an agent's own entries, so a reader serving one world keeps its frames and no other.
- Fixed: an org's feed (`GET /v1/events`) carried both worlds' calls; it carries the calls of the
  world the key acts in, and the org's agents' own entries, which serve both.
- A lexicon is one agent's: `GET`·`PUT /v1/agents/{slug}/lexicon` and `GET …/lexicon/history`
  replace `/v1/lexicon`, with the same bodies, keys, scopes and versions. Migration 0008 copies
  each org's lexicon to every agent the org has in that world, keeping its version numbers, so
  `GET /v1/calls/{call}/settings` still reads the words an older call ran on.
- A persona is one agent's: its doors are `/v1/agents/{slug}/personas[/{name}[/runs]]`, a name is
  unique within the agent, its runs are that agent's calls, and `/v1/evals/voice` refuses with
  `404` a name nobody wrote for the agent it calls. `/v1/personas` is gone, and so is a persona's
  `agents`: a row written for some agents becomes one copy each, one written for every agent one
  copy per agent the org has. Fixed by construction: the console and the CLI never sent `agents`,
  so every edit of a persona made it callable by every agent again.
- An agent's own judges: a question about its job the org writes (`GET /v1/agents/{slug}/judges`,
  `PUT`·`DELETE /v1/agents/{slug}/judges/{name}`, `pinecall judges`), asked of the judge model at
  hang-up beside the runtime's panel, on every call or only on simulated ones; its verdict is in
  `call.score` under its name. `POST /v1/evals/judge/{call}` asks them too.
- The org's own judges: `GET /v1/org/judges`, `PUT`·`DELETE /v1/org/judges/{name}`, questions
  asked of every agent's calls beside the panel and the agent's own; a name may not be the
  panel's, nor the org's and an agent's at once, and a judge without a question is refused.
- The judge's ceiling is the providers row's `judge.ceiling_usd`, applied: a model judge asked
  once the call's judging reached it is `skipped`, saying so. `PINECALL_JUDGE_CEILING_USD` is gone.
- Hosted apps, the record: `POST /v1/hosted/{name}/releases` keeps a project's sources (a gzipped
  tarball, read before it is kept: no link, no path out of the project, 10 MB) as the app's next
  release; the first makes the app, counted against the new `hosted_apps` quota, and mints a
  server's token for it, sealed. `GET /v1/hosted`, the releases and a release's source back,
  `DELETE /v1/hosted/{name}`. The org's secrets per world, sealed and never read back:
  `GET /v1/secrets`, `PUT`·`DELETE /v1/secrets/{name}`. Nothing builds or starts a release yet.
  Migration 0018.
- The runner's doors, for the process that will run hosted apps: a key scope `runner`
  (`pinecall-runtime keys runner <world>`), `POST /v1/runner/heartbeat` (every app of the world
  with the host its release runs under, whether it registered, and the runner's `live` and
  `failed` reports kept), a release's source and an app's environment (the org's secrets, its
  token, the world's address). `GET /v1/hosted` says `live_release` and `failed_why`.
  Migration 0019.
- The runner: `pinecall-runtime runner start`, the process that keeps a world's hosted apps
  running on a machine of their own (`infra/apps/`: podman, gVisor, an nftables fence, one runner
  unit per world). It installs a release inside gVisor, starts it read-only, capped and on a
  network of its own under the release's host name, reports it live once its agents register, and
  stops the release it replaced only then; one that does not install, exits or never registers is
  reported failed with its last lines, and the one before keeps serving. `PINECALL_RUNNER_KEY`,
  `PINECALL_RUNNER_ROOT`, `PINECALL_RUNNER_IMAGE`, `PINECALL_RUNNER_RUNTIME`.
- Each runner sees only its world's containers (`pinecall.world`), so two share a machine; an app's
  network is DNS-less and on a bridge of the runner's own (`pca…`), the only one the fence matches;
  the install's HOME is a scratch folder on disk. Found putting bidfire and clinica-norte on the
  first apps machine. From a terminal: `pinecall deploy` and `pinecall secrets` (pinecall 0.9.10).
- A hosted app stopped and started (`POST /v1/hosted/{name}/stop`·`start`: its process drains, its
  releases and token stay), rolled back on the gateway (`POST …/rollback {release}`), and its logs
  read (`GET …/logs`: the runner sends its container's last 300 lines on the beat after they are
  asked for). The time each app served is counted per UTC day while a process of the org runs under
  one of its hosts: `GET /v1/hosted/usage`, and every org's at `GET /v1/ops/hosted-usage`, for a
  billing layer. Migration 0021.
- The hosting code reviewed, end to end. An org's secrets no longer ride podman's environment,
  which is root's (a secret named `LD_PRELOAD` was root's to run): the runner writes each host's
  environment to a tmpfs (`PINECALL_RUNNER_ENVIRONMENTS`, `/run/pinecall-runner/<world>`),
  mounted read-only into that container, and its shell reads it. A live host whose process exits
  is run again, its last lines kept as the app's logs; five exits in ten minutes and it is failed.
  Each app's steps run in a task of their own, planned from numbers alone (`runner/_plan.py`); a
  report is never lost to a beat that fails; release folders, environments and networks nothing
  uses are swept. A host's name is stamped with the org and the world, an app's name is 40
  characters at most, and the runner reads the gateway leniently (`live_host` is new): upgrade
  the runner first. The time served is counted in one statement a beat; an upload is capped while
  it streams. The operator's `pinecall-runtime` wrapper writes no bytecode, so a verb run as root
  no longer breaks the next release.
