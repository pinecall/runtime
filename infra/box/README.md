# The box

One machine, v2's alone: Caddy, LiveKit (server, SIP, egress), Redis and Postgres as Quadlet
containers, and the runtime as systemd units — one gateway for both worlds, two workers of each
world's fleet, and the overflow. Every file here is one thing systemd, podman, Caddy or nftables reads.

```
cloud-init.yaml        first boot: packages, the deploy account, uv
install.sh             as root on the box: files to their places, secrets drawn, media plane up
release.sh             one release: a built wheel (make deploy) or a PyPI version (box up)
pinecall-runtime       /usr/local/bin/pinecall-runtime: the operator's verbs with the box's credentials
containers/            redis · livekit · sip · egress · postgres (Quadlet)
livekit.yaml sip.yaml egress.yaml
nftables.conf          5060 from Twilio and nftables.d/carriers.nft alone; 22, 80, 443, WebRTC for anyone
caddy/Caddyfile        TLS for PINECALL_DOMAINS; LiveKit's paths to the SFU, the rest to the two gateways
                       (a call's requests on one while it lives), and 127.0.0.1:8088 for the box's workers
fleets/<world>.env     PINECALL_FLEET and the worker's health port, per world
fleets/<world>-<a|b>.env   each of the box's two workers of a world: its health port, its warm processes
pinecall-gateway@.service   the gateway on a port: @8080 always, @8081 once Redis answers on loopback
pinecall-worker@.service · pinecall-overflow@.service
pinecall-worker-slot.conf  the drop-in that makes the worker template the box's a@ and b@
pinecall-migrate.service · pinecall-doctor.service · pinecall-fleet-key@.service
pinecall-retention.service · pinecall-retention.timer   the nightly erasure of calls past their org's days
pinecall-fence.service · pinecall-fence.timer   as root, every minute: 5060 opened to the carriers admitted and the addresses approved
pinecall-backup.service · pinecall-backup.timer · backup.sh · backup.age.pub   the nightly encrypted backup
wal.sh · pinecall-wal.service · pinecall-wal.timer   the WAL archive to the backup bucket, for a restore to any minute
alerts.sh                                            the cell's four alerts evaluated on the box and mailed (alerts.env)
objects.sh             the object store (any S3-compatible one) as rclone speaks it, from store.env
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
PINECALL_SMTP_URL`.

What leaves the disk (backups, the WAL archive, recordings) goes to one S3-compatible object store —
AWS S3, Google Cloud Storage by HMAC key, R2, B2, a MinIO of your own — named in
`/etc/pinecall/store.env` by `PINECALL_S3_ENDPOINT`, `PINECALL_S3_REGION`,
`PINECALL_S3_ACCESS_KEY_ID`, `PINECALL_BACKUP_BUCKET` and `PINECALL_RECORDINGS_BUCKET`, with the
secret sealed by `install.sh secret PINECALL_S3_SECRET_ACCESS_KEY`. Unset, everything stays on the
disk. The examples per store, and Google Cloud's HMAC key: `docs/a-box-in-production.md` §The
object store. Only making machines (`infra/fleet/`, the fleet loop's `--cloud`) names a cloud. A database restored from elsewhere keeps opening its sealed rows once that
box's vault key is added: `… install.sh vault-add`, the old key on stdin.

## The worlds

`pinecall-worker-a@production` and `pinecall-worker-b@production` register with LiveKit as
`pinecall`, `-a@sandbox` and `-b@sandbox` as `pinecall-sandbox` (`fleets/*.env`). The gateway
dispatches every call to the fleet of its world, so a sandbox call never reaches a production
process. Each unit holds its own world's fleet key (`/etc/pinecall/fleets/<world>.credstore/`),
and the gateway refuses a call of the other world. The overflow runs for production only: a full
sandbox refuses at the token door.

## Two workers per world

`install.sh` installs `pinecall-worker@.service` twice, as `pinecall-worker-a@` and
`pinecall-worker-b@`, each with `pinecall-worker-slot.conf` as its drop-in: `%i` is still the
world (its fleet, its key), and `%j`, the letter, picks `fleets/<world>-<letter>.env` and names the
worker `<host>-<letter>` in the roster. Both take calls. The unit is `Type=notify`: the worker
tells systemd it is ready once LiveKit registered it and the gateway answered its heartbeat, so a
`systemctl restart` returns only when the new process can take calls. `release.sh` restarts every
`b@` (each drains while its `a@` takes the calls, then comes back), then every `a@` (drains while
the new `b@` takes them). The fleet is never closed and the overflow's sentence is never the
answer to a deploy; the release waits for both drains, up to ten minutes each.

The warm processes are split so the box keeps the five it kept with one worker per world
(livekit's four on four CPUs for production, one for the sandbox): production `a` 2 and `b` 1,
sandbox 1 and 1. Each warm process is one Python interpreter with every plugin imported; what a
worker holds, its warm processes included, is `systemctl show -p MemoryCurrent
pinecall-worker-a@production` on the box.

A box installed with one worker per world moves to two with `make box` (the new units enabled,
the old `pinecall-worker@<world>` disabled and left running) and then `make deploy`: the release
starts each `b@`, drains the old unit once `b@` is ready, then starts each `a@`.

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
