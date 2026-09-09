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
- The licence is spelled out where an operator meets it: the Apache-2.0 copyright line is
  filled (`Pinecall`), `README.md` has a License section, and `license-files` puts the text
  itself in the wheel and the sdist, so an install carries its licence.

### Removed
- `LeakageJudge`: it had no user in the tree, and a judge given a declaration nobody wrote would be
  judging a rule nobody wrote. The idea returns with the milestone that declares what another
  tenant owns.
