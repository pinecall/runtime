# infra — what runs where

Each folder is one thing a machine reads:

```
apps/       the machine that runs the orgs' hosted apps, one gVisor container each, beside the box
            and never on it. Its page: apps/README.md
box/        the box: cloud-init, install.sh, release.sh, the systemd units, the Quadlet containers
            of the media plane (LiveKit, SIP, egress, Redis, Postgres), Caddy, nftables. Its page:
            box/README.md; the walk from a VM to a call: docs/a-box-in-production.md
fleet/      the clouds a fleet grows on: one script each, three verbs (create, delete, list).
            Its page: fleet/README.md; the loop that drives them: docs/scaling.md
models/     the open stack: three model servers for one NVIDIA GPU (compose.yaml) and the providers
            row that points a box at them. Its page: docs/the-open-stack.md
postgres/   the image of the box's Postgres 17 with pgvector and pg_textsearch, which `make db`
            also runs for the suites, on tmpfs
```

Nothing runs on a laptop but the suites' Postgres. A call is tried against a box, and the box's
secrets are drawn on it and sealed there, never in this tree.
