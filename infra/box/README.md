# The box

The same services as the dev stack (`../README.md`), on a machine a stranger can telephone,
declared rather than scripted. Five are on every box but a worker; the sixth, the embedder, is a
choice ("The embedder"). This directory is a box **declared**: every file in it is one thing
systemd, podman, Caddy or nftables reads. A fresh machine on any provider — a cloud that takes
cloud-init, which is all of them, or a bare one through a NoCloud seed — boots from
`cloud-init.yaml`; everything after arrives with `make deploy`, made by systemd from these files.

```
infra/box/
├── cloud-init.yaml            first boot, on any provider: the packages, the deploy account, uv,
│                              and the two values that are this box's (three lines marked YOURS)
├── Makefile                   the manifest: every file below, and where it is installed
├── sysusers.d/pinecall.conf   the service user
├── tmpfiles.d/pinecall.conf   every directory, with its owner and mode
├── nftables.conf              the fence, on the host: 5060 to the carrier and to nobody else
├── containers/                the media plane as Quadlet units: redis · livekit · sip · postgres ·
│                              egress, and tei where the box embeds here rather than at a vendor
├── livekit.yaml · sip.yaml · egress.yaml   what the three LiveKit containers mount
├── pinecall-secrets.service   the box's own secrets, drawn once, encrypted by systemd
├── pinecall-postgres-image.service   our Postgres image, built once per tag
├── pinecall-gateway@.service · pinecall-worker@.service      the two processes we write, one each
│                              per instance ("An instance")
├── pinecall-db@.service       an instance's database in the box's Postgres, made once
├── pinecall-worker-key@.service      an instance's fleet key, minted once
├── pinecall-operator-key.service     your key, production's, minted once
├── pinecall-overflow.service  the overflow agent, on the hub: it answers when every worker is full
├── pinecall-fleet.service     the fleet loop, on a hub whose box.env names a cloud (docs/scaling.md)
├── pinecall-app@.service      a tenant's app held here, one instance per app (docs/a-box-in-production.md §7)
└── caddy/Caddyfile            the one routing table every instance's site imports with its port
```

## What keeps a call's audio

A recording is one **room composite egress** per call, asked for by the worker the moment the room
exists and stopped when the call ends (`worker/recorder.py`). It writes
`/var/lib/pinecall/recordings/<instance>/<call>/audio.ogg` — each instance's `PINECALL_RECORDINGS`
is a directory of its own under the root egress mounts — which is the path `call.summary` points
at and the path `GET /v1/calls/{call}/recording` serves from.

The room, and not the session: everything anybody on the call heard is in the file — the caller,
the agent, **the hold melody** the agent publishes beside its own voice, and **a supervisor who
took the line**. livekit-agents' own recorder, which wrote these files before, knew two sources
and only two: the participant the session was pinned to, and its own TTS. The melody and the
supervisor were never in a recording, and nothing said so.

| | |
|---|---|
| the unit | `containers/pinecall-egress.container`, `docker.io/livekit/egress:v1.14.1` |
| its config | `egress.yaml` → `/etc/pinecall/egress.yaml`, mounted read-only |
| how it is found | this box's redis, by container name — a self-hosted egress registers through redis and through nothing else |
| its port | `127.0.0.1:7980`, the health port, which only the doctor knocks at (`egress` in the report) |
| what it costs | `audio_only` with no layout and no base URL is the one shape that runs on livekit's **SDK** and not on a headless Chromium, and it costs about a core per concurrent recording. `max_cpu_utilization` in `egress.yaml` is the ceiling: above it the recorder refuses, the call is taken anyway and its summary points at no audio |
| the mix | the DEFAULT one, never `DUAL_CHANNEL_AGENT`. Measured here on 2026-09-21, one call recorded each way: dual channel carries one track per participant, so the hold melody — a track of the agent's own, beside its voice — was subscribed to and dropped, and the tool's twenty seconds came back as digital silence on both channels. The default mix has it. A mode that drops the melody undoes the reason for recording the room |
| **whether** a call is recorded | the AGENT's setting, not the box's: `pinecall agent set --record on\|off`, per world and per corner, versioned like every other knob. There is no `RECORD` variable any more |

**A Quadlet key this podman does not know makes the unit vanish, not fail.** `GroupAdd=` is
Quadlet's own spelling and podman 4.9 has never heard of it, so the generator skipped the whole
file and `systemctl restart pinecall-egress` answered *Unit pinecall-egress.service not found* —
which reads like a typo in the name. `PodmanArgs=--group-add 4200` says the same thing to a
podman of this age. To see the reason at all:
`sudo /usr/lib/systemd/system-generators/podman-system-generator --dryrun`.

**The other trap, and it costs a directory nobody can read.** Egress runs in a container, as no uid this
box can name in advance — the service user's is assigned by `systemd-sysusers` and is a different
number on every machine. So the two meet on a **group with a fixed id**: `pinecall-media`, 4200,
declared in `sysusers.d/pinecall.conf` with the runtime as a member, and
`/var/lib/pinecall/recordings` is `2770 pinecall:pinecall-media`. The setgid bit is what keeps a
file egress writes readable by the gateway that serves it. Change one of the three and recordings
go on being written and stop being readable, which is a thing you find out a week later.

## The path a call takes

