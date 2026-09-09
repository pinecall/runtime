# infra — the development stack

Everything a laptop needs to carry a call end to end, in one compose file. Nothing here is
a product decision: it is the same five services a self-hosted box runs, pinned and small
enough to fit on a machine that is also running an editor.

```
docker compose -f infra/compose/dev.yml up -d          the five services
docker compose -f infra/compose/dev.yml ps             what is healthy
docker compose -f infra/compose/dev.yml logs -f sip    one service's log
docker compose -f infra/compose/dev.yml down           stop, keep the volumes
docker compose -f infra/compose/dev.yml down -v        stop and forget the data
```

The project name is `pinecall`, so the containers are `pinecall-livekit-1`,
`pinecall-postgres-1` and so on wherever you ran the command from.

Give Docker at least **4 CPUs and 8 GB**. Colima's default of 2 and 4 is not enough — the
embedder alone wants a couple of gigabytes to load bge-m3, and it dies by OOM rather than
by error message: `colima start --cpu 4 --memory 8`.

One tool belongs on the machine and not in the compose file: **the LiveKit CLI**, `brew
install livekit-cli` (Linux: `curl -sSL https://get.livekit.io/cli | bash`). It reads
livekit's current documentation from the terminal — `lk docs overview`, `lk docs search`,
2.15.0 or newer — and it is how a trunk and a dispatch are inspected by hand: `lk sip`,
`lk dispatch`. `pinecall-runtime doctor` reports whether it is there; nothing in this tree
ever runs it. See `docs/decisions/livekit-examples.md`.

## Three traps, one line each

None of them says what it means, and each has cost an hour already.

- **A native Postgres shadows the container.** Docker publishes 5432 on every interface, but a
  Postgres installed on the machine itself holds `127.0.0.1:5432` first — so the default
  `DATABASE_URL` reaches *it*, finds no `pgvector` and fails in a way that reads like a broken
  migration. The container still answers over IPv6: use
  `DATABASE_URL=postgresql://pinecall:pinecall@[::1]:5432/pinecall`, or stop the native server.
- **A stale checkout needs `uv sync` after the lockfile moves.** `cannot import name deepgram
  from livekit.plugins` and a wall of type errors mean the virtualenv is older than `uv.lock`:
  `scripts/bootstrap`.

## The services

| service | what it is | host ports |
|---|---|---|
| `livekit` | the SFU: room signalling and WebRTC media | 7880 http/ws · 7881 ice-tcp · 50000-50019/udp media |
| `sip` | the SIP bridge: a phone call becomes a participant | 5060 udp+tcp · 10000-10019/udp rtp |
| `redis` | the bus the two LiveKit services find each other over | **none, on purpose** |
| `postgres` | the only stateful service: the log, memory, hybrid search | 5432 |
| `tei` | text-embeddings-inference serving `BAAI/bge-m3`, 1024-d | 8081 |

**8080 is not in the table because it is the gateway's**: `pinecall-runtime gateway` serves it on
the host, and every CLI default, test and README points there. The embedder used to share it, and a
worker started with defaults asked TEI for `/v1/routes` and got a 404. The whole map, the
two host processes included, is in the header of `compose/dev.yml`.

Every service declares a healthcheck, so `up -d --wait` returns only when the stack is
actually usable and `ps` tells you the truth rather than "running". The first `up` is the
slow one: `tei` pulls ~2.3 GB of bge-m3 weights before it answers anything, which is why
its start period is fifteen minutes. Afterwards the weights live in a volume and it is up
in seconds.

`postgres` is built, not pulled: `compose/postgres/Dockerfile` compiles **pgvector 0.8.6**
and **pg_textsearch 1.4.0** against `postgres:17.11-trixie`, and `compose/postgres/init.sql`
creates both extensions the first time the cluster comes up. pgvector is the embedding half
of retrieval and pg_textsearch is the BM25 half; one image carries both so a query never has
to leave Postgres to be scored two ways.

## The two profiles

Default — no flag — is the five services above. The embedder runs on CPU because a laptop
has no CUDA and bge-m3 is small enough that it does not need one.

`--profile gpu` adds two more, for a box that has a GPU:

| service | what it is | host port |
|---|---|---|
| `reranker` | TEI on the CUDA image, serving `BAAI/bge-reranker-v2-m3` | 8082 |
| `vllm` | an OpenAI-shaped local model, for turns that must not leave the building | 8000 |

```
docker compose -f infra/compose/dev.yml --profile gpu up -d
docker compose -f infra/compose/dev.yml --profile llm up -d vllm    # vLLM alone
```

