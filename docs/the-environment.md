# The environment

Every variable the gateway and the worker read. The operator's verbs are `pinecall-runtime --help`.

## Where a variable comes from

Three places, the later winning: the systemd credentials of the unit (one file per name under
`CREDENTIALS_DIRECTORY`), then `.env` in the directory the process started in, then in each parent
up to the repository root (`.env`, then `runtime/.env`, in the first directory that has either),
then the real environment. A bare `NAME=` means unset. A `.env` that exists and cannot be read
stops the process with its name; it is never skipped.

Our own knobs carry `PINECALL_`. The vendors' keys are not variables: see
[The vendors](#the-vendors).

**A runtime is one gateway and one database, serving both worlds.** Production and the sandbox
are a property of each row and of each key (`pc_live_`, `pc_test_`), never two instances. What
keeps a test call off a production process is the worker pool: each world has its own fleet of
workers, and the gateway dispatches a call to the fleet of its world. A variable marked *(the
unit's)* is written per worker unit, never in the box's `box.env`.

| | |
|---|---|
| `LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` | the media plane both processes talk to. The secret also signs call tokens |
| `LIVEKIT_PUBLIC_URL` | the URL a browser is told to join, when it differs |
| `DATABASE_URL` | Postgres 17 with pgvector and pg_textsearch: the one stateful service. One database for both worlds |
| `PINECALL_REDIS_URL` | the Redis the gateways tell each other what just happened on (`redis://:password@host:port/db`; on a box a sealed credential, the password drawn by `install.sh`, which LiveKit, SIP and egress use too): what a call just wrote, who holds what. Lossy by design and never a record: a reader that misses a message resumes from Postgres. Unset, the gateway is alone on its box and keeps all of it in its own memory, as it always has; set and not answering, the gateway starts and serves all the same, and says so in its log. On a box it is LiveKit's Redis, database 1 (`infra/box/pinecall-gateway.service`) |
| `PINECALL_DB_POOL` | the connections the gateway holds open to Postgres, 10 unless set: two are its log writer's own, open for the process's life, one per lane, so an append never waits for a door and a door never waits for an append; the doors share the rest. A box writes it into `box.env` at install, sized to the machine: two per vCPU, plus the writer's two. On each one a statement is cancelled at 30 s and a transaction left idle is ended at 60 s; a request that finds every connection taken for 2 s is answered `503`, and so is a statement cancelled at its timeout (a worker retries a `5xx`). What is long on purpose runs with neither timeout: the migrations, an erasure, an export, the nightly retention and a knowledge base's push |
| `PINECALL_WORKER_KEY` | the key the worker knocks with. On a box the fleet's; on a laptop an org's own key, and the worker serves that org |
| `PINECALL_RUNNER_KEY` · `PINECALL_RUNNER_ROOT` · `PINECALL_RUNNER_ENVIRONMENTS` · `PINECALL_RUNNER_IMAGE` · `PINECALL_RUNNER_RUNTIME` | the runner's: the key of its world (`keys runner`), where it unpacks each release (`/var/lib/pinecall/runner`), where it writes each container's environment — the org's secrets — a tmpfs the unit mounts (`/run/pinecall-runner`), the image every hosted app runs in (`docker.io/library/node:24-slim`), and the OCI runtime (`runsc`, gVisor; `crun` only for code you wrote yourself). It reads `PINECALL_GATEWAY_URL` for the box it serves: that world's public address, since it never runs on the box ([../infra/apps/README.md](../infra/apps/README.md)) |
| `PINECALL_OPS_KEY` | the box's own key to `/v1/ops/*`. Unset, only a person the box made an operator opens those doors |
| `PINECALL_VAULT_KEY` | **required by the gateway**: the Fernet key every sealed secret is under (an org's vendor keys, the box's, SMTP, SSO, the carrier). A gateway without it does not start. To rotate: a comma-separated list, the new key first; a secret seals under the first and opens under whichever sealed it |
| `PINECALL_ROLE` | what this box runs: `all` · `hub` · `worker`; the doctor asks after what it has |
| `PINECALL_GATEWAY_URL` | the gateway on loopback: what it binds, and what a worker's job asks; on a worker machine of the cell, the box's balancer at its address (`http://<box>:8088`) |
| `PINECALL_HERE` *(the box's Caddy's)* | the box's own address, written by `install.sh` into `box.env`: where the cell's worker machines reach the gateways' balancer (8088), which the fence opens to them alone. The runtime never reads it |
| `PINECALL_MAX_JOBS` *(the unit's)* · `PINECALL_APP` · `PINECALL_AGENT` | what a worker takes, and for whom: LiveKit offers it calls until every one of these is taken. Unset, load is gated on CPU, refused at 0.7 |
| `PINECALL_JOIN_URL` · `PINECALL_JOIN_TOKEN` *(a fleet machine's first boot)* | written by cloud-init to `/etc/pinecall/join.env` from what the fleet loop gave `create` (`infra/fleet/first-boot`): where to spend the join token and the token, one join, ten minutes. `pinecall-join.service` spends and shreds them; never in a unit's environment |
| `PINECALL_FLEET` *(the unit's)* | the fleet this worker unit joins: the name it registers under with LiveKit, `pinecall` unless set, spelled like a slug, never empty. Which fleet each world's calls go to is the `fleets` row of `box_settings` (`{"production": "pinecall", "sandbox": "pinecall-sandbox"}` unless the operator changes it), so a box runs one unit per world, or more per world to grow |
| `PINECALL_FLEET_SEATS` · `PINECALL_FLEET_MAX` · `PINECALL_FLEET_PROJECT` · `PINECALL_FLEET_ZONE` · `PINECALL_FLEET_MIG` *(the fleet loop's unit)* | on a box on Google Cloud, `/etc/pinecall/fleet-loop-<world>.env`, written by `install.sh` from the box's metadata (Terraform's): the seats of a fleet machine, the most machines, and the managed instance group the loop lets machines go from (`infra/fleet/gcp-mig.py`) |
| `PINECALL_IDLE_PROCESSES` *(the unit's)* | job processes the worker keeps warm. Unset, livekit's own: one per CPU. Set on the box only, where four workers share its cores; a machine of workers alone never takes it from the box |
| `PINECALL_WORKER_NAME` · `PINECALL_WORKER_HTTP_PORT` · `PINECALL_OVERFLOW_SAYS` | its name in the roster (unset: the hostname), its health port on loopback (8082), and the overflow agent's one sentence |
| `NOTIFY_SOCKET` *(systemd's)* | set by systemd on a `Type=notify` unit: the worker says `READY=1` there once LiveKit registered it and the gateway answered its heartbeat, and a `systemctl restart` returns then |
| `PINECALL_RECORDINGS` | where a kept recording lands. **Whether** audio is kept is the agent's own setting |
| `PINECALL_S3_ENDPOINT` · `PINECALL_S3_REGION` · `PINECALL_S3_ACCESS_KEY_ID` · `PINECALL_S3_SECRET_ACCESS_KEY` | the object store what leaves the disk goes to: any S3-compatible endpoint (AWS S3, Google Cloud Storage by HMAC key, R2, B2, MinIO), the region its signature names, and the key the box writes with. On a box the first three are in `/etc/pinecall/store.env` and the secret is a sealed credential, never in a file; examples per store in [a-box-in-production.md](a-box-in-production.md). A runtime given a recordings bucket and not all four does not start, and says which are missing |
| `PINECALL_RECORDINGS_BUCKET` | the bucket of that store a finished recording moves to, as `<org>/<call>/audio.ogg` (on a box, in `/etc/pinecall/store.env`). Unset, recordings stay on the disk as they always have |
| `PINECALL_BACKUP_BUCKET` *(the box's scripts')* | the bucket the nightly backup and the WAL archive go to, read by `infra/box/backup.sh` and `wal.sh`, never by the runtime |
| `PINECALL_SMTP_URL` · `PINECALL_MAIL_FROM` | the box's own mail (`smtp://user:pass@host:587`, or `smtps://…:465`) and who its letters are from. A mailbox stored at `PUT /v1/ops/mail` wins over these, and an org's own over both. A URL that does not read is said in the log at start, and the box posts nothing of its own |
| `PINECALL_DOMAIN` · `PINECALL_SANDBOX_DOMAIN` | the box's name per world: production's, where the public and a carrier reach it, and the sandbox's, a second name of the same box. The name a request comes in by is its world: the console served at `sandbox.example` is the sandbox's, `pinecall-env` may only agree, and a number imported in a world points its carrier at that world's name. A sign-in, a password or an invitation link, and the SSO callback, carry the name the request came in by; a Host that is none of the box's names gets production's. A box given one name serves both worlds at it, and the console there is production's. Neither set, nothing imports |
| `PINECALL_SIGNUP` · `PINECALL_SIGNUP_KEY` · `PINECALL_CLOUD` · `PINECALL_BILLING_URL` | whether a stranger may make an org here (off unless set), the Bearer key the sign-up doors take from the bot-shielded site in front of them, Pinecall's hosted gateway, and where its orgs pay. What a new org is allowed is not a variable: it is the `admission` row of `box_settings` (see [limits.md](limits.md)) |
| `PINECALL_APP_ORIGINS` · `PINECALL_MIN_PASSWORD` | origins besides the mobile app's two that may call `/v1` from a browser, comma separated and never `*`; how short a password may be (8; `0` is no rule) |
| `PINECALL_VOICE_LOOKUP_BUDGET_MS` · `PINECALL_TEXT_LOOKUP_BUDGET_MS` · `PINECALL_REMEMBER_BUDGET_S` | how long a turn waits for recall and search, and a hang-up for memory. Unset: the session's own |
| `PINECALL_TIMEZONE` | the IANA zone a call's `today` is read in (`Europe/Madrid`); `UTC` unless set. An unknown zone is refused at startup |
| `PINECALL_LOG_LEVEL` · `PINECALL_LOG_FORMAT` | `DEBUG` · `INFO` · `WARNING` · `ERROR`; and the gateway's lines, `text` for a terminal or `json` for a journal |
| `PINECALL_OTLP_ENDPOINT` · `PINECALL_OTLP_HEADERS` · `PINECALL_OTLP_PII` | where the worker sends a call's traces (OTLP over HTTP), the headers each export carries (a credential), and whether a span carries what was said. Unset, nothing is traced |

The box's own Twilio account (the one it buys numbers on) and its Meta app (the secret every WhatsApp webhook is signed with, the handshake's word, the token replies go out on when an org brought none) are not variables: they are the sealed rows `credentials/twilio` and `credentials/whatsapp` of `box_settings`, beside the box's vendor keys. See [numbers.md](protocol/numbers.md).

The source is `pinecall/process/settings.py`: one field per variable, with its alias and its
one-line description. A variable this table names and that file does not, or the reverse, is a bug
in whichever is younger.

## The vendors

Every livekit plugin installed that exports an LLM, an STT or a TTS is a vendor, under the name of
its module (`livekit.plugins.cartesia` is `cartesia`), and LiveKit Inference is `livekit`. The
runtime keeps no list of them: `pip install "pinecall[voice]"` installs all but four,
`pinecall[voice-big]` adds the four whose SDKs weigh hundreds of megabytes (aws, azure, google,
speechmatics), and a plugin livekit ships tomorrow is a vendor on the next install.
`GET /v1/providers` lists what this box has and, for the org asking, whose key runs each vendor:
*yours* (the org brought one), *offered* (the box holds one and lends it to this org), *bring
your own* (installed, and only the org can key it); and a plugin that does not import, with why.

An agent names a stage as `vendor/model`, a vendor alone (its default model), or a model alone
(on the vendor it runs). Only a vendor that is not installed, or does not do that stage, is
refused: a model or a voice the vendor does not have is the vendor's own error, in the call's
log.

**Keys.** An org's own key for a vendor runs any model of it. Otherwise the call runs on the
box's, where the org's `quotas.lends` lends it (`limits.md`); and a vendor nobody keyed is
refused before the call. A key is one secret, or the constructor's own arguments where a vendor
takes more (Azure's key and region, a Google service account, AWS's key pair): both are stored
encrypted, the org's as its provider keys, the box's as the box's own, written from the console.
Offering a vendor is holding its key: whoever runs the runtime loads the keys of the vendors it
offers, and any other installed vendor is the org's to bring.

**The bill.** livekit counts each stage's usage (tokens with their cache, characters, seconds
heard) whatever key it ran on; the memory model's tokens and each phone leg's minutes join it, and
`call.summary` carries the rows priced at the row's rates ([charging-for-it.md](charging-for-it.md)).
A model or a leg with no rate is listed unpriced, never at zero.

**The box's choices** are one row of the database, edited from the console's box screen: the
vendor and model each stage runs when an agent names none, the voice per language, what each
vendor is told for a stage (the class it builds where it is not `STT`/`TTS`/`LLM`, its keyword
arguments by the plugin's own names, whether its ears end the turn), the languages the ears
listen for, the price of each model, the judge, and the one embedder every knowledge base and
fact is written with (its URL, model, wire shape and width; its key is the box's
`credentials/<vendor>` row). The first box is seeded from the one before it; after that, nothing
of it is read from code.

## A laptop

A checkout and `uv sync` is the whole of it for writing the runtime: `make check` runs the rules and
every suite that needs no database; `make test` starts a Postgres of its own in colima (`make db`:
the image of `infra/postgres/`, the box's Postgres 17 with pgvector and pg_textsearch, on tmpfs,
durability off) and the box's Redis beside it (nothing kept), and gives every test a schema of its
own and a prefix of its own on the Redis. `make test-box` runs the same suites on
the box's database through an ssh tunnel, the DSN never printed.

The runtime whole runs on a laptop too, with no cloud account: `make local` starts the box's
Postgres, Redis and LiveKit in docker (`infra/local/compose.yaml`, ports 55433, 56380, 7880),
migrates the schema and writes `.local/env` once (a vault key, an ops key and the sandbox fleet's
key drawn there, 0600); `make local-gateway` and `make local-worker` run both from the checkout on
those settings, and `make local-down` stops the compose. LiveKit's pair there is a laptop-only
dev pair (`infra/local/livekit.yaml`). A phone needs the SIP bridge (`--profile phone`, Linux
only) and a carrier that reaches the laptop: [../infra/local/README.md](../infra/local/README.md).

## A box

Everything a box needs at birth is `infra/box/`: `cloud-init.yaml` for the first boot and
`install.sh` once, which draws the box's own secrets (the LiveKit pair, the database password,
`PINECALL_VAULT_KEY`, `PINECALL_OPS_KEY`) and seals them with `systemd-creds`. A unit reads its
secrets as credentials, by path, so a verb typed at a shell on the box reads a box that does not
exist; the operator's verbs run from a laptop against the gateway, with the ops key. The whole
walk, from a VM to the first call: [a-box-in-production.md](a-box-in-production.md); the box's files:
[../infra/box/README.md](../infra/box/README.md).
