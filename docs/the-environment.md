# The environment, and a runtime from nothing

Every variable the gateway and the worker read, and the two ways this runtime is brought up: a
laptop, and a box. The verbs themselves are [the-runtime-cli.md](the-runtime-cli.md).

## The environment

`.env` in the directory the process started in, then in each parent up to the repository root; on a
box, systemd credentials instead. Our own knobs carry `PINECALL_`; a vendor key keeps the vendor's
own name, so the SDK that reads `ANTHROPIC_API_KEY` by itself and this runtime agree.

| | |
|---|---|
| `LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` | the media plane both processes talk to. The secret also signs call tokens |
| `LIVEKIT_PUBLIC_URL` | the URL a browser is told to join, when it differs |
| `DATABASE_URL` | Postgres 17 with pgvector and pg_textsearch: the one stateful service |
| `TEI_URL` · `EMBED_PROVIDER` · `EMBED_MODEL` · `EMBED_BASE_URL` · `PERPLEXITY_API_KEY` · `OPENROUTER_API_KEY` | who embeds, where, and the key for a vendor that takes one |
| `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `SONIOX_API_KEY` · `DEEPGRAM_API_KEY` · `ELEVEN_API_KEY` | a call needs one key of each role: llm, stt, tts |
| `PINECALL_WORKER_KEY` | the key the worker knocks with. On a box the fleet's: `keys issue --org default --scope fleet --scope app --scope calls`, which is what lets one worker answer every org's calls. On a laptop an org's own key, and the worker serves that org |
| `PINECALL_OPS_KEY` | the box's own key to `/v1/ops/*`, and what these verbs knock with. A person the box made an operator opens the same doors with their own key; unset, only such a person does |
| `PINECALL_VAULT_KEY` | the Fernet key a tenant's own provider keys are encrypted under |
| `PINECALL_ROLE` | what this box runs: `all` · `hub` · `worker` |
| `PINECALL_GATEWAY_URL` | the gateway a worker's job asks |
| `PINECALL_MAX_JOBS` · `PINECALL_APP` · `PINECALL_AGENT` | what a worker takes, and for whom |
| `PINECALL_FLEET` | the name this instance's workers register under and its gateway dispatches to, and the prefix of every trunk and rule it names on the SFU (`<fleet>:<org>`): `pinecall` unless set. Two instances on one SFU each set their own, so neither takes the other's calls |
| `PINECALL_IDLE_PROCESSES` | how many job processes the worker keeps warm. Unset, livekit's own: one per CPU — RAM a second instance on the same CPUs spends twice |
| `PINECALL_WORKER_NAME` · `PINECALL_OVERFLOW_SAYS` | its name in the roster (unset: the hostname), and the overflow agent's one sentence |
| `PINECALL_RECORDINGS` · `PINECALL_EGRESS_URL` | where a call's audio lands, and where the recorder that writes it answers. **Whether** it is kept is the agent's own setting (`pinecall agent set --record`), not the box's |
| `WHATSAPP_ACCESS_TOKEN` · `PINECALL_WHATSAPP_APP_SECRET` · `PINECALL_WHATSAPP_VERIFY_TOKEN` | Meta's webhook: the token messages are sent with; the app's App Secret every webhook body is HMAC-SHA256-signed with (unset, the WhatsApp door is closed); the word Meta echoes back when the webhook is subscribed |
| `PINECALL_SMTP_URL` · `PINECALL_MAIL_FROM` | the box's own mail (`smtp://user:pass@host:587`, or `smtps://…:465`) and who its letters are from. A mailbox stored at `PUT /v1/ops/mail` is used before these, and an org that wired its own uses that one; with none, nothing is sent |
| `TWILIO_ACCOUNT_SID` · `TWILIO_API_KEY` · `TWILIO_API_SECRET` | the box's own carrier account, for the numbers it buys for a tenant |
| `PINECALL_DOMAIN` | the box's public name: where a carrier sends a call for a number a tenant imports. Unset, nothing imports |
| `PINECALL_WORKER_HTTP_PORT` | where the worker's own health server binds, on loopback: 8082 unless set |
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
cp .env.example .env                               # and fill in the provider keys
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
not exist. Two ways in and no third — on the box the Makefile, which hands a verb the units' own
environment; from the checkout the operator API with `PINECALL_OPS_KEY` exported ([a-box-in-production.md](a-box-in-production.md)).

```bash
sudo make -s -C /opt/pinecall/app/runtime/infra/box doctor   # on the box: also providers, status
export PINECALL_OPS_KEY=…                                    # from the checkout, once, per shell
pinecall-runtime orgs add clinica --name "Clínica Norte"
pinecall-runtime keys issue --org clinica --label "berna's laptop"
pinecall-runtime routes add +34910000000 clinica-norte --org clinica
```

`box secrets` and `migrate up` are the units', run before the gateway opens; nobody types them by
hand on a box that is up. [multi-tenancy.md](multi-tenancy.md) says what a key IS.