```
  ☎  a mobile
  │  PSTN
  ▼
  the carrier's SIP trunk (Twilio today: adopted rather than rebuilt, ../tools/twilio_trunk.py)
  │   the number is ATTACHED to the trunk, so the number's own voice_url is ignored
  │   Origination URI  sip:<the box>:5060;transport=udp   — the port is named, see below
  ▼  INVITE, UDP 5060, from the carrier's signalling networks and from nowhere else
  the box
  ├─ nftables            5060 accepted from the `carrier_signalling` set, DROPPED from anyone else
  ├─ livekit-sip         hide_inbound_port: an INVITE for a number no inbound trunk declares
  │                      is dropped without a reply
  ├─ SIPInboundTrunk     numbers=[the number], allowed_addresses=the same networks again
  ├─ SIPDispatchRule     one room per caller, `call-…`, dispatching the fleet `pinecall`
  ▼
  livekit-server         opens the room, dispatches the job
  ▼
  pinecall-worker        joins, finds the caller IN THE ROOM, and asks the gateway which agent
  ▼                      answers this number
  pinecall-gateway       the routes table: one row, no deploy, live from the next call
```

The dispatch rule names a **fleet**, never an agent and never a tenant: moving a number to another
business is `pinecall-runtime routes add`, and LiveKit never hears about it.

## Stand one up

Three steps, and the machine does the rest.

```bash
# 1. A machine. Any Linux with systemd ≥ 254 and podman ≥ 4.9; Ubuntu 24.04 is what we run.
#    Hand your provider cloud-init.yaml as the instance's user-data, with the three YOURS lines
#    filled: your ssh public key, the domain, the SFU's public URL, the role. It installs the packages,
#    makes the deploy account and its /opt/pinecall/app, installs uv, and raises the fence — a
#    machine without cloud-init does those four things by hand; they are the whole file.

# 2. The deploy. From this checkout, as the account cloud-init made. Which box is yours and
#    not the repository's: BOX and DOMAIN in deploy.local.mk beside the root Makefile, git-ignored.
make deploy
#    rsync puts this repository and the wire beside it under /opt/pinecall/app; `make install`
#    puts every file of infra/box/ where systemd reads it; `uv sync` builds the virtualenv as the
#    service user; `make converge` draws the box's secrets (pinecall-secrets), makes the one
#    instance a fresh box has, `production` — its env file from box.env's PINECALL_DOMAIN, its
#    three secrets copied from the box's first draw — writes its Caddy site and enables its units.
#    Then systemd, in this order: the Postgres image is built (pinecall-postgres-image), the
#    media plane comes up (redis, livekit, sip, postgres), pinecall-db@production finds the
#    database the container made, the gateway migrates the schema and opens, the two keys are
#    minted (pinecall-worker-key@production, pinecall-operator-key), the worker registers.

# 3. Your key. Minted on that first start (gone? `systemctl start pinecall-operator-key`). Read it once:
ssh <the box> sudo systemd-creds decrypt --name=PINECALL_OPERATOR_KEY /etc/credstore.encrypted/PINECALL_OPERATOR_KEY -
pinecall login https://<the domain>     # on the laptop, and the key is kept in ~/.pinecall/credentials
```

And the vendors' keys, which the box cannot draw for itself — from stdin, kept encrypted under
their names; then a restart, because a unit reads its credentials at start:

```bash
printf '%s' 'sk-ant-…' | sudo /opt/pinecall/venv/bin/pinecall-runtime box secret ANTHROPIC_API_KEY
sudo systemctl restart pinecall-gateway@production pinecall-worker@production   # or `make restart`
```

The names are the environment's own and each unit lists which it may see (the embedder's
`PERPLEXITY_API_KEY` or `OPENROUTER_API_KEY` is the gateway's alone). The box installs a plugin for
**every vendor LiveKit ships one for** but the four heavy ones it leaves out (`providers-big`: aws, azure, google, speechmatics), each reading its key under its own variable;
one not in the credstore is simply absent and costs nothing until `make secret
NAME=CARTESIA_API_KEY` puts it there. The whole table, with what each still wants, is `make
providers` from the checkout. **The box holds no credential for the repository.** It cannot clone
and it cannot fetch; the code is pushed to it by a person at a checkout, with `make deploy` — rsync,
ssh, make and curl, and no tool that does not come with a Unix. Its one build step runs on the
laptop: `scripts/console` bundles the console into `src/pinecall/gateway/console/`, the rsync
carries it, the gateway serves it at `/`. The rest is Python. The *box* decision page in the
maintainer's notebook argues both.

## An instance

A box runs one or more **instances** of the runtime: each its own gateway, worker, database, fleet
and keys, sharing the media plane (LiveKit, SIP, Redis, egress), Caddy, the vendors' keys, the code
and the virtualenv. Production is one; the sandbox — where agents are written — is another, the one
whose file says `PINECALL_WORLD=sandbox`; a staging would be a third. **Nothing about any of them is
written in a unit file.** An instance is two things, and `box.env` names which exist:

| | |
|---|---|
| `/etc/pinecall/instances/<name>.env` | its world, fleet, domain, loopback URL, worker port, recordings, identity, elsewhere and worker knobs — every one written, an unset one as `NAME=`, so a line `box.env` still carries never becomes this instance's. Read by every unit of it after `box.env`, and winning |
| `/etc/pinecall/instances/<name>.credstore/` | 0700, root: `DATABASE_URL`, `PINECALL_OPS_KEY`, `PINECALL_VAULT_KEY`, `PINECALL_WORKER_KEY` — what it holds alone, each loaded by its units by path (`LoadCredentialEncrypted=`) — and, in a pair, the other instance's peer key ("Peers") |
| `PINECALL_INSTANCES="production sandbox"` | in `/etc/pinecall/box.env`: the names this box runs, in the order a deploy restarts and doctors them. Unset, `production` |

