# Security

This runtime answers the telephone for other people's customers and keeps what they said. A
weakness in it is a weakness in every box that runs it, so please report one privately first.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository: **Security → Report a
vulnerability** at <https://github.com/pinecall/runtime/security/advisories/new>. It reaches the
maintainer and nobody else. If you cannot use it, write to the address in `pyproject.toml`
(`authors`) and put "security" in the subject.

Say what you found, where (a door, a module, a unit file), how to reproduce it, and what it
lets somebody do. A proof of concept against your own box is welcome; against a box that is not
yours is not.

You will get an answer within three working days, a fix or a decision within thirty, and credit
in `CHANGELOG.md` under *Security* if you want it. Please give the fix time to reach the boxes
before writing about it publicly; a release is announced in the changelog and on PyPI.

## What is in scope

- The gateway and the worker (`src/pinecall/`), the CLI, and the migrations.
- The box as this repository declares it: `infra/box/` (units, Quadlets, the fence, cloud-init)
  and the deploy (`Makefile`).
- The dependency lock, `uv.lock`: `pip-audit` runs over it every Monday (`.github/workflows/audit.yml`).

Out of scope: LiveKit, Postgres, Redis and the vendors' own services, which are reported to
them; and a box somebody configured against what `infra/box/README.md` says.

## What the tree already does

The contracts a report is checked against are public: `docs/security/prompt-injection.md` (what
a lookup found never becomes part of the prompt), `infra/box/hardening.conf` (what a process on
the box may touch), and `docs/multi-tenancy.md` (what a key is, and what it may name). Keys are
compared by sha256 and never printed; secrets on a box live in systemd's credstore.
