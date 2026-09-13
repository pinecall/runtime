# The box

The same five services as the dev stack (`../README.md`), on a machine a stranger can telephone,
declared rather than scripted. Four are on every box; the fifth, the embedder, is a choice ("The embedder").

This directory is a box **declared**: every file in it is one thing systemd, podman, Caddy or
nftables reads, and there is no script. A fresh machine on any provider — a cloud that takes
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
├── containers/                the media plane as Quadlet units: redis · livekit · sip · postgres,
│                              and tei where the box embeds here rather than at a vendor
├── livekit.yaml · sip.yaml    what the two LiveKit containers mount
├── pinecall-secrets.service   the box's own secrets, drawn once, encrypted by systemd
├── pinecall-postgres-image.service   our Postgres image, built once per tag
├── pinecall-gateway.service · pinecall-worker.service        the two processes we write
├── pinecall-worker-key.service · pinecall-operator-key.service   two keys, minted once each
└── caddy/                     the Caddyfile, and the drop-in that hands Caddy its domain
```

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
#    Hand your provider cloud-init.yaml as the instance's user-data, with the three
#    YOURS lines filled: your ssh public key, the domain, the SFU's public URL. It installs
#    podman, caddy, nftables and make, creates the account the deploy logs in as, makes
#    /opt/pinecall/app for it, installs uv, and raises the fence.
#    A machine without cloud-init: do those four things by hand, they are the whole file.

# 2. The deploy. From this checkout, as the account cloud-init made. Which box is yours and
#    not the repository's: BOX and DOMAIN in deploy.local.mk beside the root Makefile, git-ignored.
make deploy
#    rsync puts this repository and the wire beside it under /opt/pinecall/app; `make install`
#    puts every file of infra/box/ where systemd reads it; `uv sync` builds the virtualenv as the
#    service user. Then systemd, on its own, in this order: the secrets are drawn
#    (pinecall-secrets), the Postgres image is built (pinecall-postgres-image), the media plane
#    comes up (redis, livekit, sip, postgres), the gateway migrates the schema and opens, the two
#    keys are minted (pinecall-worker-key, pinecall-operator-key), the worker registers.

# 3. Your key. Minted on the box on that first start, encrypted, printed nowhere. Read it once:
ssh <the box> sudo systemd-creds decrypt --name=PINECALL_OPERATOR_KEY /etc/credstore.encrypted/PINECALL_OPERATOR_KEY -
pinecall login https://<the domain>     # on the laptop, and the key is kept in ~/.pinecall/credentials
```

And the vendors' keys, which the box cannot draw for itself — from stdin, kept encrypted under
their names; then a restart, because a unit reads its credentials at start:

```bash
printf '%s' 'sk-ant-…' | sudo /opt/pinecall/venv/bin/pinecall-runtime box secret ANTHROPIC_API_KEY
sudo systemctl restart pinecall-gateway pinecall-worker
```

The names are the environment's own and each unit lists which it may see (the embedder's
`PERPLEXITY_API_KEY` or `OPENROUTER_API_KEY` is the gateway's alone). The box installs a plugin for
**every vendor LiveKit ships one for** — forty-five — each reading its key under its own variable,
all named in both units; one not in the credstore is simply absent, so the other forty cost nothing
until `make secret NAME=CARTESIA_API_KEY` puts one there. The whole table, with what each still
wants, is `make providers` from the checkout.

**The box holds no credential for the repository.** It cannot clone and it cannot fetch; the code
is pushed to it by a person at a checkout, with `make deploy` — rsync, ssh, make and curl, and no
tool that does not come with a Unix. Its one build step runs on the laptop: `scripts/console` bundles
the console into `src/pinecall/gateway/console/`, the rsync carries it, the gateway serves it at `/`.
The rest is Python. `../../docs/decisions/box.md` argues both.

## Roles, and a second box

One machine runs everything (`PINECALL_ROLE=all`, the default). The day the worker needs a
machine of its own, the same directory stands up two: the **hub** — control plane and media
plane, no worker — and a **worker**, which dials the hub by name and holds nothing but calls.
The role is one line of `/etc/pinecall/box.env`, and the Makefile enables each role's units and
disables the others', so a box that changes role changes it on its next deploy. The embedder is
decided in the same file by the same rule — "The embedder", below.

A worker box needs three things the hub does not put in its unit: where the SFU and the gateway
are, and how many calls it holds.

```
PINECALL_ROLE=worker
LIVEKIT_URL=wss://box.example.com          # the hub, under TLS: Caddy carries /agent to the SFU
PINECALL_GATEWAY_URL=https://box.example.com
PINECALL_MAX_JOBS=5                        # measured on THIS machine type — see below
```

And its own credentials, and no others: the LiveKit keypair and the vendors' keys copied from
the hub (`box secret`, from stdin, over ssh), and a `PINECALL_WORKER_KEY` issued there with
`keys issue`. Never `DATABASE_URL`, never the ops key, never the vault key, and never an
embedder's: a worker has no database, guards nothing, and embeds nothing.