Its units are the templates — `pinecall-db@<name>`, `pinecall-gateway@<name>`,
`pinecall-worker-key@<name>`, `pinecall-worker@<name>` — enabled once per listed name by the role
(a hub enables no worker; a worker box nothing but its workers). Its Caddy site is
`/etc/caddy/instances/<name>.caddy`, `<domain> { import pinecall <port> }`, written from its file on
every deploy; the port is its `PINECALL_GATEWAY_URL`'s, which is also the one its gateway binds. A
name taken out of `PINECALL_INSTANCES` is taken off Caddy and its units stopped on the next deploy;
its files and its database stay. The overflow agent, the operator's key and the fleet loop are
production's, one each: a sandbox has no overflow and no loop — its worker is the one on this box.

**Why an instance's secrets are not in `/etc/credstore.encrypted/`.** `ImportCredential=` searches
one store for the whole machine, and the doctor's wrapper imports every entry of it: a glob or a
wrapper over a shared store would hand the sandbox production's ops, vault and worker keys. So no
unit imports `PINECALL_*` any more; the box's shared secrets are imported by name, one line each,
and an instance's own are loaded by path out of its own store.

**A second instance, from the checkout.** One verb writes both files, as root over ssh; the name in
`box.env` is yours to write, because `box.env` is; the deploy does the rest:

```bash
make instance NAME=sandbox WORLD=sandbox DOMAIN=sandbox.example.com \
              IDENTITY=https://box.example.com ELSEWHERE=https://box.example.com
make ssh        # sudoedit /etc/pinecall/box.env → PINECALL_INSTANCES="production sandbox"
                # and production's own file names the sandbox back, once:
                #   sudo /opt/pinecall/venv/bin/pinecall-runtime box instance production \
                #     --world production --domain box.example.com \
                #     --elsewhere https://sandbox.example.com --force
make deploy     # pinecall-db@sandbox makes pinecall_sandbox; both gateways, workers, doctors
make migrate-post INSTANCE=sandbox   # once: the .post.sql migrations a new database has not run
make instance NAME=production WORLD=production DOMAIN=box.example.com \
              ELSEWHERE=https://sandbox.example.com SANDBOX=https://sandbox.example.com FORCE=1
make deploy     # the pair: both peer keys minted, both gateways restarted with them ("Peers")
```

`box instance` gives the next free hundred on loopback — 8180 for the gateway and 8182 for its
worker's health server beside production's 8080 and 8082; 8081 is the embedder's and 8090 the
notifier's — a fleet of `pinecall-<name>`, and recordings under
`/var/lib/pinecall/recordings/<name>`, made `2770 pinecall:pinecall-media` as the root is (egress
mounts the root, so it writes into every instance's directory). It refuses a sandbox with no
`IDENTITY`: a sandbox asks production who a person is, and one with nobody to ask would never
start. `box secrets --instance` draws the three; the fourth, the worker's, is minted by
`pinecall-worker-key@<name>` from its own gateway once that answers. **Production's are never
drawn**: its database is the one the container made at first boot, owned by the container's
superuser, so its secrets are the box's first draw (`pinecall-secrets`) and the deploy copies them
into its store. `pinecall-db@<name>` makes a missing database with a role of its own, takes
`CONNECT` on every database from `PUBLIC` — each role reaches its own and no other — and creates
both search extensions in it; production's is there, and for it the unit does nothing.

**A box born with one instance** converges on its next `make deploy` and nothing is done by hand:
`production.env` is written from `box.env`'s `PINECALL_DOMAIN` (and `PINECALL_WORLD`,
`PINECALL_ELSEWHERE_URL`, `PINECALL_IDENTITY_URL`, `PINECALL_FLEET`, `PINECALL_MAX_JOBS`,
`PINECALL_IDLE_PROCESSES` where it sets them) by the same verb, production's four secrets are
**copied** — not moved — from `/etc/credstore.encrypted/` into its store (the originals stay, for a
rollback and for the notifier, which imports the ops key there), the single `pinecall-gateway`,
`pinecall-worker` and `pinecall-worker-key` are disabled and their files removed, and `make
restart` stops each right before `…@production` starts. `conf.d/sandbox.caddy` and the Caddy
drop-in that handed it `box.env` go; `PINECALL_SANDBOX_DOMAIN` is read by nothing.

**The deploy never leaves a box that cannot start.** Before one unit is enabled or one site moves,
`make converge` checks every listed instance has its file, a domain and a port in it, and the
secrets its gateway loads by path (a worker box: its worker key) — a credential loaded by path that
is missing fails the unit's start, where an imported one is merely absent — and a missing one stops
the deploy with the verb that makes it, and the box keeps running what it ran.

### Peers

Production and its sandbox ask each other two things, and nothing else: production asks whose a
ring from a developer's own phone is (`GET /v1/agents/{slug}/rings-for`, [../../docs/protocol/numbers.md](../../docs/protocol/numbers.md)),
and the sandbox asks production which numbers the customers dial (`GET /v1/routes`). Each knocks
with a **peer key**: a fleet key of the other instance (`fleet` and `app`, org `default`, labelled
`peer-for-<name>`), minted at the other's gateway on its own ops key — in its own world, the only
one that honours it — and kept in this one's store. The name says what it opens:

