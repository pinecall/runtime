# A box in production

From an empty VM to a phone call answered, on one machine: the gateway for both worlds, a worker
fleet per world, LiveKit with SIP and egress, Redis, Postgres, Caddy and nftables. Every file the
box is made of is in `infra/box/` ([its page](../infra/box/README.md)); every variable is
[the-environment.md](the-environment.md).

## 1. The machine

On Google Cloud the machine, its address, its firewall rules, the replica, the buckets and the
names are made by Terraform (`infra/terraform/`, its README: `make tf-plan` / `tf-apply`); what
follows is what that makes, and what to make by hand anywhere else — another cloud, a server of
your own, a machine at home.

- **Ubuntu 24.04** (Debian 13 works too: `box up` needs apt and systemd), 4 vCPU, 16 GB, 30 GB of
  disk, a public IPv4. GCP's `e2-standard-4` is what Pinecall runs on.
- **Two DNS names pointed at it**, production's and the sandbox's (`voice.example.com`,
  `sandbox.voice.example.com`), before the box is made: Caddy takes their certificates from Let's
  Encrypt the moment it starts, and a name that points elsewhere makes it wait and retry. One name
  alone serves both worlds, and the console there is production's.
- **The cloud's firewall open for**: tcp 22, tcp 80 and 443 (the console, the API, LiveKit's
  signalling), tcp 7881 and udp 7882 (WebRTC), udp 10000–10199 (the phone's audio, RTP), and tcp
  and udp 5060 from your carriers' signalling addresses alone (Twilio's are in
  `infra/box/nftables.conf`; on GCP Terraform keeps that rule and a deny of everyone else's 5060
  beside it). The box fences 5060 again itself, with nftables, and opens it further to the
  carriers its operator admits and the addresses he approves, every minute
  ([telephony.md](telephony.md), "The fence, twice").

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
runtime, its migrations, the restarts. A version of your choosing: `--from pinecall==0.1.5`.

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
for, the workers of both worlds replaced one at a time and the overflow restarted, the doctor
([a-deploy-never-cuts-a-call.md](protocol/a-deploy-never-cuts-a-call.md)): a release waits for
each worker's drain, so it takes up to twenty minutes while calls are up. Then `tests/live` runs
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
| `pinecall_writer_waiting` | appends queued for the log's writer and not in a transaction yet: past a few hundred, the database is behind the calls |
| `pinecall_held{what}` | live log readers, app sockets and calls served, now |
| `pinecall_fleet{fleet,what}` | each fleet as its heartbeats say: workers, seats, busy, accepting; and `waiting`, its rooms with a caller no worker opened in the last ten minutes |
| `pinecall_vendor_failing{vendor}` | 1 for each vendor over its error line (half the calls handed it in two minutes saw it fail), as this gateway saw; its calls step over to their fallbacks |
| `pinecall_replication_lag_seconds{replica}` | how far behind each standby is in replaying the primary's WAL, from `pg_stat_replication`; empty with no replica |
| `pinecall_spend_unusual{org}` | how many times its usual day (the trailing four weeks' mean) an org's calls cost today, for each org over three times it; the same is `spend.unusual` on the agent's log, once a day |
| `pinecall_worker_state{fleet,worker,state}` | 1 for each worker heard lately, labelled how the roster counts it: `accepting`, `failing`, `full`, `draining`, `cordoned` ([scaling.md](scaling.md)) |
| `pinecall_worker_first_audio_p95_seconds{fleet,worker}` | each worker's first audio at the p95 over its last minute, as its heartbeat says; absent under five turns |

Counts start at zero when the gateway starts; the roster fills within one heartbeat, five seconds.

### The four alerts

`infra/cell/alerts.yaml` is four Prometheus alerting rules over these families, for any
Prometheus-compatible agent that scrapes the gateway on its machine: appends slow (p99 over 250 ms
for five minutes), a fleet busy over 0.8 for five minutes, a vendor over its error line for two,
a replica more than 30 s behind for five. Nothing else is alerted on until one of them misses an
incident.

On the box, `infra/box/alerts.sh apply` (run by `install.sh`) evaluates them: Prometheus scrapes
every gateway's `/metrics` every 15 s and Alertmanager mails what fires and what resolves, both on
127.0.0.1 alone. It is on when `/etc/pinecall/alerts.env` names who is told and the SMTP account,
and the password is sealed; off otherwise:

```
# /etc/pinecall/alerts.env
PINECALL_ALERTS_TO=ops@example.com
PINECALL_ALERTS_FROM=alerts@example.com
PINECALL_ALERTS_SMTP=email-smtp.us-east-1.amazonaws.com:587
PINECALL_ALERTS_SMTP_USER=…
```

`sudo /opt/pinecall/infra/box/install.sh secret PINECALL_ALERTS_SMTP_PASSWORD` seals the password
(read from stdin), `alerts.sh apply` again takes an edit, and `alerts.sh test` mails a test alert
through the same path.

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

(Pinecall's own — the two buckets, the user the box writes with, its policy — are
`infra/terraform/modules/store`; the access key is made by hand, as below, and never Terraform's.)

What leaves the box's disk — the nightly backup, the WAL archive, the recordings — goes to one
object store, spoken in S3: AWS S3, Google Cloud Storage through its S3 interoperability, Cloudflare
R2, Backblaze B2, a MinIO of your own. It is named in `/etc/pinecall/store.env` (the operator's
file, which `install.sh` never writes), and its secret is a sealed credential like the box's
others, never in that file:

```sh
# /etc/pinecall/store.env
PINECALL_S3_ENDPOINT=https://s3.eu-west-1.amazonaws.com   # the store's address
PINECALL_S3_REGION=eu-west-1                              # what the signature names
PINECALL_S3_ACCESS_KEY_ID=AKIA…                           # the key the box writes with
PINECALL_BACKUP_BUCKET=acme-pinecall-backups              # backups and the WAL archive
PINECALL_RECORDINGS_BUCKET=acme-pinecall-recordings       # recordings, when they leave the disk
# then the secret, on stdin, and the units that read the file:
#   sudo /opt/pinecall/infra/box/install.sh secret PINECALL_S3_SECRET_ACCESS_KEY
#   sudo systemctl restart 'pinecall-gateway@*' 'pinecall-worker@*'; sudo /opt/pinecall/infra/box/wal.sh apply
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

A recording is the file the call's own session writes under `/var/lib/pinecall/recordings/<call>/`:
livekit's recorder in the job process, a stereo Ogg Opus at 24 kHz, the caller on the left and
every other voice on the right, on one timeline: the agent as it was played, a supervisor who took
over and the far end of a warm transfer heard from their own tracks while they spoke, and the hold
melody laid in from its clip where it sounded. A call that had one of those is decoded and
encoded once more when it closes, a stretch at a time, nothing of its length held in memory: about
a fortieth of its length on one core (ten minutes in ~15 s), inside the worker's minute to seal for
the calls a phone line takes; a call of half an hour or more with a hold, a takeover or a transfer
may run past that minute and keep no recording, and a mix made live is the fix the day such calls
come. A call with none of those is never touched again. The session
closes it before the call is sealed, and the worker seals it under the call's own key at once
(`audio.sealed`, the plain file removed; [security/private-values.md](security/private-values.md)).
With `PINECALL_RECORDINGS_BUCKET` and the object store in `store.env` (read by the gateway, the
workers and the retention run; restart them after adding it), the worker uploads that file to
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
`store.env`, each night's files are copied to `<bucket>/<stamp>/` too; the bucket's lifecycle
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

With the backup bucket and the object store in `store.env` and the backup key on the box,
`infra/box/wal.sh apply` (which `install.sh` runs, and the operator runs after editing `store.env`)
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
same `store.env` and the store's secret sealed; there, add the old box's vault key with
`install.sh vault-add`). The private backup key comes to the box for the restore and leaves after
it. `<stamp>` is the newest night before the target minute (`sudo bash -c '. /opt/pinecall/infra/box/objects.sh
&& rclone lsf store:$PINECALL_BACKUP_BUCKET/'`):

```sh
# 1. Nothing writes while the database goes back; the database as it stands is kept aside.
sudo systemctl stop 'pinecall-gateway@*' 'pinecall-worker*@*' 'pinecall-overflow@*' pinecall-wal.timer
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
sudo systemctl start pinecall-wal.timer 'pinecall-gateway@*' pinecall-worker-a@production \
  pinecall-worker-b@production pinecall-worker-a@sandbox pinecall-worker-b@sandbox \
  pinecall-overflow@production pinecall-doctor
```

A target the archive does not reach yet ends step 4 in `FATAL: recovery ended before configured
recovery target was reached`: pick a minute before the newest segment (`rclone lsl
store:<bucket>/wal/`, with `objects.sh` sourced as above) and run step 3 again. If the replay is wrong,
`podman volume import pinecall-postgres /var/lib/pinecall/before-restore.tar` (Postgres stopped)
puts back what stood before step 3.

### A replica, and failing over to it

A second machine in the box's own network (Ubuntu 24.04, the box's size) holds a streaming
replica of its Postgres: every committed write reaches it within a second, so a box that is lost
loses at most that. Every step is a `pinecall-runtime cell` verb ([the-runtime-cli.md](the-runtime-cli.md),
"`cell`"), which runs the scripts the package carries (`infra/cell/`, [its page](../infra/cell/README.md)).
Setting one up, from a laptop that reaches both (`box` and `replica` are ssh aliases; `10.128.0.5`
is the box's private address, `10.128.0.7` the replica's; the replica made with
`infra/box/cloud-init.yaml`, so uv is on it):

```sh
# The box lets the replica stream: the role and its password (drawn and sealed on the box), the
# slot, its pg_hba line, and Postgres published on 10.128.0.5 and fenced to 10.128.0.7 alone.
# The first time, Postgres restarts once: a few seconds.
ssh box sudo pinecall-runtime cell allow-replica 10.128.0.7
# The replica joins at the box's version: the package's files copied there, the password sealed
# there; each secret goes from one machine's store to the other's through the pipe, never onto a
# screen.
ssh box sudo -n systemd-creds decrypt --name=PINECALL_REPLICATION_PASSWORD \
    /etc/credstore.encrypted/PINECALL_REPLICATION_PASSWORD - \
  | ssh replica sudo uvx --from pinecall==<version> pinecall-runtime cell join-replica 10.128.0.5
# What the promoted database is served with: its password, the vault key its sealed rows open
# with, and the ops key. `box up` there keeps all three.
for name in DATABASE_URL PINECALL_VAULT_KEY PINECALL_OPS_KEY; do
  ssh box sudo -n systemd-creds decrypt --name=$name /etc/credstore.encrypted/$name - \
    | ssh replica sudo /opt/pinecall/infra/box/install.sh secret $name
done
```

`cell join-replica` ends by printing `streaming from 10.128.0.5, received up to …`; on the box,
`sudo podman exec pinecall-postgres psql -U pinecall -d pinecall -c 'SELECT client_addr, state,
replay_lag FROM pg_stat_replication'` shows it `streaming`. The slot keeps at most 10 GB of WAL
for a replica that stops reading; past that Postgres drops it and the replica joins again.
`cell forget-replica` undoes the box's side (a retired replica must not hold a slot).

**Failing over**, when the box is lost. The target is **RTO 15 minutes**, from the decision to
calls answered at the same names. On the replica:

```sh
sudo uvx --from pinecall==<the box's version> pinecall-runtime box failover
```

It refuses a Postgres that is not a standby, promotes it (`pg_promote`), checks it left
recovery, prints when the last replayed write was (what the failover lost), and prints the rest:
keep the old box from coming back as a second primary, point the names (and any trunk that names
the old box's address) at this machine, copy `store.env` and `backup.age.pub` and seal the store's
secret, `box up --domains
…` here, and the doctor. It repoints, stops and deletes nothing itself. `box up` finds the
database running and keeps the three secrets sealed above; its numbers go back on the SIP service
when the gateway starts. Afterwards this machine is the box, and a new replica joins it the same way.

**Drilled on 2026-10-01**, on three throwaway machines of one network, the store an S3 endpoint
on one of them (`rclone serve s3`; Google's organisation policy refused an HMAC key, which any S3
endpoint replaces):

| drill | what was done | measured |
|---|---|---|
| restore to a minute | a marker written before a minute T and one after; WAL archiving on, a base backup, then the steps above to T | the database back at T in **79 s** (`recovery stopping before commit … 14:36:29`): the marker before T there, the one after T not; the write of 14:36:29 was already in the bucket 50 s later — **RPO under a minute** |
| failover | a marker written, the box's VM stopped outright; `box failover` and `box up` on the replica | promoted **17 s** after the decision, no write lost (the replica was 0.6 ms behind); `box up` until the gateway answered: **145 s**; the doctor all `ok`. **RTO under 3 minutes** plus the time the names take to move |

What the drill found: until 2026-10-01 the replica could not have streamed at all — Ubuntu 24.04's
podman ignores the `.container.d` drop-in that published Postgres toward it (fixed: the publish
lines are written into the installed container file). And a promoted replica keeps archiving off
until `store.env` and the store's secret are copied to it, as step 3 of the failover says.

### Two gateways, or more

The box runs its gateway twice, `pinecall-gateway@8080` and `pinecall-gateway@8081`, each a
process of its own; any serves any door of any call, and they tell each other what they did
through LiveKit's Redis, database 1 (`PINECALL_REDIS_URL`). Caddy sends every request to either:
the box's own workers knock at `127.0.0.1:8088`, a site of Caddy's on loopback, and name the call
in `Pinecall-Call`, which Caddy hashes so a call's requests stay on one gateway while it lives. A
release restarts `@8081`, waits for it to answer, then `@8080`: Caddy steps over the one
restarting, trying the other, so no request of a call is lost.

A gateway process holds about 300 calls a core (measured 2026-10-01: 1 200 calls at once on four
processes of a 16-vCPU box, 3.6 cores of gateway, 1.9 of Postgres, 2.4 of Caddy; append p50 26 ms,
p99 410 ms). A bigger box runs more of them: `sudo systemctl enable --now pinecall-gateway@8880
pinecall-gateway@8881`, and their addresses added to the `reverse_proxy` line of the `(gateways)`
snippet in `/etc/caddy/Caddyfile`, then `sudo systemctl reload caddy`. **Not 8082–8085**: those
are the workers' own health servers (`PINECALL_WORKER_HTTP_PORT`, one per worker slot), and a
gateway told to listen there fails to bind while Caddy sends a share of every call to a worker,
which answers `404: Not Found`. A gateway killed with calls live loses none of them (measured: a
gateway killed every minute under 1 200 calls, 8 000 calls sealed, every log read back whole):
Caddy sends the next request of its calls to another gateway, which serves them from what the
log and `call_openings` keep; a worker whose open or seal was in flight asks again and gets the
same call. Caddy itself costs about what a gateway process does at this rate: past one box, the
balancer is the cloud's, not Caddy's.

The second instance is enabled by `install.sh` only once Redis answers on `127.0.0.1:6379`: two
gateways that cannot tell each other what they did would each serve alone. A box whose Redis was
started before it was published on loopback gets it after `sudo systemctl restart pinecall-redis`,
which restarts LiveKit, SIP and egress with it, so every call in progress ends: in a window. Then
`sudo systemctl enable --now pinecall-gateway@8081`.

### Gateways on other machines

Past what one box's cores hold, gateways run on machines of their own beside it; the box keeps
Postgres, Redis, LiveKit, SIP and the workers. On the box, once per gateway machine:

```
sudo pinecall-runtime cell allow-gateway <its address>
```

lets that address alone reach Postgres (5432), Redis (6379) and LiveKit's API (7880) on the box's
address, writes its `pg_hba` line, and adds it to the gateways Caddy sends calls to
(`/etc/pinecall/gateways.env`). Then, from your laptop, the credentials go from one machine to the
other through a pipe, never through a terminal:

```
ssh box 'sudo pinecall-runtime cell gateway-credentials' |
  ssh gateway-machine 'sudo uvx --from pinecall==<version> pinecall-runtime cell join-gateway <box address> --processes 4'
```

`<version>` is the box's own (`sudo pinecall-runtime --version` on it). `cell join-gateway` copies the package's files there, seals the credentials, installs the runtime
at that version, starts four gateways on loopback (unset, one per two vCPUs) and that machine's
Caddy on its own address, port 8090, fenced to the box. A release there is
`sudo uvx --from pinecall==<version> pinecall-runtime cell release-gateway`, after the box's own
(the box migrates; a gateway machine never does). Redis asks a password of everyone since 2026-10-01: a box installed before gets it from
`install.sh` and takes it on `sudo systemctl restart pinecall-redis`, which restarts LiveKit, SIP
and egress: a window.

Measured on 2026-10-01 (a 16-vCPU box with two gateways, an 8-vCPU machine with four, a generator
apart): 1 200 calls at once, 5 510 sealed, 0 wrong, append p50 20 ms; the box spent 1.9 cores of
gateway, 1.75 of Postgres and 2.2 of Caddy, the gateway machine 2.8. **The gateway machine killed
outright (its four gateways and its Caddy, SIGKILL) for two minutes under 1 200 calls: 6 778
calls opened and sealed, none refused, none wrong, none left open, none billed twice**; the box's
two gateways took every call while it was gone, at p99 3.7 s. A recording kept on the box's disk
(`PINECALL_RECORDINGS`) is served by the box's gateways alone: with gateway machines, keep
recordings in the bucket ("Recordings, off the disk").

### A second LiveKit node

LiveKit runs as a cluster on the box's Redis: a node on another machine is the same image with
`redis.address` at the box (its password in `REDIS_PASSWORD`, as the box's LiveKit has it),
`rtc.node_ip` its own address, and the same `LIVEKIT_KEYS`; the box's fence lets it reach Redis
once `pinecall-runtime cell allow-gateway <its address>` has run. A room is placed on any node by load, and a
worker registered on one node takes rooms on the other (measured 2026-10-01 on livekit 1.13.7:
eight rooms created on the second node with the sandbox agent dispatched, every job assigned and
joined). Measured with `lk load-test`: 400 voice calls at once across two nodes, 0 packets lost,
2.25 cores a node. A room never spans nodes; a node lost ends its rooms (the caller hears the
sentence of a dead worker's call, by LiveKit's webhook) and new rooms land on the others.

### A second SIP node

One `livekit-sip` holds 200 phone calls (its RTP range). A second runs on a machine of its own on
the box's Redis, where the trunks and dispatch rules are, so every number the box routes is one it
answers. On the box, `pinecall-runtime cell allow-gateway <its address>` lets it reach Redis and LiveKit's API
(the same step as for a LiveKit node). On the SIP machine: the box's `/etc/pinecall/sip.yaml`
with `ws_url` and `redis.address` at the box's address, `LIVEKIT_API_KEY` and
`LIVEKIT_API_SECRET` from the box's `media.env`, both files root's alone, and the box's image run
on the host's network (`podman run --network host -v /etc/pinecall/sip.yaml:/sip/config.yaml:ro
--env-file …`). Its 5060 is fenced to the carrier's networks as the box's is, and its RTP range
opened like the box's: two machines may share one range. The carrier spreads calls across both:
a second origination URI on the trunk, `sip:<the SIP machine's name>:5060`. Measured on
2026-10-01 (`infra/lab/`): six calls at once, three through each node, all six answered, every
turn answered, first audio p95 1.65 s, as through one node.

### Workers on other machines

Past what one box holds in calls (six on 4 vCPU, measured below), one world's workers run on
machines of their own; the box keeps Postgres, Redis, LiveKit, SIP, the gateways and the other
world's workers. A worker machine reaches the box's LiveKit and the gateways' balancer (8088, at
the box's own address, `PINECALL_HERE` in `box.env`) and nothing else of it: no Postgres, no Redis.
It needs the recordings bucket ("Recordings, off the disk"): its disk is no gateway's, and
`worker-credentials` refuses a box that keeps recordings on its own. On the box, once per machine:

```
sudo pinecall-runtime cell allow-worker <its address>
```

opens 7880 and 8088 to that address — or to a range, the subnet the fleet loop makes its machines
in (`allow-worker 10.100.0.0/24`, once) — and restarts nothing: LiveKit's API is published on the box's
own address by `install.sh` since 0.1.4, behind the fence. A box installed before gets one restart
of LiveKit (SIP and egress with it) here, said as it happens: a window, once. Then, from your
laptop, the fleet's credentials
go from one machine to the other through a pipe, never through a terminal:

```
ssh box 'sudo pinecall-runtime cell worker-credentials production' |
  ssh worker-machine 'sudo uvx --from pinecall==<version> pinecall-runtime cell join-worker <box address> production'
```

`<version>` is the box's own. `cell join-worker` copies the package's files there, seals the fleet key and the LiveKit pair,
installs the runtime at that version, and starts one worker of that fleet holding `--calls` at
once (the fleet loop's machines come from an image made with `cell image-worker` instead, which
carries no credential, and enroll at their first boot:
[../infra/fleet/README.md](../infra/fleet/README.md)) (unset, four per vCPU: on a machine of workers
alone a call costs ~0.2 vCPU, so LiveKit's 0.7 line falls at about three per vCPU; the number is
counted, never read off the CPU, as "Capacity is counted in calls" in [scaling.md](scaling.md)
says). A release there is `sudo uvx --from pinecall==<version> pinecall-runtime cell release-worker`,
after the box's own: the worker drains its calls, the fleet's other machines take new ones
meanwhile. `pinecall-runtime cell forget-worker <address>` closes the fence to a machine that is gone. The box's own workers of that world may stay (a box
of 4 vCPU keeps two seats each) or be stopped, as its cores are needed.

Measured on 2026-10-02 (`infra/lab/`: an e2-standard-8 of workers, the box an e2-standard-4, SIP
callers, the vendors faked): 24 calls at once on the worker machine, 3.5 cores (~0.15 a call, the
recording included), first audio p95 1.3 s and every turn answered, each call live 0.1 s after it
rang; the box spent 1.8 cores on their media and their log. `cell release-worker` took 26 s with the
worker idle. The table is in [scaling.md](scaling.md), "A machine of workers alone".

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
- the **carriers** (`/v1/ops/carriers`): which of the catalog's carriers an org may bring a number
  through, and the addresses orgs asked 5060 to open to, approved or refused
  (`/v1/ops/carrier-networks`); `pinecall-fence.timer` writes the answer into nftables.
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

The org brings its carrier account, points a number here from a carrier the operator admits, or
buys one on the box's, and the number is routed to an agent in a world
([protocol/numbers.md](protocol/numbers.md); how a call reaches it is [telephony.md](telephony.md)).
The carrier sends the number's calls to the box's SIP port, which the fence opens to that
carrier alone. Before the first call the box places,
the org's dial guards are what they are by default: a destination must have called the org before,
six dials a minute, two hundred a day, ten minutes a call; `orgs dialling` changes them.

## 8. The fleet

One box holds a fleet per world. When calls outgrow it, workers on other machines join the same
fleets: on Google Cloud the box's `pinecall-fleet-loop@production` makes and lets go of
production's machines in a managed instance group with no autoscaler (Terraform's
`modules/fleet-gcp`); anywhere else `pinecall-runtime fleet loop` grows and shrinks them through a cloud script
([../infra/fleet/README.md](../infra/fleet/README.md), [scaling.md](scaling.md)).

## When it does not come up

- The journal first, whole: `make logs` from a checkout, or on the box
  `journalctl -u 'pinecall-gateway@*' -u 'pinecall-worker*@*' -u pinecall-migrate --since today`. The
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