It needs no port open but ssh. It registers by an outbound WebSocket, LiveKit hands it jobs on
that socket, and the media goes to the hub's public UDP port. `nftables.conf` is the same file
on every role; the doors it opens that nothing listens on are doors to nothing.

## The fleet

The loop that stands workers up for you, the image it clones, the cordon that drains one, and the
overflow agent that answers the phone when every worker is full: [../../docs/scaling.md](../../docs/scaling.md).

## Slots

A worker with `PINECALL_MAX_JOBS` reports its load to LiveKit as **calls held over calls it
may hold**, and LiveKit stops routing to it at 0.7 of them — the same line it holds a CPU
average to. Without it, the worker reports the machine's CPU average, which is right for a box
it shares with the SFU and wrong for one it has to itself.

`MAX_JOBS` is measured, never guessed: on the machine type it will run on, calls with real audio
in a loop, five more each step, until the p95 of first audio crosses 1.8 s. That concurrency
is the ceiling, and `MAX_JOBS` is **one under it**: LiveKit re-reads the load every half second,
and two jobs that arrive inside that window both see the old count (livekit/agents#4884). The
tolerance is one call, never more.

A fleet is summed in slots: `free = Σ(max − active)` over the workers that are up. That is the
number a person watches and the number a loop scales on — never a CPU.

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
stops the container and takes the file away where it did not, so a changed line takes effect on
the next deploy. **A worker never runs it and needs none**: it holds calls, and every lookup in
them is run by the gateway on the hub. The container publishes on `127.0.0.1:8081` and nowhere
else, exactly as Postgres does — the gateway is a process on the host and reaches it over
loopback — so the fence has no line about the embedder and nothing outside can ask it anything.

**The first start is minutes**: a cold box fetches those 2.3 GB before the port answers at all,
which is why the unit's health start period is fifteen. Nothing waits for it — the gateway reaches
the embedder lazily — so the box answers the telephone throughout, and what a call loses meanwhile
is a search, written into the log as `search_skipped`, while a knowledge push answers
503 and says so. Afterwards the weights live in the `pinecall-tei` volume and a restart is
seconds; they are **not** removed with the unit, so a box that will not come back frees them by
hand: `podman volume rm pinecall-tei`.

Switching to a vendor, from the checkout — the key first, so the box is never configured for a
door it cannot open — and back again by emptying the same line:

```bash
printf '%s' 'pplx-…' | make secret NAME=PERPLEXITY_API_KEY
make ssh                     # sudoedit /etc/pinecall/box.env → EMBED_PROVIDER=perplexity
make deploy                  # which ends with the one command that proves it, `make doctor`:
#  ✓ embedder  perplexity · pplx-embed-context-v1-0.6b — https://api.perplexity.ai/v1 — HTTP 200
#  ✓ embedder  tei · BAAI/bge-m3 — http://127.0.0.1:8081/info — HTTP 200
```

On a **hub** that line is the verdict and not advice: a hub answers the knowledge pushes, so an
embedder down there fails the deploy, naming what to type — `systemctl start pinecall-tei`, or
`make secret NAME=…`. On a laptop, and on the `all` a fresh `box.env` declares, it stays advice:
TEI has no arm64 image, so a Mac cannot run it and `EMBED_PROVIDER=perplexity` is how that
machine retrieves.

## Where the secrets live

Nowhere in the clear. Every secret on the box is a **systemd credential**: one file per name under
`/etc/credstore.encrypted/`, encrypted under the machine's own key — sealed to its TPM where it
has one — and decrypted by systemd into a private directory for the one unit that named it
(`ImportCredential=` in each `.service`), readable by that process and by nothing down the tree.
The runtime reads that directory as it reads the environment (`_settings.py`, `secrets_dir`),
under the same names; the three LiveKit containers read one credential, `media.env`, as their
environment file. There is no `.env` on the box, and a stolen disk is not a stolen tenant.

| credential | who reads it | made by |
|---|---|---|
| `LIVEKIT_API_KEY` `LIVEKIT_API_SECRET` `POSTGRES_PASSWORD` `DATABASE_URL` `PINECALL_OPS_KEY` `PINECALL_VAULT_KEY` `media.env` | the units and the containers, each what it names | `pinecall-secrets.service`, once: `pinecall-runtime box secrets` |
| `PINECALL_WORKER_KEY` — the org's key the worker knocks with | the worker | `pinecall-worker-key.service`, once |
| `PINECALL_OPERATOR_KEY` — yours | you, once, with `systemd-creds decrypt` | `pinecall-operator-key.service`, once |
| the vendors' keys | the gateway and the worker | you: `pinecall-runtime box secret <NAME>` |
| `TWILIO_ACCOUNT_SID` `TWILIO_API_KEY` `TWILIO_API_SECRET` — the box's own Twilio, for the numbers it buys for a tenant | the gateway | you, the same way; unset, `POST /v1/numbers/buy` says so |
| `PINECALL_SIGNUP` — whether a stranger may make an org here, off unless set; `PINECALL_CLOUD` — whether a plan is billed | the gateway | you: a line each in `/etc/pinecall/box.env` |

`box secrets` run twice rotates nothing: a credential that is there is kept, and the two key units
carry a `ConditionPathExists=!` on the file they would make. Rotating one of THOSE is deleting its
file and restarting — `keys revoke` the old one, an UPDATE and never a DELETE, so the log entries
that name it stay readable. A vendor's key is replaced in place, from the checkout, on stdin:

```bash
printf '%s' "$ELEVENLABS_API_KEY" | make secret NAME=ELEVEN_API_KEY   # then: make restart
make worker-secrets WORKER=deploy@<the worker>                         # a worker takes the hub's copy
``` **Never put `PINECALL_DEV_KEY` on a box**: it is not a weaker key, it
is a mode in which the gateway opens no Postgres pool at all.

## What the deploy does, and does not

`make install` installs a package the box is missing (the `PACKAGES` line of the manifest, the
same list cloud-init installed at birth — a test pins the two equal, so a box born before a
package was added converges on its next deploy), overwrites what changed and leaves what did
not; `systemd-sysusers` and `systemd-tmpfiles` make only what is missing; the fence and systemd
are reloaded. The runtime's units are restarted by `make restart`, the gateway first (the overflow
agent with it) and the worker once the gateway answers. The **containers are not**: the media plane stays up through a
deploy, and a changed `.container` takes effect on its next restart, which is yours to time —
`sudo systemctl restart pinecall-livekit` between two calls, not during one.