| | kept in | opens | named in the env file by |
|---|---|---|---|
| `PINECALL_SANDBOX_KEY` | production's store | the sandbox | production's `PINECALL_SANDBOX_URL` |
| `PINECALL_PEER_KEY` | the sandbox's store | production | the sandbox's `PINECALL_IDENTITY_URL` |

**A pair is declared, not typed.** Production's file names its sandbox — `box instance production
… --sandbox https://sandbox.example.com --force`, or `make instance … SANDBOX=… FORCE=1` — and
`make converge` finds the pair: the listed instance whose `PINECALL_DOMAIN` is that URL's host.
It runs `pinecall-runtime box peer --among <the listed instances>`, which mints each key that is
missing, at the gateway already running, and never rotates one it finds; a gateway not answering
yet — the deploy that first brings the sandbox up — is said and skipped, and the next deploy mints
it. Then each gateway gets a drop-in,
`/etc/systemd/system/pinecall-gateway@<name>.service.d/peers.conf`, loading by path the peer keys
its store holds, and none when it holds none: a `LoadCredentialEncrypted=` path that is missing
fails the start, so it cannot live in the template every instance shares. The worker loads
nothing new — it asks its own gateway, which asks the peer. Last, the refusal production's gateway
would make at start (`PINECALL_SANDBOX_URL` without `PINECALL_SANDBOX_KEY`, or the reverse), made
before anything restarts: the deploy stops with the verb to run.

So the pair's first day is two deploys: one that brings the sandbox up with production naming no
sandbox yet, then production's file names it and `make deploy` again mints both keys and restarts
both gateways with them. A key to rotate is `box peer --from … --into … --force` and a restart;
the old one stays live at the instance that minted it until `keys revoke` (label `peer-for-…`).

**A pair on two boxes** is the one thing done by hand, from the checkout:
`make peer FROM=sandbox INTO=production INTO_BOX=deploy@203.0.113.9` mints at `FROM`'s gateway on
`BOX`, carries the key to `INTO_BOX` the way `worker-secrets` carries one — decrypted on one end,
encrypted on the other, through the laptop's pipe and no file — and takes it off `BOX`; then `make
deploy` on `INTO_BOX` writes the drop-in. Without `INTO_BOX`, `make peer` is the one-box verb.

### An instance on a box of its own

Moving an instance off the hub is a change of files, never of code: stand up a worker box ("Roles,
and a second box", below), copy the instance's env file to it, and point its
`PINECALL_GATEWAY_URL` at the hub (`https://<the instance's domain>`) — box.env's `LIVEKIT_URL`
names the hub's SFU. `make worker-secrets WORKER=… INSTANCE=<name>` copies its worker key into the
same path there. A whole instance — gateway and database too — on a VM of its own is that VM's
`PINECALL_INSTANCES` naming it, with the two files copied and its DSN pointing at wherever its
database now is ([docs/scaling.md](../../docs/scaling.md), the worker-box recipe, is the half that
is measured).

## Roles, and a second box

One machine runs everything (`PINECALL_ROLE=all`, the default). The day the worker needs a
machine of its own, the same directory stands up two: the **hub** — control plane and media
plane, no worker — and a **worker**, which dials the hub by name and holds nothing but calls.
The role is one line of `/etc/pinecall/box.env`, and the Makefile enables each role's units and
disables the others', so a box that changes role changes it on its next deploy. The embedder is
decided in the same file by the same rule — "The embedder", below.

