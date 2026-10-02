# The runtime on a laptop

The box's services in docker, the gateway and a worker from the checkout. No cloud account, no
key of the box: everything secret is drawn on the laptop and stays in `.local/env` (0600, ignored
by git).

```console
$ make local            # Postgres :55433, Redis :56380, LiveKit :7880; schema migrated; .local/env
$ make local-gateway    # the gateway on 127.0.0.1:8080 (production = localhost, sandbox = sandbox.localhost)
$ make local-worker     # in another terminal: a worker of the sandbox fleet, 4 seats
$ set -a; . ./.local/env; set +a; uv run pinecall-runtime fleet list
pinecall-sandbox   <your hostname>   0   4  0.00  accepting  4s ago
$ make local-down       # the compose stopped; the database volume and .local/env are kept
```

| file | what it is |
|---|---|
| `compose.yaml` | Postgres (the image of `infra/postgres/`, the box's), Redis, LiveKit, pinned; the SIP bridge under the `phone` profile |
| `livekit.yaml` | LiveKit's config: a dev pair that exists only here, media on 7881/tcp and 7882/udp, its webhook to the gateway |
| `sip.yaml` | the SIP bridge's config, on the same pair and Redis |
| `up.sh` | `make local`: the compose up and healthy, `migrate up`, `.local/env` written once |

`.local/env` holds the runtime's settings (`docs/the-environment.md`): the DSN, the Redis, the
LiveKit pair, both worlds' names, a fresh `PINECALL_VAULT_KEY` and `PINECALL_OPS_KEY`, and the
sandbox fleet's key (`keys fleet sandbox`). Run `make local` again and it keeps them; delete the
file and the volume (`docker compose -f infra/local/compose.yaml down -v`) to start from nothing.

A browser or an SDK reaches the laptop's LiveKit at `ws://127.0.0.1:7880`; a call over a vendor
needs that vendor's key, set like on a box (the console's providers page, on the laptop's own
gateway).

## A phone

`docker compose -f infra/local/compose.yaml --profile phone up -d sip` adds the SIP bridge on the
host's network (5060, RTP 10000–10019). That works on Linux; Docker Desktop on macOS has no host
network, so a phone call is tried against a box. The carrier must reach the laptop's 5060 from the
internet: a home router forwards it, or a VM with a public address runs the same compose.
