# The environment

Every variable the gateway and the worker read. The operator's verbs are `pinecall-runtime --help`.

## Where a variable comes from

Three places, the later winning: a directory of credential files, one file per name, when
`CREDENTIALS_DIRECTORY` names one (systemd's convention, which neither the laptop nor the chart
uses), then `.env` in the directory the process started in, then in each parent
up to the repository root (`.env`, then `runtime/.env`, in the first directory that has either),
then the real environment. A bare `NAME=` means unset. A `.env` that exists and cannot be read
stops the process with its name; it is never skipped.

Our own knobs carry `PINECALL_`. The vendors' keys are not variables: see
[The vendors](#the-vendors).

**A runtime is one gateway and one database, serving both environments.** Production and the sandbox
are a property of each row and of each key (`pc_live_`, `pc_test_`), never two instances. What
keeps a test call off a production machine is the media plane and the worker pool: each environment
has its own LiveKit and its own fleet of workers, and the gateway dispatches a call to the fleet
of its environment, on its environment's LiveKit. A variable marked *(the
pod's)* is set per worker Deployment by the chart (`infra/charts/pinecall`), never for the
gateway.

| | |
|---|---|
| `LIVEKIT_URL` · `LIVEKIT_SANDBOX_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` | the media plane. A worker is given its environment's LiveKit in `LIVEKIT_URL` *(the pod's)*; the gateway, production's in `LIVEKIT_URL` and the sandbox's own in `LIVEKIT_SANDBOX_URL` (unset, the sandbox shares production's), and it asks each environment's LiveKit of that environment's rooms, trunks and rules. Both take the one key pair, so a token the gateway signs opens a room on either; the secret also signs room tokens, and draws the password of the sandbox's `hand-over` trunk ([telephony.md](telephony.md), "A developer's own phone"). Each LiveKit sends its webhook naming its environment (`/v1/livekit/webhook?world=sandbox`) |
| `LIVEKIT_PUBLIC_URL` | the URL a browser is told to join on a deployment with no `PINECALL_DOMAIN`. With one, a browser joins `wss://<the name>`, and a sandbox call `wss://<the name>/sandbox` when the sandbox has a LiveKit of its own: the load balancer sends `/sandbox/rtc…` there, the prefix taken off |
| `DATABASE_URL` | Postgres 17 with pgvector and pg_textsearch: the one stateful service. One database for both environments |
| `PINECALL_REDIS_URL` | the Redis the gateways tell each other what just happened on (`redis://:password@host:port/db`; in a cluster built from the secret `pinecall-<world>-redis-password`, which LiveKit and SIP use too): what a call just wrote, who holds what. Lossy by design and never a record: a reader that misses a message resumes from Postgres. Unset, the gateway is alone on its platform and keeps all of it in its own memory, as it always has; set and not answering, the gateway starts and serves all the same, and says so in its log. In a cluster it is LiveKit's Redis, database 1 (`charts/pinecall/templates/secrets.yaml`) |
| `PINECALL_DB_POOL` | the connections the gateway holds open to Postgres, 10 unless set: two are its log writer's own, open for the process's life, one per lane, so an append never waits for an endpoint and an endpoint never waits for an append; the endpoints share the rest. On each one a statement is cancelled at 30 s and a transaction left idle is ended at 60 s; a request that finds every connection taken for 2 s is answered `503`, and so is a statement cancelled at its timeout (a worker retries a `5xx`). What is long on purpose runs with neither timeout: the migrations, an erasure, an export, the nightly retention and a knowledge base's push |
| `PINECALL_WORKER_KEY` | the key the worker knocks with: its environment's fleet key (`keys fleet`), which in a cluster the chart's install Job mints into the secret `pinecall-fleet-keys`, and on a laptop `make local` writes into `.local/env`. A worker given an org's own key instead serves that org on the org's own provider keys: the platform lends its keys to its own fleets alone |
| `PINECALL_RUNNER_KEY` · `PINECALL_RUNNER_IMAGE` · `PINECALL_RUNNER_RUNTIME_CLASS` · `PINECALL_RUNNER_NAMESPACE` · `PINECALL_RUNNER_SOURCES_URL` · `PINECALL_RUNNER_LISTEN` · `PINECALL_RUNNER_PORT` | the runner's, a pod of the hosting cluster (`charts/hosting`): the key of its environment (`keys runner`), the image every hosted app runs in (`docker.io/library/node:24-slim`), the runtime class of their pods (`gvisor`, GKE Sandbox), the namespace they run in (`pinecall-apps`), its own address as those pods reach it for a release's sources, and where it serves them (`127.0.0.1:8080`; its chart says every address). It reads `PINECALL_GATEWAY_URL` for the platform it serves, by its public name: the hosting cluster is not the platform's |
| `PINECALL_OPS_KEY` | the platform's own key to `/v1/ops/*`. Unset, only a person the platform made an operator opens those endpoints |
| `PINECALL_VAULT_KEY` | **required by the gateway**: the Fernet key every sealed secret is under (an org's vendor keys, the platform's, SMTP, SSO, the carrier). A gateway without it does not start. To rotate: a comma-separated list, the new key first; a secret seals under the first and opens under whichever sealed it |
| `PINECALL_TOKEN_KEY` | **required by the gateway**: the key it signs its own tokens with, those that open no room (a call's log token, a caller code's token). Only the gateway holds it, never a worker or LiveKit, so the LiveKit pair a worker holds forges none; a reading token signed with the pair reads nothing. Changing it ends every log link and code token handed out, which live hours at most |
| `PINECALL_APPROVE_HOOKED` | `true` makes a number an org hooks itself (pointed at the platform from its own carrier, so nothing proves it is the org's) wait for the operator: no call to it opens until `POST /v1/ops/numbers/{number}/approve` ([protocol/numbers.md](protocol/numbers.md)). Unset, a hooked number rings at once; one org holds a number either way |
| `PINECALL_DB_APP_ROLE` | read by `migrate up` alone: the role it grants every table's rows to after the migrations (`select`, `insert`, `update`, `delete`, the sequences), now and for every table made later, and never a table's shape. The gateway then connects as that role in its own `DATABASE_URL`. Unset (a laptop), the owner is the gateway's role too. A role that does not exist after two minutes is refused, never skipped |
| `PINECALL_ROLE` | parsed (`all` · `hub` · `worker`) and read by no process: a leftover of the single-machine install, which is gone |
| `PINECALL_GATEWAY_URL` | the gateway: on a laptop its loopback address, what it binds; what a worker's job asks, in a pod the gateways' Service (`http://pinecall-gateway:8080`) |
| `PINECALL_GATEWAY_LISTEN` · `PINECALL_TRUSTED_PROXIES` · `PINECALL_METRICS_FROM` *(a pod's)* | in a Kubernetes pod (`infra/charts`): where the gateway binds (`0.0.0.0:8080`; unset, the URL's loopback), the addresses or networks whose `X-Forwarded-For` it believes (unset, `127.0.0.1`; a cluster's, the load balancer's ranges and its own address, which ends every `X-Forwarded-For` it writes), and the ones `/metrics` answers (unset, loopback; a cluster's, the pods' network), a forwarded request never |
| `PINECALL_MAX_JOBS` *(the pod's)* · `PINECALL_APP` · `PINECALL_AGENT` | what a worker takes, and for whom: it reports `0.7 × calls ÷ slots` to LiveKit, whose full is 0.7, so LiveKit offers it calls until every one of these is taken. Unset, load is gated on CPU, refused at 0.7 |
| `PINECALL_FLEET` *(the pod's)* | the fleet this worker unit joins: the name it registers under with LiveKit, `pinecall` unless set, spelled like a slug, never empty. Which fleet each environment's calls go to is the `fleets` row of `box_settings` (`{"production": "pinecall", "sandbox": "pinecall-sandbox"}` unless the operator changes it), so a cluster runs the workers of each environment as Deployments of their own (`fleets` in the chart's values) |
| `PINECALL_IDLE_PROCESSES` *(the pod's)* | job processes the worker keeps warm. Unset, livekit's own: one per CPU. Set to 1 on the core node's workers, which share its cores with the platform's services; a scaled worker, alone on its node, leaves it unset |
| `PINECALL_WORKER_NAME` · `PINECALL_WORKER_HTTP_PORT` · `PINECALL_WORKER_HTTP_HOST` · `PINECALL_OVERFLOW_SAYS` | its name in the roster (unset: the hostname), its health port (8082) and the address it binds (unset, loopback; a pod's, `0.0.0.0` for its probes), and the overflow agent's one sentence |
| `NOTIFY_SOCKET` *(systemd's)* | where a worker run as a systemd `Type=notify` unit says `READY=1`, once LiveKit registered it and the gateway answered its heartbeat. Nothing here sets it: in a pod the start is its startup probe on the health port, livekit's own |
| `PINECALL_RECORDINGS` | where a kept recording lands. **Whether** audio is kept is the agent's own setting |
| `PINECALL_S3_ENDPOINT` · `PINECALL_S3_REGION` · `PINECALL_S3_ACCESS_KEY_ID` · `PINECALL_S3_SECRET_ACCESS_KEY` | the object store what leaves the disk goes to: any S3-compatible endpoint (AWS S3, Google Cloud Storage by HMAC key, R2, B2, MinIO), the region its signature names, and the key the platform writes with. In a cluster the endpoint, the region and
the bucket are `store` in the chart's values, and the key is the environment's `s3-access-key-id` and
`s3-secret-access-key` in Secret Manager, put by the operator (`infra/terraform/modules/secrets`,
`given`). A runtime given a recordings bucket and not all four does not start, and says which are missing |
| `PINECALL_RECORDINGS_BUCKET` | the bucket of that store a finished recording moves to, as `<org>/<call>/audio.ogg`. Unset, recordings stay on the disk as they always have |
| `PINECALL_WAL_SPOOL` | a WAL spool whose backlog `doctor`'s `archive` line reads through Postgres (unset, `/var/lib/pinecall/wal`, the single-machine install's). The chart does not set it: a cluster's Postgres archives through its operator and has no such directory, which the line reads as empty, so it reads the archiver alone |
| `PINECALL_SMTP_URL` · `PINECALL_MAIL_FROM` | the platform's own mail (`smtp://user:pass@host:587`, or `smtps://…:465`) and who its letters are from. A mailbox stored at `PUT /v1/ops/mail` wins over these, and an org's own over both. A URL that does not read is said in the log at start, and the platform posts nothing of its own |
| `PINECALL_DOMAIN` | the platform's one name, where the public, the SDKs and a carrier reach it, for both environments. The environment of a request is never the name's: a server's token opens the environment it was made in, and a person's key opens the one `pinecall-env` asks for, the sandbox when it asks for none. The console is production's at `/` and the sandbox's at `/sandbox/…`. A sign-in, a password or an invitation link, and the SSO callback, carry this name, never the request's own Host. Unset, nothing imports a number and a hosted app has no gateway to dial |
| `PINECALL_SIP_DOMAIN` · `PINECALL_SANDBOX_SIP_DOMAIN` | the name a carrier sends each environment's calls to, when it is not the platform's name: in a cluster the platform's name is Google's HTTPS load balancer, which carries no SIP, and each environment's SIP node has an address of its own. Unset, the platform's name (the sandbox's: production's SIP name, else the platform's). Where the sandbox has a LiveKit of its own, the address `PINECALL_SIP_DOMAIN` names, looked up at start, is the one the sandbox's `hand-over` trunk admits a developer's ring from: unset, the sandbox admits none, and a developer's ring reaches production's agent once its leg went unanswered. Nobody types them: the runtime writes an environment's into a trunk when a number is imported there, and one made before the name moved is sent on by `pinecall-runtime sip repoint`, once |
| `PINECALL_SIGNUP` · `PINECALL_SIGNUP_KEY` · `PINECALL_CLOUD` · `PINECALL_BILLING_URL` | whether a stranger may make an org here (off unless set), the Bearer key the sign-up endpoints take from the bot-shielded site in front of them, Pinecall's hosted gateway, and where its orgs pay. What a new org is allowed is not a variable: it is the `admission` row of `box_settings` (see [limits.md](limits.md)) |
| `PINECALL_APP_ORIGINS` · `PINECALL_MIN_PASSWORD` | origins besides the mobile app's two that may call `/v1` from a browser, comma separated and never `*`; how short a password may be (8; `0` is no rule) |
| `PINECALL_VOICE_LOOKUP_BUDGET_MS` · `PINECALL_TEXT_LOOKUP_BUDGET_MS` · `PINECALL_REMEMBER_BUDGET_S` | how long a turn waits for recall and search, and a hang-up for memory. Unset: the session's own |
| `PINECALL_TIMEZONE` | the IANA zone a call's `today` is read in (`Europe/Madrid`); `UTC` unless set. An unknown zone is refused at startup |
| `PINECALL_LOG_LEVEL` · `PINECALL_LOG_FORMAT` | `DEBUG` · `INFO` · `WARNING` · `ERROR`; and the gateway's lines, `text` for a terminal or `json` for a journal |
| `PINECALL_OTLP_ENDPOINT` · `PINECALL_OTLP_HEADERS` · `PINECALL_OTLP_PII` | where the worker sends a call's traces (OTLP over HTTP), the headers each export carries (a credential), and whether a span carries what was said. Unset, nothing is traced |

The platform's own Twilio account (the one it buys numbers on) and its Meta app (the secret every WhatsApp webhook is signed with, the handshake's word, the token replies go out on when an org brought none) are not variables: they are the sealed rows `credentials/twilio` and `credentials/whatsapp` of `box_settings`, beside the platform's vendor keys. See [numbers.md](protocol/numbers.md).

The source is `pinecall/process/settings.py`: one field per variable, with its alias and its
one-line description. A variable this table names and that file does not, or the reverse, is a bug
in whichever is younger.

## The vendors

Every livekit plugin installed that exports an LLM, an STT or a TTS is a vendor, under the name of
its module (`livekit.plugins.cartesia` is `cartesia`), and LiveKit Inference is `livekit`. The
runtime keeps no list of them: `pip install "pinecall[voice]"` installs all but four,
`pinecall[voice-big]` adds the four whose SDKs weigh hundreds of megabytes (aws, azure, google,
speechmatics), and a plugin livekit ships tomorrow is a vendor on the next install.
`GET /v1/providers` lists what this deployment has and, for the org asking, whose key runs each vendor:
*yours* (the org brought one), *offered* (the platform holds one and lends it to this org), *bring
your own* (installed, and only the org can key it); and a plugin that does not import, with why.

An agent names a stage as `vendor/model`, a vendor alone (its default model), or a model alone
(on the vendor it runs). Only a vendor that is not installed, or does not do that stage, is
refused: a model or a voice the vendor does not have is the vendor's own error, in the call's
log.

**Keys.** An org's own key for a vendor runs any model of it. Otherwise the call runs on the
platform's, where the org's `quotas.lends` lends it (`limits.md`); and a vendor nobody keyed is
refused before the call. A key is one secret, or the constructor's own arguments where a vendor
takes more (Azure's key and region, a Google service account, AWS's key pair): both are stored
encrypted, the org's as its provider keys, the platform's as the platform's own, written from the console.
Offering a vendor is holding its key: whoever runs the runtime loads the keys of the vendors it
offers, and any other installed vendor is the org's to bring.

**The bill.** livekit counts each stage's usage (tokens with their cache, characters, seconds
heard) whatever key it ran on; the memory model's tokens and each phone leg's minutes join it, and
`call.summary` carries the rows priced at the row's rates ([charging-for-it.md](charging-for-it.md)).
A model or a leg with no rate is listed unpriced, never at zero.

**The platform's choices** are one row of the database, edited from the console's platform screen: the
vendor and model each stage runs when an agent names none, the voice per language, what each
vendor is told for a stage (the class it builds where it is not `STT`/`TTS`/`LLM`, its keyword
arguments by the plugin's own names, whether its ears end the turn), the languages the ears
listen for, the price of each model, the judge, and the one embedder every knowledge base and
fact is written with (its URL, model, wire shape and width; its key is the platform's
`credentials/<vendor>` row). The first platform is seeded from the one before it; after that, nothing
of it is read from code.

## A laptop

A checkout and `uv sync` is the whole of it for writing the runtime: `make check` runs the rules and
every suite that needs no database; `make test` starts a Postgres of its own in colima (`make db`:
the image of `infra/local/postgres/`, Postgres 17 with pgvector and pg_textsearch, on tmpfs,
durability off) and a Redis beside it (nothing kept), and gives every test a schema of its own and
a prefix of its own on the Redis. `make suite` runs the same suites inside a cluster, against its
Postgres under CloudNativePG.

The runtime whole runs on a laptop too, with no cloud account: `make local` starts the platform's
Postgres, Redis and LiveKit in docker (`infra/local/compose.yaml`, ports 55433, 56380, 7880),
migrates the schema and writes `.local/env` once (a vault key, an ops key and the sandbox fleet's
key drawn there, 0600); `make local-gateway` and `make local-worker` run both from the checkout on
those settings, and `make local-down` stops the compose. LiveKit's pair there is a laptop-only
dev pair (`infra/local/livekit.yaml`). A phone needs the SIP bridge (`--profile phone`, Linux
only) and a carrier that reaches the laptop: [../infra/local/README.md](../infra/local/README.md).

## A cluster

Everything a runtime needs in the cloud is `infra/`: Terraform makes the cluster, its node pools,
the registry, the address, the firewall, the names and the secrets (drawn there, kept in Secret
Manager: the LiveKit pair, the Redis password, `PINECALL_VAULT_KEY`, `PINECALL_OPS_KEY`,
`PINECALL_TOKEN_KEY`), and the
chart runs the gateways, the workers, LiveKit, SIP and Redis, with Postgres under CloudNativePG.
External Secrets hands each pod its secrets as environment, never a file in the image; the
operator's verbs run from a laptop against the gateway, with the ops key. The whole walk, from
nothing to the suites and a release: [../infra/README.md](../infra/README.md).