A worker box needs three things the hub does not put in its unit: where the SFU and the gateway
are, and how many calls it holds — the first in its `box.env`, the other two in the file of the
instance whose calls it takes (production's, unless it says), copied from the hub and pointed back:

```
# /etc/pinecall/box.env
PINECALL_ROLE=worker
LIVEKIT_URL=wss://box.example.com          # the hub, under TLS: Caddy carries /agent to the SFU
# /etc/pinecall/instances/production.env — the hub's, with these two lines changed
PINECALL_GATEWAY_URL=https://box.example.com
PINECALL_MAX_JOBS=5                        # measured on THIS machine type — see below
```

And its own credentials, and no others: the LiveKit keypair and the vendors' keys copied from the
hub's store, and the instance's `PINECALL_WORKER_KEY` from the hub's instance store into the same
path here (`make worker-secrets`, over ssh, printed nowhere). Never `DATABASE_URL`, never the ops
key, never the vault key, and never an embedder's: a
worker has no database, guards nothing, and embeds nothing. It needs no port open but ssh. It
registers by an outbound WebSocket, LiveKit hands it jobs on that socket, and the media goes to the
hub's public UDP port. `nftables.conf` is the same file on every role; the doors it opens that
nothing listens on are doors to nothing.

## The fleet

The loop that stands workers up for you, the image it clones, the cordon that drains one, and the
overflow agent that answers the phone when every worker is full: [../../docs/scaling.md](../../docs/scaling.md).

## Slots

A worker with `PINECALL_MAX_JOBS` reports its load to LiveKit as **calls held over calls it may
hold**, and LiveKit stops routing to it at 0.7 of them — the same line it holds a CPU average to.
Without it, the worker reports the machine's CPU average, which is right for a box it shares with
the SFU and wrong for one it has to itself. `MAX_JOBS` is measured, never guessed: on the machine
type it will run on, calls with real audio in a loop, five more each step, until the p95 of first
audio crosses 1.8 s. That concurrency is the ceiling, and `MAX_JOBS` is **one under it**: LiveKit
re-reads the load every half second, and two jobs that arrive inside that window both see the old
count (livekit/agents#4884). The tolerance is one call, never more. A fleet is summed in slots:
`free = Σ(max − active)` over the workers that are up. That is the number a person watches and the
number a loop scales on — never a CPU.

## The embedder

A hub turns text into vectors twice: a knowledge push embeds every chunk of a tenant's files, and
a turn embeds the caller's sentence to find the few that answer it. There are two honest ways to
have that, and one line of `/etc/pinecall/box.env` — beside `PINECALL_ROLE`, read by the manifest
in the same way — chooses between them.

| `EMBED_PROVIDER` | what it is | what it costs |
|---|---|---|
| `tei`, the default | `pinecall-tei`, a container beside the other four, serving `BAAI/bge-m3` | 2.3 GB of weights, a couple of gigabytes of RAM, and no key at all |
| `perplexity` · `openrouter` | a vendor's door — Perplexity's is contextual, a chunk embedded while the model saw the file around it | one API key, in the credstore like every other, and every push leaves the building |

`make install` puts `pinecall-tei.container` under Quadlet only where the box asked for it, and
stops the container and takes the file away where it did not, so a changed line takes effect on the
next deploy. **A worker never runs it and needs none**: it holds calls, and every lookup in them is
run by the gateway on the hub. The container publishes on `127.0.0.1:8081` and nowhere else, exactly
as Postgres does — the gateway is a process on the host and reaches it over loopback — so the fence
has no line about the embedder and nothing outside can ask it anything. **The first start is
minutes**: a cold box fetches those 2.3 GB before the port answers at all, which is why the unit's
health start period is fifteen. Nothing waits for it — the gateway reaches the embedder lazily — so
the box answers the telephone throughout, and what a call loses meanwhile is a search, written into
the log as `search_skipped`, while a knowledge push answers 503 and says so. Afterwards the weights
live in the `pinecall-tei` volume and a restart is seconds; they are **not** removed with the unit,
so a box that will not come back frees them by hand: `podman volume rm pinecall-tei`.

Switching to a vendor, from the checkout — the key first, so the box is never configured for a
door it cannot open — and back again by emptying the same line:

```bash
printf '%s' 'pplx-…' | make secret NAME=PERPLEXITY_API_KEY
make ssh                     # sudoedit /etc/pinecall/box.env → EMBED_PROVIDER=perplexity
make deploy                  # which ends with the one command that proves it, `make doctor`:
#  ✓ embedder  perplexity · pplx-embed-context-v1-4b — https://api.perplexity.ai/v1 — a word embedded, 1024 wide
#  ✓ embedder  tei · BAAI/bge-m3 — http://127.0.0.1:8081 — a word embedded, 1024 wide
```

**A vector is comparable only to vectors of the same model**, and the line above changes the
model: `tei` embeds with `BAAI/bge-m3`, `perplexity` with `pplx-embed-context-v1-4b` — its larger
contextual model, asked for 1024 wide (Matryoshka; it answers 2560 unasked) so it fits the columns
— unless `EMBED_MODEL` says otherwise. After the switch, `knowledge.search` refuses every base the old model
pushed — `409`, `base <name> was pushed with <old>; this gateway embeds with <new>: push it again`
— until the project that owns it pushes it again (`pinecall knowledge push`, from each project);
in a call, a lookup on such a base is skipped and said in the call's log (`search_skipped`). A
contact's facts are not re-embedded by the switch: the dense branch of a recall filters on the
`model` column, so an older fact is recalled by its words (BM25) alone until
`pinecall-runtime memory reembed` writes its vector again from its text — once, on the hub, after
the deploy (`make ssh`, then the verb as the units see the instance), and a second run writes
nothing.

On a **hub** that line is the verdict and not advice: a hub answers the knowledge pushes, so an
embedder down there fails the deploy, naming what to type — `systemctl start pinecall-tei`, or
`make secret NAME=…`. On a laptop, and on the `all` a fresh `box.env` declares, it stays advice:
TEI has no arm64 image, so a Mac cannot run it and `EMBED_PROVIDER=perplexity` is how that
machine retrieves.

## Where the secrets live

Nowhere in the clear. Every secret on the box is a **systemd credential**: one file per name under
`/etc/credstore.encrypted/` — the box's — or under an instance's own
`/etc/pinecall/instances/<name>.credstore/`, encrypted under the machine's own key — sealed to its
TPM where it has one — and decrypted by systemd into a private directory for the one unit that
named it (`ImportCredential=` by name for the box's, `LoadCredentialEncrypted=` by path for an
instance's), readable by that process and by nothing down the tree.
The runtime reads that directory as it reads the environment (`_settings.py`, `secrets_dir`),
under the same names; the three containers that take a secret — livekit, sip and postgres — read
one credential, `media.env`, as their environment file. There is no `.env` on the box, and a stolen disk is not a stolen tenant.

| credential | who reads it | made by |
|---|---|---|
| `LIVEKIT_API_KEY` `LIVEKIT_API_SECRET` `POSTGRES_PASSWORD` `media.env` — the box's | the units and the containers, each what it names | `pinecall-secrets.service`, once: `pinecall-runtime box secrets` |
| `DATABASE_URL` `PINECALL_OPS_KEY` `PINECALL_VAULT_KEY` — an instance's, in its own store | that instance's units | production's: drawn with the box's by `box secrets` (its database is the container's), copied into its store by `make converge`, the originals left where they were. Any other's: `box secrets --instance <name>` (`make instance`) |
| `PINECALL_WORKER_KEY` — an instance's fleet key: its org default, the `fleet` scope | that instance's worker, and production's overflow agent | `pinecall-worker-key@<name>`, once, into the instance's store |
| `PINECALL_OPERATOR_KEY` — yours | you, once, with `systemd-creds decrypt` | `pinecall-operator-key.service` — it mints one only while the credstore has none, so `systemctl start` it to replace one that is gone |
| `pinecall-app-<name>.key` `pinecall-app-<name>.env` — an app held here: the org's key it knocks with, and its own secrets as dotenv lines | `pinecall-app@<name>` | that app's deploy, from its checkout |
| the vendors' keys | the gateway and the worker | you: `pinecall-runtime box secret <NAME>` |
| `TWILIO_ACCOUNT_SID` `TWILIO_API_KEY` `TWILIO_API_SECRET` — the box's own Twilio, for the numbers it buys for a tenant | the gateway | you, the same way; unset, `POST /v1/numbers/buy` says so |
| `PINECALL_SIGNUP` — whether a stranger may make an org here, off unless set; `PINECALL_CLOUD` — whether a plan is billed | the gateway | you: a line each in `/etc/pinecall/box.env` |

A box born before the `fleet` scope re-mints its worker key once (`docs/a-box-in-production.md`, "The worker's key"), or every other org's call dies with `NoRoute`.

`box secrets` run twice rotates nothing: a credential that is there is kept, and the two key units
carry a `ConditionPathExists=!` on the file they would make. Rotating one of THOSE is deleting its
file and restarting — `keys revoke` the old one, an UPDATE and never a DELETE, so the log entries
that name it stay readable. A vendor's key is replaced in place, from the checkout, on stdin:

```bash
printf '%s' "$ELEVENLABS_API_KEY" | make secret NAME=ELEVEN_API_KEY   # then: make restart
printf '%s' "$KEY" | make secret NAME=… INSTANCE=sandbox             # one instance's own store
make worker-secrets WORKER=deploy@<the worker>                         # a worker takes the hub's copy
```

## What the deploy does, and does not

`make install` installs a package the box is missing (the `PACKAGES` line of the manifest, the
same list cloud-init installed at birth — a test pins the two equal, so a box born before a
package was added converges on its next deploy), overwrites what changed and leaves what did
not; `systemd-sysusers` and `systemd-tmpfiles` make only what is missing; the fence and systemd
are reloaded. `make converge`, once the virtualenv is built, makes every listed instance whole or
stops the deploy ("An instance"), writes the Caddy sites and enables what the role runs. The
runtime's units are restarted by `make restart`, instance by instance in `PINECALL_INSTANCES`'
order: its database check and gateway first, then the health check through Caddy at that
instance's name, then its worker; the overflow agent and the loop last. The **containers are not**: the media plane stays up through a
deploy, and a changed `.container` takes effect on its next restart, which is yours to time —
`sudo systemctl restart pinecall-livekit` between two calls, not during one.

**An extension** — a package beside the runtime that plugs a policy into it, the way a box that
charges says its numbers (`docs/charging-for-it.md`) — travels with the deploy. Name its checkout in
`deploy.local.mk` (`EXTENSIONS_SRC = ../cloud`, space separated for more), and its module in
`PINECALL_EXTENSIONS` in `/etc/pinecall/box.env`, which both instances read: `sync` carries it to
`/opt/pinecall/extensions/<its directory>`, and `install` puts it into the venv with `--no-deps`
(what it depends on, `pinecall-core`, is already there: a member of the runtime's workspace)
**after** `uv sync --frozen`, which removes whatever the lock does not name — a package installed
once by hand is gone at the next deploy, and a gateway told to load it then refuses to start.
Nothing set, nothing of it runs.

The last word is the doctor's. `make doctor` runs `pinecall-runtime doctor` on the box exactly as
each instance's units run — their user, `box.env` and then the instance's file, the box's
credentials by name and the instance's own by path, in a transient unit systemd tears down on exit;
every listed instance in turn, or the one `INSTANCE=` names — and every provider key that is set is knocked at its own vendor's
cheapest door, once. A key that expired or was pasted wrong fails the deploy right there, with its
**name** on the screen and never its value, instead of failing a caller: the first voice call
through the second box, 2026-09-09, found an ElevenLabs key the hub had carried dead since its
`.env` days. A worker box is asked after what a worker has — the keys, the SFU — and never after the
hub's Postgres or its embedder, because a worker has neither. The embedder's line is a verdict on a
**hub** and advice anywhere else, where every call still runs: "The embedder", above.

## What a process may touch

Every long-running unit of the runtime — each instance's gateway and worker, the overflow agent,
the fleet loop — runs under `hardening.conf`, one drop-in the manifest installs beside each: no new
privileges, the whole file system read-only but what the unit's own `ReadWritePaths=` names (the
recordings, the cache LiveKit's plugins keep a model in, the fleet loop's cloud configuration),
its own `/tmp`, no home, no devices, no capability, the `@system-service` syscalls and the three
socket families a Python process on a box needs. The setgid bit stays allowed: the worker marks
each call's recording directory with it so egress's file belongs to `pinecall-media`, and with it
forbidden every recording failed (2026-09-26). `systemd-analyze security pinecall-gateway@production`
is the grade. The deploy compiles the bytecode at the sync, because a process that cannot write
its virtualenv would otherwise compile every module at every start. The oneshots that mint a key
catch it in `/run`, which a strict file system refuses, and stay outside; so does `pinecall-db@`,
which speaks to Postgres through podman as root; a tenant's app keeps a lighter set in its own
file, `/opt` writable. A unit that cannot start backs off — three seconds, doubling, to a minute —
and the journal is bounded (`journald.conf.d/pinecall.conf`), so a crash loop fills no disk.

What the box takes on trust is pinned: every container image by tag **and** digest — the index's,
so a Mac and the box pull the same one (`scripts/image-digests` says what a tag points at today,
and bumping one is a decision) — uv and `hcloud` by release and checksum, NodeSource's key by the
fingerprint it publishes, held to the same one at birth and on every deploy. Google's apt key
rotates and is not pinned. The deploy account's sudo is whole on purpose: it runs `sudo make -C`
over a manifest it writes, and a list of commands that includes that is `ALL` with extra steps.

## Five traps on a real box, one line each

- **The fence must let the containers ask the host for a name.** podman's own DNS, aardvark-dns,
  answers on the bridge's address (`10.89.0.1`), so a container resolving `pinecall-redis` sends a
  packet TO THE HOST and it arrives in `input`. Without the one line that accepts 53 from a
  `podman*` bridge, every container says "bad address", livekit sits up and silent with no redis,
  and the only symptom is a healthcheck that never turns green.
- **A credential's file is named exactly as the credential, with no extension.** systemd refuses
  `PINECALL_OPS_KEY.cred` for a credential named `PINECALL_OPS_KEY` — "embedded credential name
  does not match filename, refusing" — and `ImportCredential=` then finds nothing and says
  nothing: a missing credential is silently absent, by design, and it is the runtime that refuses
  to start without its `DATABASE_URL`. Measured 2026-09-09; the manual does not say it.
- **`uv` needs `--frozen` on a box.** Without it it re-resolves `uv.lock`, walks every path
  source in it, and stops at the one that is not there. The units never call uv at all — they run
  `/opt/pinecall/venv/bin/pinecall-runtime`, and the deploy's `uv sync` is the one place the
  environment is built.
- **Caddy's packaged unit reads no environment file**, so a `{$PINECALL_DOMAIN}` site address is
  empty and the block collapses into the *global options* one — `unrecognized global option:
  @livekit` — and a `{$VAR}` cannot be a site's second address even when set (`Expected another
  address but had '{'`, `caddy validate`, 2026-09-21). So a site is a file the manifest writes with
  the name in it, one per instance, each importing the one `(pinecall)` snippet with its port, and
  the Caddyfile reads no environment at all.
- **`StandardOutput=file:` is opened before `RuntimeDirectory=` is created**, so a unit that
  catches a key into a directory it also declares dies with `209/STDOUT` and "No such file or
  directory". The two key units write into `/run` itself and set `UMask=0077`, which is what makes
  that file `0600`.

## Where the keys come from

Three, and none of them opens another's door — nor another instance's: each instance has its own
ops and worker key, and the operator's is production's. The *keys* decision page in the maintainer's notebook argues the split;
`../../docs/protocol/operator-api.md` is the contract.

| key | who holds it | made by |
|---|---|---|
| `PINECALL_OPS_KEY` | an instance — its `/v1/ops/*` and nothing else. A person the box made an operator (`orgs operator`) opens the same doors with their own key | production's: `pinecall-secrets.service`, once; another instance's: `box secrets --instance` |
| `PINECALL_WORKER_KEY` | an instance's worker — `/v1/routes`, the app socket, the log, for EVERY org's calls | `pinecall-worker-key@<name>`, once, into the instance's store: `keys issue --org default --scope fleet --scope app --scope calls`, stdout straight into `systemd-creds encrypt` |
| `PINECALL_OPERATOR_KEY` | you — a key of production's org `default`, every scope but `fleet` | `pinecall-operator-key.service`, once: `keys issue --org default --label the-operator`, the same way |

`migrate up` mints nothing: it runs before every start of the gateway, and a verb that runs there
must print no secret into a journal. `keys issue` is the one place a key exists in the clear — on
stdout alone, with the two lines about it on stderr — which is exactly what lets a unit capture it
into a credential without a shell in between.

## Wire a number — the order, and it is ten minutes

What was run on 2026-09-08 to put **+1 417 674 3169** on `box.pinecall.io`, in the order it was
run; the *sip* decision page in the maintainer's notebook argues why each step is what it is. `twilio_trunk.py` takes `--dry-run`; `routes` has none.

```bash
export TWILIO_ACCOUNT_SID=… TWILIO_API_KEY=… TWILIO_API_SECRET=…   # from your own .env, never ours
export LIVEKIT_URL=https://box.pinecall.io                          # caddy proxies /twirp* to the SFU
export LIVEKIT_API_KEY=$(ssh <the box> sudo systemd-creds decrypt --name=LIVEKIT_API_KEY /etc/credstore.encrypted/LIVEKIT_API_KEY -)
export LIVEKIT_API_SECRET=$(ssh <the box> sudo systemd-creds decrypt --name=LIVEKIT_API_SECRET /etc/credstore.encrypted/LIVEKIT_API_SECRET -)

# 1. read the plan. It opens no socket and needs no credential; nothing below is sent until you
#    have read this and agree with every line of it.
uv run python ../tools/twilio_trunk.py \
    --number +1… --sip-host box.pinecall.io --trunk-name <the trunk> --adopt --dry-run

# 2. send it. --adopt repoints ONE origination URI and writes nothing else; drop --adopt on an
#    account that has no trunk yet and it builds one, once, and attaches the number.
uv run python ../tools/twilio_trunk.py \
    --number +1… --sip-host box.pinecall.io --trunk-name <the trunk> --adopt
#    → the carrier's trunk, then the SFU's inbound trunk and the one-room-per-caller rule.
#      Both LiveKit halves are looked for by name first, so a second run doubles neither.

# 3. who answers. A ROW, never a field on the tenant's class — the routes decision, in the maintainer's notebook.
export PINECALL_GATEWAY_URL=https://box.pinecall.io
export PINECALL_OPS_KEY=$(ssh <the box> sudo systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/pinecall/instances/production.credstore/PINECALL_OPS_KEY -)
unset PINECALL_WORKER_KEY                       # v1 exports one, and this gateway has never heard of it
uv run pinecall-runtime routes add +1… clinica-norte
uv run pinecall-runtime routes list
```

A **second** number on the same trunk is step 3 alone plus two additions: attach it to the trunk
in the carrier's console, and add it to the inbound trunk's `numbers` (`lk sip inbound update`, or
the console). `twilio_trunk.py` deliberately does neither: attaching a number is a decision, not a
re-run. A number the box **buys for a tenant** (`POST /v1/numbers/buy`, the three `TWILIO_*` above)
the gateway attaches to this trunk and admits on the tenant's own `pinecall-<org>` inbound trunk.

**Another carrier is another set**: edit `carrier_signalling` in `nftables.conf` and deploy; the
trunk tool reads the same set, so there is no second list to keep in step.

## The four fences on 5060, and why there are four

A public SIP port is found and probed within hours of opening, and a scanner that gets through
costs money: an admitted INVITE is a room, a job, a model and three provider bills.

| layer | what it does | where |
|---|---|---|
| the fence | 5060 ACCEPTED from the `carrier_signalling` set, DROPPED with a counter from anyone else | `nftables.conf` |
| `hide_inbound_port` | an INVITE for a number no inbound trunk declares is dropped with no reply at all | `sip.yaml` |
| the inbound trunk | `numbers` is an allow-list, and `allowed_addresses` carries the same networks again | `../tools/twilio_trunk.py` |
| the routes table | an unknown number resolves to nobody; it is never a default | `pinecall-runtime routes` |

The networks are written down **once**, as the `carrier_signalling` set in the fence itself.
`../tools/carrier_cidrs.py` reads that set for the trunk, so the fence and the carrier can never
disagree about who is allowed to ring. `nft list table inet pinecall` shows the drop rule's counter:
that is the fence, working. Media stays open, on purpose: RTP legitimately arrives from any of the
carrier's media addresses, and from any browser anywhere. Without an admitted INVITE nothing is
listening there for it. A cloud's own firewall in front of all this is fine and is not relied on:
the box is its own fence, so the same tree stands on any provider and on a machine in a cupboard.

## Working with the carrier, not against its fraud detection

A carrier's anti-fraud system reads the *pattern*, not the intent, and it cannot tell our
automation from an account takeover. So:

- **A trunk is created once.** `../tools/twilio_trunk.py` refuses to touch a trunk that
  already carries its friendly name — it says so and exits 0. There is no delete in it and no loop
  around a create, and there is no timer on this box that provisions anything, ever.
- **`--dry-run` first, and it opens no socket at all**: it prints every request the real run would
  make, needs no credential to do it, and can be read before the account has heard from us once.
- **Credentials live in the environment.** `TWILIO_ACCOUNT_SID` with either
  `TWILIO_API_KEY`/`TWILIO_API_SECRET` (preferred: revocable without touching the account) or
  `TWILIO_AUTH_TOKEN`. Never in a file in this repo, never in a `.md`, never pasted into a
  transcript.
- **A precautionary suspension is a thing that happens.** Identity check, rotate, restart the two
  units — the trunk and the number survive it. Knowing that in advance is the whole playbook.

## The toggles never to touch from a script

| toggle | why a human, deliberately, once |
|---|---|
| `transfer_mode` / `transfer_caller_id` on the trunk | they decide whether a SIP REFER is carried, and every transfer is billed twice — the child leg is Origination *plus* Termination. `twilio_trunk.py` reads them, reports them and prints the exact command; it never sends it |
| `krisp_enabled` on the inbound trunk | a LiveKit Cloud feature, untested on self-hosted media, and it cannot be turned off again with an update: the trunk has to be deleted and recreated, which changes its id and orphans the dispatch rule |
| the trunk and the number themselves | see above: created once, never recreated on a schedule |

## Verifying without a phone

```bash
uv run python ../tools/sip_probe.py \
    --host <the box's address> --domain box.example --dialled +598…
```

One INVITE — one, never a retry loop — then the ACK, and the probe asks the SFU whether a room
appeared. A room is the proof that the firewall, `hide_inbound_port`, the inbound trunk, the
dispatch rule and the fleet all did their part. It sends no RTP, so the **audio** is the one thing
only a real call can prove, and a carrier refuses a call from another number on the same account
(`21216`) — which is exactly why this probe exists.

It only gets through if this machine's own **public** address is admitted in **two** places for
the length of the run, and both were missing from this paragraph until a real number was wired:
the inbound trunk's `allowed_addresses`, and the `carrier_signalling` set of the fence. The probe
speaks UDP, so an ssh tunnel is not a way around either. Put the address in both, run the probe,
take it out again — a deploy restores the fence to exactly the eight networks in the file, so
taking it out is one command and not a memory. That inconvenience is the fence working.

`_the_address_that_reaches` (`../tools/sip_call.py`) reports the address on this machine's own interface, which behind
NAT is not the address the box sees: read the public one (`curl -s ifconfig.me`) and admit that.
