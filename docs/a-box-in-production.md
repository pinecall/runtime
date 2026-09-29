# A box in production

From an empty VM to a phone call answered, on one machine: the gateway for both worlds, a worker
fleet per world, LiveKit with SIP and egress, Redis, Postgres, Caddy and nftables. Every file the
box is made of is in `infra/box/` ([its page](../infra/box/README.md)); every variable is
[the-environment.md](the-environment.md).

## 1. The machine

A VM (GCP `e2-standard-4`, Ubuntu 24.04), `infra/box/cloud-init.yaml` as its user-data with your ssh
key in it, and an ssh alias for it. Each name the box answers at is a DNS record pointing at it.

## 2. The box, once

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

## 3. Deploy

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
Each erasure is a row of `erasures`, which outlives the org. The journal keeps a month
(`journald.conf.d/pinecall.conf`, 1 GB at most) and Caddy writes no access log.

### Backups

`pinecall-backup.timer` runs `infra/box/backup.sh` at 03:00: `pg_dump -Fc` of the database, read
back by `pg_restore --list` before anything else, and a tar of the recordings, each encrypted with
`age` to `/etc/pinecall/backup.age.pub` (`infra/box/backup.age.pub`), with a manifest of their
sha256 before encryption. The private key is never on the box: whoever restores holds it. They
are kept 7 days in `/var/lib/pinecall/backups`; with `PINECALL_BACKUP_BUCKET=<bucket>` in
`/etc/pinecall/backup.env` (the operator's file, which `install.sh` never writes) each night's
files are copied to that bucket with the VM's own identity, which needs `storage.objects.create`
on it and nothing else; the bucket's lifecycle rule deletes them after 35 days.

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
  `pinecall-runtime providers prices infra/box/prices.csv --apply`.
- the **box's vendor keys** (`/v1/ops/provider-keys/{vendor}`): offering a vendor is holding its key.
- **admission** (`/v1/ops/admission`): what a new org is given in each world.
- the **mail** the box posts through, its **brand**, the **fleets** each world dispatches to.

## 6. The first org and the first person

From a laptop, with `PINECALL_GATEWAY_URL` the box's address and `PINECALL_OPS_KEY` the box's key:

```bash
pinecall-runtime init --org clinica --email you@example.com --person "Your Name"
```

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

- `make logs` first, whole: the sentence that stopped a unit is in it.
- A Quadlet key the box's podman does not know makes the generator skip the whole unit: "not found",
  not failed. `/usr/lib/systemd/system-generators/podman-system-generator --dryrun` says which.
- A gateway without `PINECALL_VAULT_KEY` does not start, and says so.
- A database restored from another box opens its sealed rows once that box's vault key is added:
  `install.sh vault-add`, the old key on stdin.
