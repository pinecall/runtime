# A box in production

From an empty VM to a phone call answered, on one machine: the gateway for both worlds, a worker
fleet per world, LiveKit with SIP and egress, Redis, Postgres, Caddy and nftables. Every file the
box is made of is in `infra/box/` ([its page](../infra/box/README.md)); every variable is
[the-environment.md](the-environment.md).

## 1. The machine

- **Ubuntu 24.04** (Debian 13 works too: `box up` needs apt and systemd), 4 vCPU, 16 GB, 30 GB of
  disk, a public IPv4. GCP's `e2-standard-4` is what Pinecall runs on.
- **Two DNS names pointed at it**, production's and the sandbox's (`voice.example.com`,
  `sandbox.voice.example.com`), before the box is made: Caddy takes their certificates from Let's
  Encrypt the moment it starts, and a name that points elsewhere makes it wait and retry. One name
  alone serves both worlds, and the console there is production's.
- **The cloud's firewall open for**: tcp 22, tcp 80 and 443 (the console, the API, LiveKit's
  signalling), tcp 7881 and udp 7882 (WebRTC), udp 10000–10199 (the phone's audio, RTP), and tcp
  and udp 5060 from your carrier's signalling addresses alone (Twilio's are in
  `infra/box/nftables.conf`). The box fences 5060 again itself, with nftables.

## 2. The box, from the package

On the machine, as root — no checkout, no build:

```console
$ curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
$ sudo uvx --from pinecall pinecall-runtime box up \
    --domains voice.example.com,sandbox.voice.example.com \
    --backup-key age1…
```

The wheel on PyPI carries `infra/box` and `infra/postgres` as `pinecall/infra/`, and `box up` does
with them what `make box` and `make deploy` do from a laptop, printing each step:

1. the system's packages: podman, Caddy, nftables, curl, rsync, openssl, age (apt), and nftables on
   at boot;
2. `uv` copied to `/opt/pinecall/bin/uv`, where the release runs it;
3. the package's `infra/` copied to `/opt/pinecall/infra/`;
4. `install.sh <names>`: the `deploy` and `pinecall` accounts, the directories, the fence, Caddy for
   the names, the containers (Postgres with `vector` and `pg_textsearch` built here, LiveKit, its SIP
   and egress, Redis), the units and timers, and the box's secrets — the LiveKit pair, the
   database password, `PINECALL_VAULT_KEY`, `PINECALL_OPS_KEY` — drawn once and sealed with
   `systemd-creds` in `/etc/credstore.encrypted/`, never printed;
5. `release.sh` with `PACKAGE=pinecall==<the version running>`: the runtime from PyPI into
   `/opt/pinecall/venv`, the migrations, the gateway, both worlds' workers and the overflow
   started, the doctor;
6. the address, the next three commands, and whether backups are on.

`--backup-key` is an age public key (`age-keygen -o box.key` makes a pair; keep `box.key` off the
box): the nightly backup encrypts to it, and with no key there is no backup at all — an
unencrypted copy of every call is never written. `--package <wheel path>` installs a wheel you
carry instead of PyPI's, for a machine that reaches no index. Run again, `box up` rotates nothing
and loses nothing: it is also how a box changes version.

**On the box, afterwards**, `/usr/local/bin/pinecall-runtime` runs any operator verb with the box's
own settings and sealed credentials:

```console
$ sudo pinecall-runtime init --org clinica --email you@example.com --person "You"
$ sudo pinecall-runtime doctor
$ sudo pinecall-runtime orgs invite clinica ana@example.com --name Ana
```

`init` prints the first admin's invitation on the box's public name
(`https://voice.example.com/invitations/…`); opening it sets their password, and from there the
console holds the rest.

**A newer version**: `sudo uvx --from pinecall@latest pinecall-runtime box upgrade` — the same steps
at the names the box already has (`/etc/pinecall/box.env`): the new package's `infra/`, the new
runtime, its migrations, the restarts. A version of your choosing: `--from pinecall==0.1.3`.

## 3. Or: the box, from a checkout

How Pinecall's own box is made, so a change is deployed before it is released. The machine is the
same, with `infra/box/cloud-init.yaml` as its user-data (your ssh key in it) and an ssh alias.

