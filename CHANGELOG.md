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
  embedder (`providers/embed/tei.py`, refused by name when it is not 1024 wide);
  `PINECALL_TEXT_SEARCH_CONFIG` names the language BM25 ranks in. A missed fill is an `error`
  entry (`memory_skipped`, `retrieval_skipped`, `remember_failed`), recoverable, and the call
  goes on.
- The knowledge base (`knowledge/`, migration `0009_knowledge`): a tenant's Markdown files
  chunked by heading under ~350 tokens, each chunk under its heading path, embedded in batches
  and kept in `knowledge_chunks` with an HNSW index by cosine and a BM25 index in spanish; a push
  replaces the base whole in one statement; a search fuses both indexes by reciprocal rank
  (k=60, thirty candidates a branch) and hands the model `### path › heading` over each chunk.
  The row in `knowledge_bases` says which model wrote the vectors, so an `Embedder` now names
  its model (`model()`, what TEI's `/info` reports).
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

### Removed
- `LeakageJudge`: it had no user in the tree, and a judge given a declaration nobody wrote would be
  judging a rule nobody wrote. The idea returns with the milestone that declares what another
  tenant owns.