Both reserve an nvidia device, so they will not start on a machine without one — that is
why they are behind a profile and not behind a comment.

## Three settings that are not decoration

Each of these was paid for once already. They look arbitrary; they are not.

**`rtc.node_ip: 127.0.0.1` in `compose/livekit.yaml`.** The worker and the browser both run
on the host, outside the container. Left to itself the SFU advertises the address it sees on
its own interface, which nothing on the host can route to. Every ICE check then fails, and it
fails *silently*: the room joins, the participant appears, and no audio ever arrives.
`127.0.0.1` is the address the published UDP range actually answers on. It travels with
`use_external_ip: false`, because when that is on the node IP is ignored.

**Redis publishes no host port.** It authenticates nobody. A Redis listening on a laptop's
6379 is one of the most scanned ports on the internet and, through LiveKit's message bus, it
is a way into room control. The two services that need it are on the compose network with it;
nothing else has any business there.

**`compose/sip.yaml` is mounted at exactly `/sip/config.yaml`.** The image's entrypoint is
`livekit-sip --config=/sip/config.yaml`, hardcoded. Mount the file anywhere else and the
service starts happily on its defaults — no config, no error, no log line — and the first
call is the thing that tells you.

## Secrets

None are committed. `compose/livekit.yaml` and `compose/sip.yaml` carry no key pair: the
services read `LIVEKIT_KEYS` and `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` from the
environment, and `dev.yml` supplies obvious local defaults so an untouched checkout still
comes up. Write your own `compose/.env` — gitignored, and docker compose reads it
automatically beside `dev.yml` — the moment anything outside the machine can reach port
7880: that secret is what mints room tokens. There is no committed example of it, because
the only two names it ever holds are the pair above and `TEI_IMAGE` below.

## Apple Silicon

HuggingFace publishes no versioned arm64 CPU tag for TEI, only a rolling one, so `dev.yml`
defaults to the amd64 build. On an M-series Mac, put `TEI_IMAGE=` in `compose/.env` with the
arm64 tag; without it Docker falls back to emulation and a single embedding takes minutes.
Every other image in the stack is multi-arch.

---

# The box — the same stack, on a machine a stranger can telephone

`infra/box/` stands up one VM that answers the PSTN: the media plane in compose, the two runtime
processes under systemd, Caddy in front of them, and a firewall whose whole job is one port.
Everything in it is idempotent, and every script takes `--dry-run`, which prints what it would do
and does none of it.

## The path a call takes

```
  ☎  a mobile
  │  PSTN
  ▼
  a Twilio Elastic SIP Trunk (today: "convo-platform", TK150b9c…, adopted rather than rebuilt)
  │   the number is ATTACHED to the trunk, so the number's own voice_url is ignored
  │   Origination URI  sip:<the box>:5060;transport=udp   — the port is named, see below
  ▼  INVITE, UDP 5060, from the carrier's signalling networks and from nowhere else
  the box
  ├─ firewall            5060 allowed from carrier-signalling-cidrs.txt, DENIED from 0.0.0.0/0
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

These are the steps that were actually run against `box.pinecall.io` on 2026-09-08, in this order.
Every script here takes `--dry-run`, which prints what it would do and does none of it; read that
first, every time.

```bash
# 0. ssh. BOX is an ssh destination, and the default is the alias `pinecall-v2-box`. Give it a
#    block in ~/.ssh/config — HostName, User, IdentityFile, IdentitiesOnly — so scp, rsync and
#    shipway all reach the same machine the same way.

DOMAIN=box.pinecall.io PROJECT=<gcp project> ./infra/box/setup.sh --dry-run
DOMAIN=box.pinecall.io PROJECT=<gcp project> ./infra/box/setup.sh
#    docker, uv, the service user, the two directories, the secrets, the media plane, Caddy,
#    the firewall, and both units installed and ENABLED — started by nothing, because there is
#    no code under them yet.

#    the provider keys go into /etc/pinecall/pinecall.env ON THE BOX, by hand, and nowhere else.

shipway deploy                          # from the root of the checkout: rsync, sync, restart
./infra/box/first_run.sh                # once per box: the schema, the org's key, the fleet's key

cd runtime && uv run python ../infra/scripts/twilio_trunk.py \
    --number +598… --sip-host box.pinecall.io --dry-run   # the carrier side, planned
