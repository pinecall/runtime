# The environment, and a runtime from nothing

Every variable the gateway and the worker read, and the two ways this runtime is brought up: a
laptop, and a box. The verbs themselves are [the-runtime-cli.md](the-runtime-cli.md).

## The environment

`.env` in the directory the process started in, then in each parent up to the repository root; on a
box, `/etc/pinecall/box.env` and then the instance's own `/etc/pinecall/instances/<name>.env`
(which wins), with the secrets as systemd credentials. Our own knobs carry `PINECALL_`; a vendor
key keeps the vendor's own name, so the SDK that reads `ANTHROPIC_API_KEY` by itself and this
runtime agree.

**A runtime is one instance.** A laptop is one, production unless its `.env` says `sandbox`; a box
runs one or more, each its own gateway, worker, database, fleet and keys, sharing the media plane
and the vendors' keys. Every variable below marked *(the instance's)* is written per instance by
`pinecall-runtime box instance`, never in `box.env`, which describes the box: its role, its
instances, its embedder, its fleet cloud, its mail.

| | |
|---|---|
| `PINECALL_WORLD` *(the instance's)* | the one world this instance is: `production` unless it says `sandbox`. A box that runs one instance is production, as every box was before there were two; the sandbox is said on purpose, by its own instance's environment file. Nothing picks a world per request |
| `PINECALL_ELSEWHERE_URL` · `PINECALL_IDENTITY_URL` *(the instance's)* | the other instance's public URL — named in every refusal that sends a person there, marked into the console and served at `/.well-known/pinecall` — and, on a sandbox instance, production's: where people sign in. **`PINECALL_IDENTITY_URL` is required on a sandbox**, which asks production who a person is and holds no password of its own: one without it is refused at startup in one sentence |
| `PINECALL_SANDBOX_URL` · `PINECALL_SANDBOX_KEY` · `PINECALL_PEER_KEY` *(the instance's)* | the peers ([../infra/box/README.md](../infra/box/README.md), "Peers"). On production, where its sandbox answers — asked whose a ring from a developer's own phone is — and the fleet key that sandbox minted for it, in production's store; **both or neither**, or production is refused at startup in one sentence. On a sandbox, `PINECALL_PEER_KEY`, the fleet key production minted for it, with which it reads production's numbers at `PINECALL_IDENTITY_URL`. The keys are minted by `pinecall-runtime box peer` and never typed |
| `LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` | the media plane both processes talk to. The secret also signs call tokens |
| `LIVEKIT_PUBLIC_URL` | the URL a browser is told to join, when it differs |
| `DATABASE_URL` | Postgres 17 with pgvector and pg_textsearch: the one stateful service. On a box each instance has its own database and role in the one Postgres (`pinecall` for production, `pinecall_<name>` for another), in its own credstore |
| `TEI_URL` · `EMBED_PROVIDER` · `EMBED_MODEL` · `EMBED_BASE_URL` · `PERPLEXITY_API_KEY` · `OPENROUTER_API_KEY` | who embeds, where, and the key for a vendor that takes one |
| `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `SONIOX_API_KEY` · `DEEPGRAM_API_KEY` · `ELEVEN_API_KEY` | a call needs one key of each role: llm, stt, tts |
| `PINECALL_WORKER_KEY` | the key the worker knocks with. On a box the fleet's: `keys issue --org default --scope fleet --scope app --scope calls`, which is what lets one worker answer every org's calls. On a laptop an org's own key, and the worker serves that org |
| `PINECALL_OPS_KEY` | the box's own key to `/v1/ops/*`, and what these verbs knock with. A person the box made an operator opens the same doors with their own key; unset, only such a person does |
| `PINECALL_VAULT_KEY` | the Fernet key a tenant's own provider keys are encrypted under |
| `PINECALL_ROLE` · `PINECALL_INSTANCES` | what this box runs: `all` · `hub` · `worker`; and which instances, by name, space-separated — `production` when unset. Both `box.env`'s |
| `PINECALL_GATEWAY_URL` *(the instance's)* | the instance's gateway on loopback: the host and port the gateway binds, and what its worker's job asks. On a worker box, the hub's |
| `PINECALL_MAX_JOBS` *(the instance's)* · `PINECALL_APP` · `PINECALL_AGENT` | what a worker takes, and for whom |
| `PINECALL_FLEET` *(the instance's)* | the name this instance's workers register under and its gateway dispatches to, and the prefix of every trunk and rule it names on the SFU (`<fleet>:<org>`): `pinecall` unless set, spelled like a slug (`[a-z0-9-]`, never empty), or the process does not start. Two instances on one SFU each set their own, so neither takes the other's calls |
| `PINECALL_IDLE_PROCESSES` *(the instance's)* | how many job processes the worker keeps warm. Unset, livekit's own: one per CPU — RAM a second instance on the same CPUs spends twice |
| `PINECALL_WORKER_NAME` · `PINECALL_OVERFLOW_SAYS` | its name in the roster (unset: the hostname), and the overflow agent's one sentence |
| `PINECALL_RECORDINGS` *(the instance's)* · `PINECALL_EGRESS_URL` | where a call's audio lands — on a box `/var/lib/pinecall/recordings/<instance>`, and where the recorder that writes it answers. **Whether** it is kept is the agent's own setting (`pinecall agent set --record`), not the box's |
| `WHATSAPP_ACCESS_TOKEN` · `PINECALL_WHATSAPP_APP_SECRET` · `PINECALL_WHATSAPP_VERIFY_TOKEN` | Meta's webhook: the token messages are sent with; the app's App Secret every webhook body is HMAC-SHA256-signed with (unset, the WhatsApp door is closed); the word Meta echoes back when the webhook is subscribed |
| `PINECALL_SMTP_URL` · `PINECALL_MAIL_FROM` | the box's own mail (`smtp://user:pass@host:587`, or `smtps://…:465`) and who its letters are from. A mailbox stored at `PUT /v1/ops/mail` is used before these, and an org that wired its own uses that one; with none, nothing is sent |
| `TWILIO_ACCOUNT_SID` · `TWILIO_API_KEY` · `TWILIO_API_SECRET` | the box's own carrier account, for the numbers it buys for a tenant |
| `PINECALL_DOMAIN` *(the instance's)* | the instance's public name: where Caddy answers it, and where a carrier sends a call for a number a tenant imports. Unset, nothing imports |
| `PINECALL_WORKER_HTTP_PORT` *(the instance's)* | where the worker's own health server binds, on loopback: 8082 unless set; two above the gateway's port on a box |
| `PINECALL_SIGNUP` · `PINECALL_CLOUD` · `PINECALL_EXTENSIONS` | whether a stranger may make an org here (off unless set); Pinecall's hosted gateway; packages that plug a policy into the runtime, comma separated |
| `PINECALL_APP_ORIGINS` | origins besides the mobile app's own two (`capacitor://localhost`, `https://localhost`) that may call `/v1` from a browser, comma separated — the app's dev server on a laptop. Unset, only the two; never `*` ([people.md](protocol/people.md)) |
| `PINECALL_MIN_PASSWORD` | how short a member's password may be: 8 unless set, `0` for no rule |
| `PINECALL_JUDGE_CEILING_EUR` | what judging one call may spend on a model. Zero: no judge asks |
| `PINECALL_VOICE_LOOKUP_BUDGET_MS` · `PINECALL_TEXT_LOOKUP_BUDGET_MS` · `PINECALL_REMEMBER_BUDGET_S` | how long a turn waits for recall and search, and a hang-up for memory |
| `PINECALL_LOG_LEVEL` | `DEBUG` · `INFO` · `WARNING` · `ERROR` |

## A laptop, from nothing

```bash
cd runtime
docker compose -f infra/compose/dev.yml up -d      # livekit · sip · redis · postgres · tei
uv sync --extra runtime --group dev
cp .env.example .env                               # the provider keys; the instance is production
pinecall-runtime migrate up                        # the schema, on the compose Postgres
pinecall-runtime doctor                            # every line green before anything else
pinecall-runtime init --org clinica \
  --email berna@clinica.test --person "Berna"      # the first org, and a link to set a password
pinecall-runtime gateway
pinecall-runtime worker dev                        # in another terminal, for spoken calls
pinecall login http://localhost:8080 && pinecall link   # as a person; the agent's .env
```

[from-zero.md](from-zero.md) is this same path with every output under it, through to a call. **This
is the same runtime a box runs, and there is no other.** The `PINECALL_DEV_KEY` a laptop used to
have — no database, no login — cost more every hour than it saved in the first five minutes: two
sets of keys, two orgs, two behaviours, and no way to see which you were on. On an M-series Mac,
TEI needs the arm64 tag `infra/README.md` names in `TEI_IMAGE`; without an embedder a push answers
503 and a lookup is skipped and said in the call's log.

## A box, from nothing

A box's secrets are systemd credentials, so a verb typed at a shell there reads a box that does
not exist. Two ways in and no third — on the box the Makefile, which hands a verb one instance's
own environment and credentials; from the checkout the operator API with that instance's
`PINECALL_OPS_KEY` exported ([a-box-in-production.md](a-box-in-production.md)).

```bash
sudo make -s -C /opt/pinecall/app/runtime/infra/box doctor INSTANCE=production   # also providers
export PINECALL_OPS_KEY=…                                    # from the checkout, once, per shell
pinecall-runtime orgs add clinica --name "Clínica Norte"
pinecall-runtime keys issue --org clinica --label "berna's laptop"
pinecall-runtime routes add +34910000000 clinica-norte --org clinica
```

The first deploy makes the box one instance, `production`: its env file from `box.env`'s
`PINECALL_DOMAIN`, its three secrets from the box's own first draw. A second — a sandbox, a
staging — is one verb from the checkout, its name in `PINECALL_INSTANCES`, and a deploy:

```bash
make instance NAME=sandbox WORLD=sandbox DOMAIN=sandbox.example.com \
              IDENTITY=https://box.example.com ELSEWHERE=https://box.example.com
make ssh      # sudoedit /etc/pinecall/box.env → PINECALL_INSTANCES="production sandbox"
make deploy   # its database, units, Caddy site; restarted and doctored after production
make instance NAME=production WORLD=production DOMAIN=box.example.com \
              ELSEWHERE=https://sandbox.example.com SANDBOX=https://sandbox.example.com FORCE=1
make deploy   # the pair: each gateway gets the other's peer key
```

`box secrets`, `box database` and `migrate up` are the units', run before a gateway opens; nobody
types them by hand on a box that is up. [../infra/box/README.md](../infra/box/README.md), "An
instance", is the whole of it; [multi-tenancy.md](multi-tenancy.md) says what a key IS.