The last word is the doctor's. `make doctor` runs `pinecall-runtime doctor` on the box exactly as
the units run — their user, their `box.env`, every credential in the credstore, in a transient
unit systemd tears down on exit — and every provider key that is set is knocked at its own
vendor's cheapest door, once. A key that expired or was pasted wrong fails the deploy right
there, with its **name** on the screen and never its value, instead of failing a caller: the
first voice call through the second box, 2026-09-09, found an ElevenLabs key the hub had carried
dead since its `.env` days. A worker box is asked after what a worker has — the keys, the SFU —
and never after the hub's Postgres or its embedder, because a worker has neither. On a **hub**
the embedder's line is the verdict, since a hub is what answers a knowledge push; anywhere else
it is advice, and every call still runs. "The embedder", above.

## Four traps on a real box, one line each

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
- **Caddy's packaged unit reads no environment file.** `{$PINECALL_DOMAIN}` is then empty, the
  site block collapses to a bare `{ … }`, and Caddy reads it as the *global options* block:
  `unrecognized global option: @livekit`. `caddy/pinecall.conf` is the drop-in that points it at
  `/etc/pinecall/box.env`.
- **`StandardOutput=file:` is opened before `RuntimeDirectory=` is created**, so a unit that
  catches a key into a directory it also declares dies with `209/STDOUT` and "No such file or
  directory". The two key units write into `/run` itself and set `UMask=0077`, which is what makes
  that file `0600`.

## Where the keys come from

Three, and none of them opens another's door. `../../docs/decisions/keys.md` argues the split;
`../../docs/protocol/operator-api.md` is the contract.

| key | who holds it | made by |
|---|---|---|
| `PINECALL_OPS_KEY` | the box — `/v1/ops/*` and nothing else | `pinecall-secrets.service`, once |
| `PINECALL_WORKER_KEY` | the worker unit — `/v1/routes`, the app socket, the log | `pinecall-worker-key.service`, once: `keys issue --org default`, stdout straight into `systemd-creds encrypt` |
| `PINECALL_DEV_KEY` | a laptop, never a box | set by hand, in development |

`migrate up` mints nothing: it runs before every start of the gateway, and a verb that runs there
must print no secret into a journal. `keys issue` is the one place a key exists in the clear — on
stdout alone, with the two lines about it on stderr — which is exactly what lets a unit capture it
into a credential without a shell in between.

## Wire a number — the order, and it is ten minutes

What was run on 2026-09-08 to put **+1 417 674 3169** on `box.pinecall.io`, in the order it was
run; `../../docs/decisions/sip.md` argues why each step is what it is. Every tool takes `--dry-run`.

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

# 3. who answers. A ROW, never a field on the tenant's class — ../../docs/decisions/routes.md.
export PINECALL_GATEWAY_URL=https://box.pinecall.io
export PINECALL_OPS_KEY=$(ssh <the box> sudo systemd-creds decrypt --name=PINECALL_OPS_KEY /etc/credstore.encrypted/PINECALL_OPS_KEY -)
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
`../tools/carrier_cidrs.py` reads that set for the trunk, so the fence and the carrier can
never disagree about who is allowed to ring. `nft list table inet pinecall` shows the drop rule's
counter: that is the fence, working.

Media stays open, on purpose: RTP legitimately arrives from any of the carrier's media addresses,
and from any browser anywhere. Without an admitted INVITE nothing is listening there for it.

A cloud's own firewall in front of all this is fine and is not relied on: the box is its own fence,
so the same tree stands on any provider and on a machine in a cupboard.

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

`_the_address_that_reaches` reports the address on this machine's own interface, which behind
NAT is not the address the box sees: read the public one (`curl -s ifconfig.me`) and admit that.