```bash
make box BOX=my-box DOMAINS=voice.example.com,sandbox.voice.example.com
```

Two names, one box: the first is production's and the second the sandbox's, and the name a request
comes in by is its world (the console at the second name is the sandbox's; a number imported in the
sandbox points its carrier there). One name alone serves both worlds, and the console there is production's.

`infra/` is copied to `/opt/pinecall/infra/` and `install.sh` run as root: the units, Caddy, the
nftables fence, the containers of the media plane, and the box's own secrets drawn once and sealed
(`systemd-creds`, `/etc/credstore.encrypted/`), never printed. Run again, it rotates nothing. A
secret you bring goes in from stdin: `ssh my-box sudo /opt/pinecall/infra/box/install.sh secret
PINECALL_SMTP_URL`.

Then each deploy:

```bash
make deploy BOX=my-box DOMAINS=voice.example.com,sandbox.voice.example.com
```

The console is built in, a wheel is built and copied to `/opt/pinecall/wheels/<sha>/`, and
`release.sh` runs on the box: the wheel into the venv, `migrate`, the gateway restarted and waited
for, the workers of both worlds and the overflow restarted, the doctor. Then `tests/live` runs
against the domain and `make logs` prints the journal of every unit since the gateway started,
whole. `make rollback WHEEL=<sha>` releases an older wheel still on the box. The deploy account
needs no sudo but the restarts, which a polkit rule allows. The first start mints each world's
fleet key into its unit's store.

## 4. The doctor

`pinecall-runtime doctor` asks each thing the box needs one question, a line each: the vault key,
the database and its migrations, the WAL archive, LiveKit, the gateway. Its exit is the deploy's.
The archive's line is `ok  archive: off: …` on a box without a bucket, and `ok  archive: on, …`
with what waits to ship and when the last segment was spooled; it says `NO` when Postgres could
not spool a segment, or when a segment has waited more than five minutes for the bucket.

### What the gateway measures: `GET /metrics`

`curl -s http://127.0.0.1:8080/metrics` on the box answers the gateway's measures in Prometheus's
text format, for a scraper on the same machine. It takes no key: the address is the fence. A
request from anywhere but the box's loopback is refused `403`, and so is anything that came
through Caddy, which marks every request it passes on with `X-Forwarded-For`. The families:

| family | what |
|---|---|
| `pinecall_append_seconds` | histogram: how long a worker's entry or batch took to write, at the door |
| `pinecall_entries_appended_total` | entries written through the two append doors |
| `pinecall_errors_total{code,vendor}` | `error` entries workers wrote; `vendor` is the plugin a failed component's label names, empty for the rest |
| `pinecall_pool_connections{state}` · `pinecall_pool_waiting` | the database pool: open, in use, its most; requests waiting now |
| `pinecall_pool_requests_total` · `pinecall_pool_wait_seconds_total` | connections asked of the pool, and the time spent waiting for one: their rates' ratio is the mean wait |
| `pinecall_held{what}` | live log readers, app sockets and calls served, now |
| `pinecall_fleet{fleet,what}` | each fleet as its heartbeats say: workers, seats, busy, accepting |

Counts start at zero when the gateway starts; the roster fills within one heartbeat, five seconds.

### What the box keeps, and for how long

Every call's log is kept until it is erased: by its org (`DELETE /v1/calls/{call}`,
`DELETE /v1/contacts/{contact}`), by the operator erasing the org (`DELETE /v1/ops/orgs/{named}`),
or by `pinecall-retention.timer`, which runs `pinecall-runtime retention run` at 04:00 and erases
every sealed call older than its org's `retention_days`. An org with no days keeps everything.
Each erasure is a row of `erasures`, which outlives the org. An erased phone call leaves its detail
record (numbers, times, how it ended) in `call_records`, and the `dials` ledger keeps every dial
placed or refused; the same nightly run forgets both after 24 months, and `pinecall-runtime
traceback <number>` reads them for a carrier. A vendor the operator enables on the box is a
subprocessor: the Privacy Policy's table names every one, and it is edited by hand in the same
change. The journal keeps a month
(`journald.conf.d/pinecall.conf`, 1 GB at most) and Caddy writes no access log.

