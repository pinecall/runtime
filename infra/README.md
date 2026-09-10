# infra — the development stack

Everything a laptop needs to carry a call end to end, in one compose file. Nothing here is
a product decision: it is the same five services a self-hosted box runs, pinned and small
enough to fit on a machine that is also running an editor. There, `tei` is a Quadlet unit and
the box runs it only where it embeds on the machine rather than at a vendor (`box/README.md`,
"The embedder"); the other four it always runs, on the same images as these.

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

---

The box — the same five services on a machine a stranger can telephone, the embedder being the
one it may not want, declared rather than scripted — is `infra/box/README.md`.
