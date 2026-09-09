# routes — a number is a route, and the operator's row wins

## The decision

Two tables can name the same door. An app declares its routes when its socket connects
(`api/agents/registry.py`); an operator types them into Postgres (`migrations/0003_routes.sql`).
When both name one number and different agents, **the operator's row answers and the declaration
is dropped from the union** — and the app is named in a warning beside the row that took its door,
every time the two tables are read together, never silently.

The reason is what `routes add` promises. The milestone is accepted against "a number is a route:
`pinecall-runtime routes add` changes who answers with no deploy". A declaration that could take
the number back on the app's next restart would make that a lie: the operator would move a number
at nine, the tenant would deploy at ten, and the call would land where nobody moved it. The other
order has no such failure — an operator who does not want the row can remove it, in the same
terminal, in one verb, and the declaration answers again from the next call.

The loss is the app's, so the app is told. `routes/answering.py` logs one line per
conflicting door naming the number, the agent that answers, and the agent that declared it too;
`POST /v1/ops/routes` answers with `overrides: <agent>` so `routes add` prints it in the operator's
own terminal, in the breath that caused it.

A consequence worth stating: an agent that loses its phone door has no phone door at all, so an
explicit outbound dispatch of that agent on the phone channel finds no route (`worker/router.py`
resolves an agent's door strictly by channel). That is correct — it no longer answers at that
number, and it has no other number to call from.

## The order, in one line

```
answering.doors(stored, declared) = the operator's rows, then every declaration whose door is free
```

A door is `(channel, number)`, as `domain/route.py` has always said. So a WhatsApp row and a phone
declaration at the same number are two doors and both survive; the collision is per door, never per
number.

## A web door is the agent, not (channel, null)

A door is `(channel, number)` and a web route carries no number, so every widget in a runtime is
the one door `("web", None)`. The registry keeps a door unique — one agent answers it at a time —
and that rule, read literally, refused the second agent of a fleet with *the door web already
answers for agent clinica-norte*. Two agents on one gateway is the whole of ms-14, so the rule was
wrong about the web, not the fleet.

**The web is not a claimable door.** Nobody dials a widget. A web caller arrives already naming its
agent, on all three paths that exist: the chat socket names it in the URL (`session/text/chat.py`,
`?agent=`), a visit token is minted FOR an agent and refused when the org answers no web door of
that name (`tokens/endpoints.py`), and the worker's router resolves a web arrival by agent
and never by door (`worker/router.py`, `_of_agent`; `_at_door` is reached only when a number was
dialled). Nothing in the runtime asks `registry.at("web", None)` — so the table was picking a
winner for a question nobody asks, and the loser was refused its register.

What identifies a web door is the agent's slug, which the registry already keys everything else on
and which `_refuse_another_orgs_slug` already keeps unique across the whole runtime, durably.

So `Registration.dialled_doors` is the **dialled doors only** — the routes whose channel is in
`CHANNELS_WITH_A_NUMBER` — and it is what `_refuse_a_taken_door` and `_claim_doors` see.
`Registration.routes` still carries every route, so an agent's channels are still read off its
declaration and `registry.routes(org)` still lists both agents' widgets. `registry.at("web", None)`
is `None` however many agents hold one.

A number is untouched: it is still refused to a second agent, in the same sentence, and an agent
that declares a widget and a number claims the number alone.

## The table

```sql
routes(org, number, agent, channel, added_at)   primary key (org, number)
```

The first column was `fleet` until 0006 renamed it: the tenant is an org now, and a route is one
org's door. `docs/decisions/orgs.md` says what an org is.

The number is the key because a number **is** one door: whoever dials it reaches one agent,
whatever channel carries it. So moving a number is an UPDATE of one row and nothing else, which is
what makes `routes add` a change with no deploy behind it — and `routes add` on a number that
already exists is a move, never a second row. `added_at` is only an order: the list comes back the
way it was typed, and a re-add leaves a number where the operator remembers seeing it.

It is a numbered migration beside `0001_call_log.sql` and `0002_api_keys.sql`, applied by the one
runner, because DDL lives in a migration and never in a string constant with a promise attached.
`tests/log/test_migrations.py` greps the whole package for `create table` and finds it in the
`.sql` files and in the runner's own bookkeeping table, which cannot be a migration because it is
what records that a migration ran.

## Where the doors are, and why

| door | who knocks | what it answers |
|---|---|---|
| `GET /v1/routes` | a worker, with its **org's API key** | the union, as `Route` objects. The org is the key's, never a parameter |
| `GET /v1/ops/routes?org=` | an operator, with the **box's ops key** | the same doors, each naming which table put it there |
| `POST /v1/ops/routes` | an operator | the stored route, and the agent it took the number from |
| `DELETE /v1/ops/routes/{number}?org=` | an operator | 204, or 404 for a number nobody typed |

The writes are `/v1/ops/*` and not `/v1/*` because they are the operator's, not the org's.
`PINECALL_OPS_KEY` was already declared in `_settings.py` and used by nothing, and
`docs/protocol/projections.md` already named the operator key as the one that reads every agent of
a runtime; CLAUDE.md's line is that everything needed to charge lives outside this repo and reaches
a runtime **only** through that API. So the routes writes are the first door of it, and the
contract is public: `docs/protocol/operator-api.md`.

The read stays where it was. A worker holds its org's API key and asks for its own doors; that
call is on the hot path of every job, and giving it a second key to carry would buy nothing. It
grew from "what the registry holds" to "the union" and `worker/client.py` did not change one line —
the hop still carries `Route`, adapted by the same pydantic adapter on both sides.

There is no cache anywhere: `GET /v1/routes` reads the table on every request, and a worker asks
before every job (`worker/entry.py`). That is what makes "no restart" true rather than eventual,
and it is one indexed read on the primary key's own prefix.

## What a clone with no database does

`routes_for(pool)` gives a `PostgresRoutes` when the process opened a pool and a `MemoryRoutes`
when it did not — which is the clone running on `PINECALL_DEV_KEY` alone, the one that must come up
before Postgres exists. It can be told a route, it answers with it, and it forgets when the process
exits. The alternative, refusing to route at all without a database, would make the first five
minutes with this repo a database installation.

## Where the pool came from

`log/store/pool.py` now holds the `Pool` Protocol and `open_pool`, and `auth/keys.py` imports them.
It had its own two-method copy; the routes table needs `fetch` and `execute`, and a second Protocol
for the same object is the litter the hygiene rules name. The driver is still spelled in
`log/store/` and nowhere else — `tests/test_isolation.py` asserts it from both sides.

## The CLI

`pinecall-runtime routes list | add | rm | seed` talks to the operator API over HTTP
(`PINECALL_GATEWAY_URL`, `PINECALL_OPS_KEY`), not to Postgres. `sessions` reads the log straight off
the database because a log is read from wherever the operator is sitting and nothing else can be
harmed by it; a route is a **write** that changes who answers a call, and the one door that may
make it is the one with the contract, the validation and the audit line. It also means a number
moves from a laptop against a box that is not this one.

`--org` defaults to `default`, the org 0006 seeds and `keys issue` mints the first key for. It
takes the org's id or its slug. `seed` reads
a JSON array of routes — each element the body `routes add` would have sent — from
`infra/seed/routes.json`, so a fresh clone answers a call without anybody typing four verbs.
