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
the database and its migrations, LiveKit, the gateway. Its exit is the deploy's.

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