### The object store

What leaves the box's disk — the nightly backup, the WAL archive, the recordings — goes to one
object store, spoken in S3: AWS S3, Google Cloud Storage through its S3 interoperability, Cloudflare
R2, Backblaze B2, a MinIO of your own. It is named in `/etc/pinecall/backup.env` (the operator's
file, which `install.sh` never writes), and its secret is a sealed credential like the box's
others, never in that file:

```sh
# /etc/pinecall/backup.env
PINECALL_S3_ENDPOINT=https://s3.eu-west-1.amazonaws.com   # the store's address
PINECALL_S3_REGION=eu-west-1                              # what the signature names
PINECALL_S3_ACCESS_KEY_ID=AKIA…                           # the key the box writes with
PINECALL_BACKUP_BUCKET=acme-pinecall-backups              # backups and the WAL archive
PINECALL_RECORDINGS_BUCKET=acme-pinecall-recordings       # recordings, when they leave the disk
# then the secret, on stdin, and the units that read the file:
#   sudo /opt/pinecall/infra/box/install.sh secret PINECALL_S3_SECRET_ACCESS_KEY
#   sudo systemctl restart pinecall-gateway 'pinecall-worker@*'; sudo /opt/pinecall/infra/box/wal.sh apply
```

| store | `PINECALL_S3_ENDPOINT` | `PINECALL_S3_REGION` | the key |
|---|---|---|---|
| AWS S3 | `https://s3.<region>.amazonaws.com` | the buckets' region | an IAM user's access key |
| Google Cloud Storage | `https://storage.googleapis.com` | `auto` | an HMAC key of a service account (below) |
| MinIO | `http://10.0.0.9:9000`, your own | `us-east-1`, unless yours says otherwise | a MinIO user's access key |

The key needs, on both buckets, to write, read, delete and list objects (`s3:PutObject`,
`GetObject`, `DeleteObject`, `ListBucket` on AWS; without the list right a missing object reads as
refused, not missing). The shell speaks it with `rclone` (`infra/box/objects.sh`), the runtime with
its own signed requests (`process/_objects.py`). Unset, everything stays on the disk, as before. Only
making machines names a cloud (the fleet loop's `--cloud`, a script per cloud in `infra/fleet/`).

**A box on Google Cloud** keeps its buckets and takes an HMAC key: in the console, Cloud Storage →
Settings → Interoperability → create a key for a service account (or `gcloud storage hmac create
<service-account-email>`); give that account `roles/storage.objectUser` on both buckets; put its
access id in `PINECALL_S3_ACCESS_KEY_ID`, the endpoint and `auto` as above, and seal its secret with
`install.sh secret PINECALL_S3_SECRET_ACCESS_KEY`. The VM's own identity is no longer used.

### Recordings, off the disk

A recording is the file egress writes under `/var/lib/pinecall/recordings/<call>/`, sealed by
the worker under the call's own key as soon as it is written (`audio.sealed`, the plain file
removed; [security/private-values.md](security/private-values.md)). With
`PINECALL_RECORDINGS_BUCKET` and the object store in `backup.env` (read by the gateway, the workers
and the retention run; restart them after adding it), the worker uploads that file to
`<bucket>/<org>/<call>/audio.sealed` before it seals the call, and removes it from the disk.
`GET /v1/calls/{call}/recording` then reads it from the bucket with the player's byte range, so any
gateway serves it and it outlives the machine that took the call. A recording whose upload failed
stays on the disk, the worker's journal says `the recording of <call> stays on this disk`, and the
door serves it from there, as it does every recording made before the bucket was set. Erasing a
call, a contact or an org, and the nightly retention, delete the object as well as any file left.
It is a bucket of its own, not the backup's: it has no lifecycle rule (each org's
`retention_days` is the rule, and the backup bucket's 35 days would forget what an org keeps
longer). The nightly tar then holds only what is still on the disk. Unset, nothing changes.

### Backups

