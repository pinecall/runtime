# Changelog

## Unreleased — 2.0.0a0

The runtime written again from a blank page.

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
