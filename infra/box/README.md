# The box

One machine, v2's alone: Caddy, LiveKit (server, SIP, egress), Redis and Postgres as Quadlet
containers, and the runtime as four systemd units — one gateway for both worlds and a worker
fleet per world. Every file here is one thing systemd, podman, Caddy or nftables reads.

```
cloud-init.yaml        first boot: packages, the deploy account, uv
install.sh             as root on the box: files to their places, secrets drawn, media plane up
release.sh             one release: a built wheel (make deploy) or a PyPI version (box up)
pinecall-runtime       /usr/local/bin/pinecall-runtime: the operator's verbs with the box's credentials
containers/            redis · livekit · sip · egress · postgres (Quadlet)
livekit.yaml sip.yaml egress.yaml
nftables.conf          5060 from the carrier alone; 22, 80, 443, WebRTC for anyone
caddy/Caddyfile        TLS for PINECALL_DOMAINS; LiveKit's paths to the SFU, the rest to the gateway
fleets/<world>.env     PINECALL_FLEET and the worker's health port, per world
pinecall-gateway.service · pinecall-worker@.service · pinecall-overflow@.service
pinecall-migrate.service · pinecall-doctor.service · pinecall-fleet-key@.service
pinecall-retention.service · pinecall-retention.timer   the nightly erasure of calls past their org's days
pinecall-backup.service · pinecall-backup.timer · backup.sh · backup.age.pub   the nightly encrypted backup
wal.sh · pinecall-wal.service · pinecall-wal.timer   the WAL archive to the backup bucket, for a restore to any minute
pinecall-postgres-image.service · hardening.conf · polkit/ · sysusers.d/ · tmpfiles.d/ · journald.conf.d/
```

## A new box, from the package

On an Ubuntu 24.04 machine whose names already point at it, as root, no checkout:
`sudo uvx --from pinecall pinecall-runtime box up --domains <production>,<sandbox> [--backup-key age1…]`.
The wheel carries this directory and `../postgres` as `pinecall/infra/`; `box up` installs the
system's packages cloud-init would, copies them to `/opt/pinecall/infra/`, runs `install.sh`, then
`release.sh` with `PACKAGE=pinecall==<its version>`. `box upgrade`, run from a newer `uvx --from
pinecall@latest`, does it again at the names in `/etc/pinecall/box.env`. The walk-through, the
ports and the options: `docs/a-box-in-production.md`.

## A new box, from a checkout

1. A VM (GCP `e2-standard-4`, Ubuntu 24.04) with `cloud-init.yaml` as its user-data, your ssh
   key in it; an ssh alias for it (`pinecall-runtime-v2`, the Makefile's `BOX`).
2. DNS: each name in `DOMAINS` pointed at the VM.
3. `make box DOMAINS=sandbox.pinecall.io`: infra/ copied to `/opt/pinecall/infra/`, `install.sh`
   run. The LiveKit pair, the database password, `PINECALL_VAULT_KEY` and `PINECALL_OPS_KEY` are
   drawn on the box, sealed by `systemd-creds` into `/etc/credstore.encrypted/`, never printed.
4. `make deploy`: the console built in, a wheel, `release.sh` on the box, `tests/live`, the journal.
   The first start mints each world's fleet key (`pinecall-fleet-key@production`, `@sandbox`).

A secret you bring is read from stdin: `ssh $BOX sudo /opt/pinecall/infra/box/install.sh secret
PINECALL_SMTP_URL`. A database restored from elsewhere keeps opening its sealed rows once that
box's vault key is added: `… install.sh vault-add`, the old key on stdin.

## The worlds

`pinecall-worker@production` registers with LiveKit as `pinecall`, `@sandbox` as
`pinecall-sandbox` (`fleets/*.env`). The gateway dispatches every call to the fleet of its world,
so a sandbox call never reaches a production process. Each unit holds its own world's fleet key
(`/etc/pinecall/fleets/<world>.credstore/`), and the gateway refuses a call of the other world.
The overflow runs for production only: a full sandbox refuses at the token door.

## Traps

- A Quadlet key this podman (4.9) does not know makes the generator skip the whole unit: the
  unit is "not found", not failed. `sudo /usr/lib/systemd/system-generators/podman-system-generator --dryrun`.
- The recordings directory is `2770 pinecall:pinecall-media` (gid 4200, fixed): egress writes as
  a uid nobody can name, the setgid bit keeps its files readable by the gateway.
- A 5060 rule in `input` fences nothing: a published port is DNAT'd and routed through `forward`.
  The fence is in `raw` prerouting.
- Redis holds livekit-sip's trunks and rules: its volume is the numbers. The gateway's live
  signal uses it too (database 1, channels under `pinecall:`), through `127.0.0.1:6379`. A change
  to its container takes `systemctl restart pinecall-redis`, which restarts LiveKit, SIP and
  egress with it (`Requires=`): every call in progress ends.
- `/etc/pinecall/livekit.yaml` is written by `install.sh` (the webhook's key name and the box's
  name filled in), and livekit-server reads it when it starts: a change takes
  `systemctl restart pinecall-livekit`, which ends every call in progress.