`pinecall-backup.timer` runs `infra/box/backup.sh` at 03:00: `pg_dump -Fc` of the database, read
back whole by `pg_restore -f /dev/null` before anything else, and a tar of the recordings, each encrypted with
`age` to `/etc/pinecall/backup.age.pub` — the key `box up --backup-key` wrote, or Pinecall's own
(`infra/box/backup.age.pub`) on a box made from the checkout — with a manifest of their sha256
before encryption. A box with no key there makes no backup (`install.sh` enables the timer only
when the key exists); the private key is never on the box: whoever restores holds it. They are
kept 7 days in `/var/lib/pinecall/backups`; with `PINECALL_BACKUP_BUCKET` and the object store in
`backup.env`, each night's files are copied to `<bucket>/<stamp>/` too; the bucket's lifecycle
rule deletes them after 35 days. A store half named (no region, no key id, no sealed secret) is
said in `journalctl -u pinecall-backup` and the night's files stay on the box. A backup taken
before an erasure still holds what went: 7 days on the box, 35 in the bucket, which an answer to
a person's "delete my data" says.

A restore, from a machine that holds the key:

```sh
age -d -i backup-age.key -o db.dump 20260930T030000Z.db.dump.age
sha256sum -c 20260930T030000Z.sha256 --ignore-missing      # the bytes the box dumped
pg_restore --clean --if-exists -d "$DATABASE_URL" db.dump    # on the box being restored
```

### The WAL archive: a restore to any minute

With the backup bucket and the object store in `backup.env` and the backup key on the box,
`infra/box/wal.sh apply` (which `install.sh` runs, and the operator runs after editing `backup.env`)
turns Postgres's WAL archiving on: `archive_mode`, `archive_timeout = 60s` and an `archive_command` set with `ALTER
SYSTEM`, and Postgres restarted once, a few seconds, when `archive_mode` changes. Each finished
segment is copied by Postgres to `/var/lib/pinecall/wal` on the same disk (the spool: Postgres
never waits on the network), and `pinecall-wal.timer` runs `wal.sh ship` every 10 s: each segment
gzipped, encrypted to the backup key and copied to `<bucket>/wal/`, then removed from the
spool. A quiet minute still closes its segment, so a committed write is off the box within about
70 s: **the RPO is one minute**, where the nightly dump alone lost up to 24 hours. With the
archive on, the 03:00 backup also takes a base backup (`pg_basebackup -Ft -z -X stream`),
encrypted the same way, to `<bucket>/<stamp>/` only. The bucket's 35-day lifecycle rule
forgets segments and base backups alike, so any minute of the last 34 days can be restored. A
segment sent twice after a crash is skipped (rclone's `--ignore-existing`, each segment looked for by
name), never overwritten; only one shipper runs at a time, so the look and the write are never
raced. Without a bucket nothing changes:
archiving stays off, as it has always been, and the doctor's line says so.

**When the bucket does not answer**, nothing waits: Postgres goes on, the spool grows by what the
box writes (a busy segment compresses to a few MB, a quiet one to almost nothing), and after five
minutes the doctor says `NO  archive: … segments waiting since …`; `journalctl -u pinecall-wal`
has rclone's reason. The spool empties itself when the bucket is back. If it is not back before
the disk fills, the copy fails, Postgres keeps its segments in `pg_wal` on the same disk, and a
full disk stops Postgres: the doctor's line is the warning, hours ahead at the box's rate.

**A restore to a point in time**, on the box (or a new `box up` box of the same version, with the
same `backup.env` and the store's secret sealed; there, add the old box's vault key with
`install.sh vault-add`). The private backup key comes to the box for the restore and leaves after
it. `<stamp>` is the newest night before the target minute (`sudo bash -c '. /opt/pinecall/infra/box/objects.sh
&& rclone lsf store:$PINECALL_BACKUP_BUCKET/'`):

