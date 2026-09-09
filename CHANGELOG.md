# Changelog

All notable changes to `pinecall`, the runtime. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers and tags are the
maintainer's call, so everything sits under Unreleased until one is cut.

## [Unreleased]

### Added
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
- The seam memory and retrieval land on: a view's markers (`<!-- knowledge: … -->`,
  `<!-- memory: … -->`, `<!-- retrieved: … -->`) are read by the runtime and replaced on the way
  into the request — the knowledge file's text once per call, the turn's fills when the caller's
  turn ends, under `PINECALL_FILL_BUDGET_MS` (250); at hang-up the call is remembered under
  `PINECALL_REMEMBER_BUDGET_S` (8.0). `AgentConfig` declares `knowledge` (`{path, text}`),
  `docs` and `memory`; the worker asks `POST /v1/calls/{call}/fill` and `/remember`; TEI is the
  embedder (`providers/embed/tei.py`, refused by name when it is not 1024 wide). A missed fill is
  an `error` entry (`memory_skipped`, `retrieval_skipped`, `remember_failed`), recoverable, and
  the call goes on.
- Memory itself: `memory/` and `0008_memory.sql`. `PgvectorMemory` keeps a contact's facts in
  `contact_memories`, bi-temporally — an update is a new row that supersedes the old one, an
  invalidation an end date, nothing is deleted but by `forget`, the right to be forgotten.
  `recall` runs cosine over `halfvec(1024)` and BM25 through pg_textsearch (`spanish`), fuses the
  two by rank (RRF, k=60), weighs recency (half-life 90 days) and confidence, and answers the best
  k at 0..1 with no model; `remember` is one call to the org's own model at hang-up, answering
  add / update / invalidate ops, parsed strictly and policed by the tenant's `MemoryPolicy`
  (a `forget` category never reaches the table). `facts_as_text` is what the memory marker becomes.
- The knowledge base (`knowledge/`, migration `0009_knowledge`): a tenant's Markdown files
  chunked by heading under ~350 tokens, each chunk under its heading path, embedded in batches
  and kept in `knowledge_chunks` with an HNSW index by cosine and a BM25 index in spanish; a push
  replaces the base whole in one statement; a search fuses both indexes by reciprocal rank
  (k=60, thirty candidates a branch) and hands the model `### path › heading` over each chunk.
  The row in `knowledge_bases` says which model wrote the vectors, so an `Embedder` now names
  its model (`model()`, what TEI's `/info` reports).
- The fill itself (`filling/`): one `Filling` per gateway is the session's `Filler` and
  `Rememberer` for every text call in-process and, over `POST /v1/calls/{call}/fill` and
  `/remember` (worker-only, 404 in the events door's words for another org's call), for every
  spoken one. A memory marker recalls the contact — `CallContext.remembered_as`: the resolved id,
  else the number on phone and WhatsApp, else nobody and nothing is written — a retrieved marker
  searches the agent's `docs.base` under the marker's own `k`/`min_score`, and the gateway writes
  `memory.ops` and `docs.sources` on the call's log with the turn's `speech_id`; at hang-up
  `remember` reads the call's turns off its log and runs on the org's own model and keys. The
  embedder is reached lazily and, down, is named in the error entry (`TEI at … did not answer`).
  Ring 1's sessions are built with the same `Filling`, so a golden carries its sources, and the
  grounded judge reads them.
- The knowledge base's doors, on the tenant's key: `PUT /v1/knowledge/{base}` (the base replaced
  whole), `GET /v1/knowledge`, `DELETE /v1/knowledge/{base}` (404 for a name never pushed); and a
  contact's, `GET /v1/contacts/{contact}/memory` (the history, current first) and `DELETE`
  (forget, the right to be forgotten). On a dev key each answers 503 with its sentence
  (`this gateway keeps no knowledge: it runs on a dev key`, `… no memory …`).
- The licence is spelled out where an operator meets it: the Apache-2.0 copyright line is
  filled (`Pinecall`), `README.md` has a License section, and `license-files` puts the text
  itself in the wheel and the sdist, so an install carries its licence.

### Changed
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

### Removed
- `PINECALL_TEXT_SEARCH_CONFIG`: nothing read it. The language BM25 stems in is the index's own,
  fixed in `0008_memory` and `0009_knowledge` (`spanish`).
- `doctor --bench`: it printed that no embedder was wired. The embedder is wired; the TEI line
  of the report now says what a down TEI costs (a skipped fill, said in the call's log).
- `LeakageJudge`: it had no user in the tree, and a judge given a declaration nobody wrote would be
  judging a rule nobody wrote. The idea returns with the milestone that declares what another
  tenant owns.
