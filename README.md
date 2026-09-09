# pinecall

The Pinecall voice-AI runtime: one Python distribution, two processes, on LiveKit.

- `pinecall-runtime gateway` is the control plane: the app protocol over WebSocket, the call
  log over SSE, the tokens, the routes, WhatsApp's webhook, the operator API.
- `pinecall-runtime worker` is the fleet: one livekit-agents worker, one process per call.

The public talks to an agent by web, WhatsApp and telephone. The agent itself is written with
the `pinecall` framework, in the agents repository; this runtime holds the log, the wire, the
tenants, the sessions and the judges, and never the conversation.

```
pip install pinecall            the gateway
pip install pinecall[runtime]   the gateway and the worker (livekit-agents and its plugins)
```

## Five minutes

```
docker compose -f infra/compose/dev.yml up -d      livekit · sip · redis · postgres · tei
scripts/bootstrap                                  uv sync, every extra and tool group
uv run pinecall-runtime migrate up                 the schema; a fresh database seeds the
                                                   default org, and `keys issue` mints its key
uv run pinecall-runtime gateway                    the control plane, on 8080
uv run pinecall-runtime worker dev                 the fleet that answers a call
uv run pinecall-runtime doctor                     every service and key, one line each
```

Development happens from the checkout, with `uv`:

```
scripts/format        ruff format, then the fixable lint rules
scripts/lint          ruff, pyright strict, mypy strict
scripts/test          pytest -m "unit or postgres": no keys, no network
make deploy           this checkout onto your box: rsync, ssh, make, curl (infra/box/README.md)
```

The wire is `pinecall-protocol`, generated in the protocol repository and resolved here as the
checkout beside this one (`../protocol/python`, see `pyproject.toml`).

## The map

Fourteen directories under `src/pinecall/`, and none of them is a process:

| directory | what it is |
|---|---|
| `types/` | the shapes both processes speak: org, route, agent, call, tool, token, consent. No IO |
| `auth/` | who knocks: the bearer header, the API keys, the call tokens and their scopes |
| `log/` | the truth: the entry, the store, the reducer, the projections, the fanout, the meter |
| `providers/` | one file per vendor, the registry, the pipeline, the prices |
| `session/` | one call, on either channel: `text/` and `voice/`, and what both share |
| `orgs/` | the tenants: the table, admission by quota, the meter, the vault |
| `routes/` | which agent answers a door: the operator's table, and how it outranks a declaration |
| `tokens/` | what a browser receives and spends once: the ledger, the room, a human's seat |
| `whatsapp/` | the third door's machinery: Meta's bodies in, the signature, the answer out |
| `evals/` | the judges, the ring-3 checks, the rings, and the score a call seals on |
| `api/` | the doors, and the process memory behind them. Never imports `worker/` |
| `worker/` | the job: its router, its client to the gateway, the recordings. Never imports `api/` |
| `cli/` | `pinecall-runtime <group> <verb>` |
| `migrations/` | the schema, numbered, applied in order, never edited |

`tests/test_isolation.py` carries the whole import table: a package earns its directory by
having a line in it. `docs/decisions/` holds the why, one page per module; `docs/protocol/`
the operator API and the token door as public contracts; `infra/` the dev stack and the box.