```

`BOX` (the ssh destination), `NETWORK`, `TAG` and `PROJECT` are environment overrides with
sensible defaults; `DOMAIN` has none, because Caddy cannot guess the name it gets a certificate
for. `PROJECT` is worth passing every time: these rules face the internet, and gcloud's active
project is whatever the operator last worked on.

The secrets — the LiveKit pair, the database password, the ops key — are generated **on the box**
on the first run and never leave it, and no script ever rewrites that file.

**The box holds no credential for the repository.** It cannot clone and it cannot fetch; the code
is pushed to it by a person at a checkout, with `shipway deploy` (`shipway.yml` at the root). That
deploy carries no build step at all: the gateway is an API and serves no page, so the two
directories it rsyncs — this repository and the wire beside it — are Python and nothing else.
`docs/decisions/box.md` argues both.

## Three traps on a real box, one line each

Every one of these cost time on the first box, and none of them says what it means.

- **`[ -f /etc/pinecall/pinecall.env ]` lies to the account running the script.** The directory is
  `0750` and owned by the service user, so the login account is *refused* the file rather than told
  it is there, and the test reports "no such file" on a box that has one — which made `setup.sh`
  rewrite the whole environment on every re-run, rotating the database password out from under a
  Postgres volume that remembers the one it was initialised with. What the operator sees is
  `InvalidPasswordError: password authentication failed for user "pinecall"` from the gateway. Every
  read of that file from a script goes through `sudo`.
- **`uv` needs `--frozen` on a box.** Without it it re-resolves `runtime/uv.lock`, which walks every
  path source in it — `evals` among them, an editable dependency of an extra a box never asks for —
  and stops at `Distribution not found at: /opt/pinecall/app/evals`.
- **Caddy's packaged unit reads no environment file at all.** `{$PINECALL_DOMAIN}` in the Caddyfile
  is then empty, the site block collapses to a bare `{ … }`, and Caddy reads it as the *global
  options* block: `unrecognized global option: @livekit`, which names everything except the domain
  that is missing. `infra/box/caddy-domain.conf` is the drop-in that fixes it.

## Where the keys come from

Three, and none of them opens another's door. `docs/decisions/keys.md` argues the split;
`docs/protocol/operator-api.md` is the contract.

| key | who holds it | made by |
|---|---|---|
| `PINECALL_OPS_KEY` | the box — `/v1/ops/*` and nothing else | `setup.sh`, once, on the box |
| `PINECALL_API_KEY` | the worker unit — `/v1/routes`, the app socket, the log | `first_run.sh`, once: `pinecall-runtime keys issue --org default`, appended to `/etc/pinecall/pinecall.env` |
| `PINECALL_DEV_KEY` | a laptop, never a box | set by hand, in development |

`first_run.sh` runs `pinecall-runtime migrate up`, and on a **fresh** database that also creates the
`default` org and issues its key — the only moment any key exists in the clear. You do not have to
catch it, and you are not shown it: the script writes it on the box, to `~/.pinecall/box-org-key`
under the login account's home at `0600`, and never onto the terminal it was run from. A terminal is
a laptop, a CI log and a transcript at once. The same run issues the worker its own key into the
environment file, and refuses to rewrite one that is already there, so a second run mints nothing.

**Never put `PINECALL_DEV_KEY` in a box's environment.** It is not a weaker API key, it is a
different mode: with it set the gateway honours that key alone and opens **no Postgres pool at
all**, so the routes table and the api_keys table both stop existing as far as it is concerned.

A key is printed once and stored as its sha256; there is no verb that reads one back. Lost one?
`pinecall-runtime keys issue`, then `pinecall-runtime keys revoke <fingerprint>` on the old — which
is an UPDATE, never a DELETE, so the log entries that name it stay readable.

## Wire a number — the order, and it is ten minutes

This is what was actually run on 2026-09-08 to put **+1 417 674 3169** on `box.pinecall.io`, in
the sequence it was run. `docs/decisions/sip.md` argues why each step is what it is; this is the
order. Every script here takes `--dry-run` first, and the first four steps are one command.

```bash
cd runtime
export TWILIO_ACCOUNT_SID=… TWILIO_API_KEY=… TWILIO_API_SECRET=…   # from your own .env, never ours
export LIVEKIT_URL=https://box.pinecall.io                          # caddy proxies /twirp* to the SFU
export LIVEKIT_API_KEY=$(ssh pinecall-v2-box "sudo grep '^LIVEKIT_API_KEY=' /etc/pinecall/pinecall.env | cut -d= -f2-")
export LIVEKIT_API_SECRET=$(ssh pinecall-v2-box "sudo grep '^LIVEKIT_API_SECRET=' /etc/pinecall/pinecall.env | cut -d= -f2-")

# 1. read the plan. It opens no socket and needs no credential; nothing below is sent until you
#    have read this and agree with every line of it.
uv run python ../infra/scripts/twilio_trunk.py \
    --number +1… --sip-host box.pinecall.io --trunk-name <the trunk> --adopt --dry-run

# 2. send it. --adopt repoints ONE origination URI and writes nothing else; drop --adopt on an
#    account that has no trunk yet and it builds one, once, and attaches the number.
uv run python ../infra/scripts/twilio_trunk.py \
    --number +1… --sip-host box.pinecall.io --trunk-name <the trunk> --adopt
#    → the carrier's trunk, then the SFU's inbound trunk and the one-room-per-caller rule.
#      Both LiveKit halves are looked for by name first, so a second run doubles neither.

# 3. who answers. A ROW, never a field on the tenant's class — docs/decisions/routes.md.
export PINECALL_GATEWAY_URL=https://box.pinecall.io
export PINECALL_OPS_KEY=$(ssh pinecall-v2-box "sudo grep '^PINECALL_OPS_KEY=' /etc/pinecall/pinecall.env | cut -d= -f2-")
unset PINECALL_API_KEY                       # v1 exports one, and this gateway has never heard of it
uv run pinecall-runtime routes add +1… clinica-norte
uv run pinecall-runtime routes list
```

A **second** number on the same trunk is steps 3 alone plus two additions: attach it to the trunk
in Twilio's console, and add it to the inbound trunk's `numbers` (`lk sip inbound update`, or the
console). `twilio_trunk.py` deliberately does neither — it says the standing trunk is already
there and stops, because attaching a number to a trunk is a decision and not a re-run.

**If the CIDR file moved, the firewall has to be told**, and only then:

```bash
PROJECT=<gcp project> ./infra/box/firewall.sh --dry-run
PROJECT=<gcp project> ./infra/box/firewall.sh
```

## The four fences on 5060, and why there are four

A public SIP port is found and probed within hours of opening, and a scanner that gets through
costs money: an admitted INVITE is a room, a job, a model and three provider bills.

| layer | what it does | where |
|---|---|---|
| the cloud firewall | 5060 ALLOWED from the carrier's signalling networks at priority 1000, DENIED from `0.0.0.0/0` at 1100 | `infra/box/firewall.sh` |
| `hide_inbound_port` | an INVITE for a number no inbound trunk declares is dropped with no reply at all | `infra/box/sip.yaml` |
| the inbound trunk | `numbers` is an allow-list, and `allowed_addresses` carries the same networks again | `infra/scripts/twilio_trunk.py` |
| the routes table | an unknown number resolves to nobody; it is never a default | `pinecall-runtime routes` |

The networks are written down **once**, in `infra/box/carrier-signalling-cidrs.txt`.
`infra/scripts/carrier_cidrs.py` is its only reader: the firewall calls that script and the trunk
imports it, so the fence and the carrier can never disagree about who is allowed to ring.

Media stays open, on purpose: RTP legitimately arrives from any of the carrier's media addresses,
and from any browser anywhere. Without an admitted INVITE nothing is listening there for it.

## Working with the carrier, not against its fraud detection

A carrier's anti-fraud system reads the *pattern*, not the intent, and it cannot tell our
automation from an account takeover. So:

- **A trunk is created once.** `infra/scripts/twilio_trunk.py` refuses to touch a trunk that
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
cd runtime && uv run python ../infra/scripts/sip_probe.py \
    --host <the box's address> --domain box.example --dialled +598…
```

One INVITE — one, never a retry loop — then the ACK, and the probe asks the SFU whether a room
appeared. A room is the proof that the firewall, `hide_inbound_port`, the inbound trunk, the
dispatch rule and the fleet all did their part. It sends no RTP, so the **audio** is the one thing
only a real call can prove, and a carrier refuses a call from another number on the same account
(`21216`) — which is exactly why this probe exists.

It only gets through if this machine's own **public** address is admitted in **two** places for
the length of the run, and both were missing from this paragraph until a real number was wired:
the inbound trunk's `allowed_addresses`, and `pinecall-sip-signalling`'s `--source-ranges` on
GCP. The probe speaks UDP, so an ssh tunnel is not a way around either. Put the address in both,
run the probe, take it out again — `./infra/box/firewall.sh` restores the cloud rule to exactly
the eight networks in the file, which is why taking it out is one command and not a memory. That
inconvenience is the fence working.

`_the_address_that_reaches` reports the address on this machine's own interface, which behind
NAT is not the address the box sees: read the public one (`curl -s ifconfig.me`) and admit that.