```sh
# 1. Nothing writes while the database goes back; the database as it stands is kept aside.
sudo systemctl stop pinecall-gateway 'pinecall-worker@*' 'pinecall-overflow@*' pinecall-wal.timer
sudo systemctl stop pinecall-postgres
sudo podman volume export pinecall-postgres -o /var/lib/pinecall/before-restore.tar
# 2. That night's base backup, every segment since, and the settings that stop at the target
#    minute, into /var/lib/pinecall/wal/restore/.
sudo /opt/pinecall/infra/box/wal.sh fetch <stamp> /root/backup-age.key '2026-09-30 14:05:00+00'
sudo shred -u /root/backup-age.key
# 3. The data directory replaced by the base backup, told where to stop.
sudo podman run --rm --user postgres -v pinecall-postgres:/var/lib/postgresql/data \
  -v /var/lib/pinecall/wal:/var/lib/pinecall/wal --entrypoint bash \
  localhost/pinecall/postgres:17-pgvector0.8.6-pgtextsearch1.4.0 -ec '
    cd /var/lib/postgresql/data && find . -mindepth 1 -delete
    tar -xzf /var/lib/pinecall/wal/restore/base.tar.gz
    tar -xzf /var/lib/pinecall/wal/restore/pg_wal.tar.gz -C pg_wal
    cat /var/lib/pinecall/wal/restore/recovery.conf >> postgresql.auto.conf
    touch recovery.signal'
# 4. Postgres replays to the minute and opens on a new timeline.
sudo systemctl start pinecall-postgres
sudo podman logs -f pinecall-postgres   # "recovery stopping before commit … time …", then "ready to accept connections"
sudo podman exec pinecall-postgres psql -U pinecall -d pinecall -Atc 'SELECT pg_is_in_recovery()'   # f
# 5. The recovery settings cleared, the runtime back, archiving on the new timeline.
for name in restore_command recovery_target_time recovery_target_action; do
  sudo podman exec pinecall-postgres psql -U pinecall -d pinecall -c "ALTER SYSTEM RESET $name"
done
sudo rm -rf /var/lib/pinecall/wal/restore
sudo systemctl start pinecall-wal.timer pinecall-gateway pinecall-worker@production \
  pinecall-worker@sandbox pinecall-overflow@production pinecall-doctor
```

A target the archive does not reach yet ends step 4 in `FATAL: recovery ended before configured
recovery target was reached`: pick a minute before the newest segment (`rclone lsl
store:<bucket>/wal/`, with `objects.sh` sourced as above) and run step 3 again. If the replay is wrong,
`podman volume import pinecall-postgres /var/lib/pinecall/before-restore.tar` (Postgres stopped)
puts back what stood before step 3.

### A replica, and failing over to it

A second machine in the box's own network (Ubuntu 24.04, the box's size) holds a streaming
replica of its Postgres: every committed write reaches it within a second, so a box that is lost
loses at most that. The files are `infra/cell/` ([its page](../infra/cell/README.md)), shipped in
the package beside `infra/box/`. Setting one up, from a laptop that reaches both (`box` and
`replica` are ssh aliases; `10.128.0.5` is the box's private address, `10.128.0.7` the replica's):

```sh
# The box lets the replica stream: the role and its password (drawn and sealed on the box), the
# slot, its pg_hba line, and Postgres published on 10.128.0.5 and fenced to 10.128.0.7 alone.
# The first time, Postgres restarts once: a few seconds.
ssh box sudo /opt/pinecall/infra/cell/primary.sh allow 10.128.0.7
# The box's files on the replica, then the replica joins; each secret goes from one machine's store
# to the other's through the pipe, never onto a screen.
ssh box 'sudo tar -C /opt/pinecall -c infra' | ssh replica 'sudo mkdir -p /opt/pinecall && sudo tar -C /opt/pinecall -x'
ssh box sudo -n systemd-creds decrypt --name=PINECALL_REPLICATION_PASSWORD \
    /etc/credstore.encrypted/PINECALL_REPLICATION_PASSWORD - \
  | ssh replica sudo /opt/pinecall/infra/cell/replica.sh join 10.128.0.5
# What the promoted database is served with: its password, the vault key its sealed rows open
# with, and the ops key. `box up` there keeps all three.
for name in DATABASE_URL PINECALL_VAULT_KEY PINECALL_OPS_KEY; do
  ssh box sudo -n systemd-creds decrypt --name=$name /etc/credstore.encrypted/$name - \
    | ssh replica sudo /opt/pinecall/infra/box/install.sh secret $name
done
# uv on the replica, for `box failover` and `box up`.
ssh replica 'curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh'
```

