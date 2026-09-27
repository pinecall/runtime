# The environment

Every variable the gateway and the worker read. The verbs are [the-runtime-cli.md](the-runtime-cli.md).

## Where a variable comes from

Three places, the later winning: the systemd credentials of the instance (one file per name under
`CREDENTIALS_DIRECTORY`), then `.env` in the directory the process started in, then in each parent
up to the repository root (`.env`, then `runtime/.env`, in the first directory that has either),
then the real environment. A bare `NAME=` means unset. A `.env` that exists and cannot be read
stops the process with its name; it is never skipped.

Our own knobs carry `PINECALL_`; a vendor key keeps the vendor's own name (`ANTHROPIC_API_KEY`),
so the SDK that reads it by itself and this runtime agree. The runtime declares no field per
vendor: every variable read is kept, and the provider catalog asks for a vendor's key by name.

**A runtime is one instance.** A laptop is one, production unless its `.env` says `sandbox`; a box
runs one or more, each its own gateway, worker, database, fleet and keys, sharing the media plane
and the vendors' keys. A variable marked *(the instance's)* is written per instance, never in the
box's `box.env`.

| | |
|---|---|
| `PINECALL_WORLD` *(the instance's)* | the one world this instance is: `production` unless it says `sandbox`. Nothing picks a world per request |
| `PINECALL_ELSEWHERE_URL` · `PINECALL_IDENTITY_URL` *(the instance's)* | the other instance's public URL, named in every refusal that sends a person there; and, on a sandbox, production's, where people sign in. **`PINECALL_IDENTITY_URL` is required on a sandbox**: one without it is refused at startup in one sentence |
| `PINECALL_SANDBOX_URL` · `PINECALL_SANDBOX_KEY` · `PINECALL_PEER_KEY` *(the instance's)* | the peers. On production, where its sandbox answers and the fleet key that sandbox minted for it: **both or neither**, or production is refused at startup. On a sandbox, the fleet key production minted for it. Minted by `pinecall-runtime box peer`, never typed |
| `LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` | the media plane both processes talk to. The secret also signs call tokens |
| `LIVEKIT_PUBLIC_URL` | the URL a browser is told to join, when it differs |
| `DATABASE_URL` | Postgres 17 with pgvector and pg_textsearch: the one stateful service. On a box each instance has its own database and role in the one Postgres, in its own credstore |
| `TEI_URL` · `EMBED_PROVIDER` · `EMBED_MODEL` · `EMBED_BASE_URL` · `PERPLEXITY_API_KEY` · `OPENROUTER_API_KEY` | who embeds (`tei` · `perplexity` · `openrouter`), where, and the key for a vendor that takes one |
| `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `SONIOX_API_KEY` · `DEEPGRAM_API_KEY` · `ELEVEN_API_KEY` · `CARTESIA_API_KEY` … | a call needs one key of each role: llm, stt, tts. Every vendor of the catalog, by its own name |
| `PINECALL_WORKER_KEY` | the key the worker knocks with. On a box the fleet's; on a laptop an org's own key, and the worker serves that org |
| `PINECALL_OPS_KEY` | the box's own key to `/v1/ops/*`. Unset, only a person the box made an operator opens those doors |
| `PINECALL_VAULT_KEY` | the Fernet key a tenant's own secrets are encrypted under. To rotate: a comma-separated list, the new key first; a secret seals under the first and opens under whichever sealed it |
| `PINECALL_ROLE` | what this box runs: `all` · `hub` · `worker`; the doctor asks after what it has |
| `PINECALL_GATEWAY_URL` *(the instance's)* | the instance's gateway on loopback: what it binds, and what its worker's job asks |
| `PINECALL_MAX_JOBS` *(the instance's)* · `PINECALL_APP` · `PINECALL_AGENT` | what a worker takes, and for whom. Unset, load is gated on CPU |
| `PINECALL_FLEET` *(the instance's)* | the name this instance's workers register under and its gateway dispatches to, and the prefix of every trunk it names on the SFU: `pinecall` unless set, spelled like a slug, never empty |
| `PINECALL_IDLE_PROCESSES` *(the instance's)* | job processes the worker keeps warm. Unset, livekit's own: one per CPU |
| `PINECALL_WORKER_NAME` · `PINECALL_WORKER_HTTP_PORT` · `PINECALL_OVERFLOW_SAYS` | its name in the roster (unset: the hostname), its health port on loopback (8082), and the overflow agent's one sentence |
| `PINECALL_RECORDINGS` *(the instance's)* · `PINECALL_EGRESS_URL` | where a kept recording lands, and where the recorder answers its health check. **Whether** audio is kept is the agent's own setting |
| `WHATSAPP_ACCESS_TOKEN` · `PINECALL_WHATSAPP_APP_SECRET` · `PINECALL_WHATSAPP_VERIFY_TOKEN` | Meta's webhook: the token messages are sent with, the app secret every webhook body is signed with (unset, the door is closed), and the word Meta echoes back when subscribing |
| `PINECALL_SMTP_URL` · `PINECALL_MAIL_FROM` | the box's own mail (`smtp://user:pass@host:587`, or `smtps://…:465`) and who its letters are from. A mailbox stored at `PUT /v1/ops/mail` wins over these, and an org's own over both |
| `TWILIO_ACCOUNT_SID` · `TWILIO_API_KEY` · `TWILIO_API_SECRET` | the box's own carrier account, for the numbers it buys for a tenant |
| `PINECALL_DOMAIN` *(the instance's)* | the instance's public name: where a carrier sends a call for an imported number. Unset, nothing imports |
| `PINECALL_SIGNUP` · `PINECALL_SIGNUP_KEY` · `PINECALL_CLOUD` · `PINECALL_BILLING_URL` · `PINECALL_EXTENSIONS` | whether a stranger may make an org here (off unless set), the Bearer key the sign-up doors take from the bot-shielded site in front of them, Pinecall's hosted gateway, where its orgs pay, and the packages that plug a policy in |
| `PINECALL_APP_ORIGINS` · `PINECALL_MIN_PASSWORD` | origins besides the mobile app's two that may call `/v1` from a browser, comma separated and never `*`; how short a password may be (8; `0` is no rule) |
| `PINECALL_JUDGE_CEILING_EUR` | what judging one call may spend on a model. Zero: no model judge asks |
| `PINECALL_VOICE_LOOKUP_BUDGET_MS` · `PINECALL_TEXT_LOOKUP_BUDGET_MS` · `PINECALL_REMEMBER_BUDGET_S` | how long a turn waits for recall and search, and a hang-up for memory. Unset: the session's own |
| `PINECALL_TIMEZONE` | the IANA zone a call's `today` is read in (`Europe/Madrid`); `UTC` unless set. An unknown zone is refused at startup |
| `PINECALL_LOG_LEVEL` · `PINECALL_LOG_FORMAT` | `DEBUG` · `INFO` · `WARNING` · `ERROR`; and the gateway's lines, `text` for a terminal or `json` for a journal |
| `PINECALL_OTLP_ENDPOINT` · `PINECALL_OTLP_HEADERS` · `PINECALL_OTLP_PII` | where the worker sends a call's traces (OTLP over HTTP), the headers each export carries (a credential), and whether a span carries what was said. Unset, nothing is traced |

The source is `pinecall/settings/settings.py`: one field per variable, with its alias and its
one-line description. A variable this table names and that file does not, or the reverse, is a bug
in whichever is younger.

## A laptop

A `.env` with the vendor keys, in the checkout; the instance is production unless it says
otherwise. The suites run against the sandbox database on the box through an SSH tunnel
(`make test` opens it and hands the DSN to pytest without printing it); nothing runs a local
Postgres or LiveKit. The rest of the laptop path lands with the steps that write it.

## A box

An instance's secrets are systemd credentials, loaded by path out of its own store, so a verb
typed at a shell there reads a box that does not exist. The box's own page,
`infra/box/README.md`, is not written yet; `../runtime/infra/box/README.md` describes the box
both runtimes share.
