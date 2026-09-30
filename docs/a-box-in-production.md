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

### Backups

`pinecall-backup.timer` runs `infra/box/backup.sh` at 03:00: `pg_dump -Fc` of the database, read
back whole by `pg_restore -f /dev/null` before anything else, and a tar of the recordings, each encrypted with
`age` to `/etc/pinecall/backup.age.pub` — the key `box up --backup-key` wrote, or Pinecall's own
(`infra/box/backup.age.pub`) on a box made from the checkout — with a manifest of their sha256
before encryption. A box with no key there makes no backup: `install.sh` enables the timer only
when the key exists. The private key is never on the box: whoever restores holds it. They
are kept 7 days in `/var/lib/pinecall/backups`; with `PINECALL_BACKUP_BUCKET=<bucket>` in
`/etc/pinecall/backup.env` (the operator's file, which `install.sh` never writes) each night's
files are copied to that bucket with the VM's own identity, which needs `storage.objects.create`
on it and nothing else; the bucket's lifecycle rule deletes them after 35 days. A backup taken
before an erasure still holds what went: 7 days on the box, 35 in the bucket, which an answer to
a person's "delete my data" says.

A restore, from a machine that holds the key:

```sh
age -d -i backup-age.key -o db.dump 20260930T030000Z.db.dump.age
sha256sum -c 20260930T030000Z.sha256 --ignore-missing      # the bytes the box dumped
pg_restore --clean --if-exists -d "$DATABASE_URL" db.dump    # on the box being restored
```

### The WAL archive: a restore to any minute

With a bucket in `backup.env` and the backup key on the box, `infra/box/wal.sh apply` (which
`install.sh` runs, and the operator runs after editing `backup.env`) turns Postgres's WAL
archiving on: `archive_mode`, `archive_timeout = 60s` and an `archive_command` set with `ALTER
SYSTEM`, and Postgres restarted once, a few seconds, when `archive_mode` changes. Each finished
segment is copied by Postgres to `/var/lib/pinecall/wal` on the same disk (the spool: Postgres
never waits on the network), and `pinecall-wal.timer` runs `wal.sh ship` every 10 s: each segment
gzipped, encrypted to the backup key and copied to `gs://<bucket>/wal/`, then removed from the
spool. A quiet minute still closes its segment, so a committed write is off the box within about
70 s: **the RPO is one minute**, where the nightly dump alone lost up to 24 hours. With the
archive on, the 03:00 backup also takes a base backup (`pg_basebackup -Ft -z -X stream`),
encrypted the same way, to `gs://<bucket>/<stamp>/` only. The bucket's 35-day lifecycle rule
forgets segments and base backups alike, so any minute of the last 34 days can be restored. The
VM's identity needs `storage.objects.get` on the bucket besides `create`: a segment sent twice
after a crash is skipped (`--no-clobber`), never overwritten. Without a bucket nothing changes:
archiving stays off, as it has always been, and the doctor's line says so.

**When the bucket does not answer**, nothing waits: Postgres goes on, the spool grows by what the
box writes (a busy segment compresses to a few MB, a quiet one to almost nothing), and after five
minutes the doctor says `NO  archive: … segments waiting since …`; `journalctl -u pinecall-wal`
has gcloud's reason. The spool empties itself when the bucket is back. If it is not back before
the disk fills, the copy fails, Postgres keeps its segments in `pg_wal` on the same disk, and a
full disk stops Postgres: the doctor's line is the warning, hours ahead at the box's rate.

**A restore to a point in time**, on the box (or a new `box up` box of the same version, with the
same `backup.env`; there, add the old box's vault key with `install.sh vault-add`). The private
backup key comes to the box for the restore and leaves after it. `<stamp>` is the newest night
before the target minute (`gcloud storage ls gs://<bucket>/`):

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
recovery target was reached`: pick a minute before the newest segment (`gcloud storage ls -l
gs://<bucket>/wal/`) and run step 3 again. If the replay is wrong,
`podman volume import pinecall-postgres /var/lib/pinecall/before-restore.tar` (Postgres stopped)
puts back what stood before step 3.

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