`replica.sh join` ends by printing `streaming from 10.128.0.5, received up to …`; on the box,
`sudo podman exec pinecall-postgres psql -U pinecall -d pinecall -c 'SELECT client_addr, state,
replay_lag FROM pg_stat_replication'` shows it `streaming`. The slot keeps at most 10 GB of WAL
for a replica that stops reading; past that Postgres drops it and the replica joins again.
`primary.sh forget` undoes the box's side (a retired replica must not hold a slot).

**Failing over**, when the box is lost. The target is **RTO 15 minutes**, from the decision to
calls answered at the same names. On the replica:

```sh
sudo uvx --from pinecall==<the box's version> pinecall-runtime box failover
```

It refuses a Postgres that is not a standby, promotes it (`pg_promote`), checks it left
recovery, prints when the last replayed write was (what the failover lost), and prints the rest:
keep the old box from coming back as a second primary, point the names (and any trunk that names
the old box's address) at this machine, copy `backup.env` and `backup.age.pub` and seal the store's
secret, `box up --domains
…` here, and the doctor. It repoints, stops and deletes nothing itself. `box up` finds the
database running and keeps the three secrets sealed above; its numbers go back on the SIP service
when the gateway starts. Afterwards this machine is the box, and a new replica joins it the same way.

**Drilled: not yet.**

## 5. What the box runs

From the console's box screens, or the operator's doors with the ops key
([protocol/operator-api.md](protocol/operator-api.md)):

- the **providers row** (`/v1/ops/providers`): the default vendor and model of each stage, the
  voices, the rates in dollars, the judge, the embedder. A box's first row can be written once from
  a file: `pinecall-runtime providers seed providers.json`, and its rates from the prices file:
  `pinecall-runtime providers prices infra/box/prices.csv --apply`. The row for open models on your
  own GPU, and the servers it points at, is `infra/models/` ([the-open-stack.md](the-open-stack.md)).
- the **box's vendor keys** (`/v1/ops/provider-keys/{vendor}`): offering a vendor is holding its key.
- **admission** (`/v1/ops/admission`): what a new org is given in each world.
- the **mail** the box posts through, its **brand**, the **fleets** each world dispatches to.

## 6. The first org and the first person

On the box, with its own credentials:

```bash
sudo pinecall-runtime init --org clinica --email you@example.com --person "Your Name"
```

Or from a laptop, with `PINECALL_GATEWAY_URL` the box's address and `PINECALL_OPS_KEY` its key.

The org, its first admin invited and made an operator of the box, and the link printed once. That
person signs in at the console and makes the rest: keys, people, numbers.

## 7. A phone number

The org brings its carrier account or buys a number on the box's, and the number is routed to an
agent in a world ([protocol/numbers.md](protocol/numbers.md)). The carrier's trunk points at the
box's SIP port, which the fence opens to that carrier alone. Before the first call the box places,
the org's dial guards are what they are by default: a destination must have called the org before,
six dials a minute, two hundred a day, ten minutes a call; `orgs dialling` changes them.

## 8. The fleet

One box holds a fleet per world. When calls outgrow it, workers on other machines join the same
fleets, and `pinecall-runtime fleet loop` grows and shrinks them through a cloud script
([scaling.md](scaling.md)).

## When it does not come up

- The journal first, whole: `make logs` from a checkout, or on the box
  `journalctl -u pinecall-gateway -u 'pinecall-worker@*' -u pinecall-migrate --since today`. The
  sentence that stopped a unit is in it.
- `box up` stops at the first step that fails and names it (`→ the box installed`); fix what it
  said and run it again: every step is safe to repeat.
- A name that does not point at the machine yet: Caddy retries its certificate, and after five
  failed tries Let's Encrypt refuses that name for an hour. Point the DNS first.
- The console loads but a call has no audio: the cloud's firewall does not let udp 7882 or
  10000–10199 in (section 1).
- A Quadlet key the box's podman does not know makes the generator skip the whole unit: "not found",
  not failed. `/usr/lib/systemd/system-generators/podman-system-generator --dryrun` says which.
- A gateway without `PINECALL_VAULT_KEY` does not start, and says so.
- A database restored from another box opens its sealed rows once that box's vault key is added:
  `install.sh vault-add`, the old key on stdin.
