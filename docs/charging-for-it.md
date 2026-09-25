# Charging for it: a billing layer on top of your runtime

The runtime charges nobody. It holds no plan, no price and no card, and it never will: whoever runs
a box and wants to bill for it writes a **billing layer** beside it, and the runtime gives that
layer mechanisms to steer. With nothing written, a box has no limits and runs every call on its
own vendor keys, which is what a box run for one's own agents wants.

This is how Sentry is cut from getsentry: the open package holds every mechanism, a private one
plugs policy in, and the door that enforces a limit never learns who set it.

```
 ┌──────────────────── the runtime (this repository) ─────────────────────┐
 │  Quotas        what an org may consume and keep            limits.md    │
 │  Lends         which of the box's vendor keys it runs on   limits.md    │
 │  Admitting     what a NEW org may do: your policy, in process           │
 │  Usage         every call's consumption, with a cursor     GET /v1/ops/usage │
 │  Ops doors     set an org's quotas and lends               PUT /v1/ops/orgs/{org}/quotas │
 │  The log       credits.exhausted when a quota refuses                    │
 └───────────▲────────────────────────────────────────────┬───────────────┘
             │ the ops key, over HTTP                       │ PINECALL_EXTENSIONS, in process
             │ (plans, payments: asynchronous)              │ (the one synchronous decision)
 ┌───────────┴──────────────────── your billing layer ─────────────────────┐
 │  plans → quotas and lends · usage → your invoices (Stripe, Paddle, …)   │
 │  a payment that failed → quotas that refuse                              │
 └──────────────────────────────────────────────────────────────────────────┘
```

Keep the payment provider **out of process**. A webhook, a retry or an outage at the provider must
never be able to fail a call; the runtime decides every call from rows it already holds, and your
layer writes those rows when money moves.

## 1. What a new org may do — `Admitting`

A package named in `PINECALL_EXTENSIONS` (comma separated) is imported when the gateway starts and
handed the extension points to fill:

```python
# my_billing/__init__.py — installed in the runtime's venv, named in PINECALL_EXTENSIONS
from pinecall.types import PRODUCTION, Env, Org, Quotas

TRIAL = Quotas(
    minutes=30,
    messages=300,
    concurrent_calls=1,
    lends=frozenset({"deepgram", "cartesia", "anthropic/claude-haiku-4-5"}),
)
CLOSED = Quotas(minutes=0, messages=0, lends=frozenset())


def admitted(org: Org, email: str, world: Env, already: int) -> Quotas:
    """A person's first org: a trial in the sandbox. Production, or a second org: closed."""
    return TRIAL if world != PRODUCTION and already == 0 else CLOSED


def register(extensions) -> None:
    extensions.admitted = admitted
```

`already` is how many orgs that email already belongs to on this instance, the new one not
counted: a trial given only where it is `0` is one trial per person, not one per org they make.
A sign-up proves its address with a six-digit code first (`POST /v1/signup/verify`), so a policy
is asked only for an email somebody answered for. Each instance asks it **once, in its own world**, the moment it makes an org it did not have:
production at signup (`POST /v1/signup`, when `PINECALL_SIGNUP` is on), a sandbox the first time
it mirrors the org from production. An org already there is never asked about again. A name in
`PINECALL_EXTENSIONS` that does not import stops the gateway from starting — a box told to load a
policy never runs without one. With no package, the answer is `Quotas()`: no limit, no row.

The package is installed into the runtime's own environment (`/opt/pinecall/venv` on a box). On a
box deployed with `make deploy`, name its checkout in `deploy.local.mk` (`EXTENSIONS_SRC =
../my_billing`): the deploy carries it and installs it after `uv sync --frozen`, which would
otherwise remove it as a package its lock does not name (`infra/box/README.md`, "An extension").

## 2. What an org may use — quotas and lends

The whole vocabulary is `Quotas` ([limits.md](limits.md)): `minutes`, `messages`, `llm_tokens`,
`agents`, `concurrent_calls`, `memory_facts`, `knowledge_chunks`, `numbers`, `seats` — `null` is
no limit, `0` is a real one — plus `budget_eur`, shown and never enforced, and `lends`: which of the box's
vendor keys the org's calls may run on where it brought none of its own (a vendor, or
`vendor/model` read as a prefix; `null` all, `[]` none). An org's own key is never refused, for
any model: bringing keys is how an org leaves your vendor bill.

When a plan changes — a payment, an upgrade, a cancellation — your layer replaces the org's row:

```http
PUT https://<gateway>/v1/ops/orgs/<org>/quotas
Authorization: Bearer <PINECALL_OPS_KEY>

{ "minutes": 2000, "messages": 20000, "concurrent_calls": 5,
  "lends": ["deepgram", "cartesia", "anthropic"] }
```

The whole row is replaced: a field left out is no limit. It bites the next call. A box with two
instances holds two rows per org, one in each database: write both, with each instance's ops key.

What the org sees when a quota refuses: the door answers `429` with one sentence
(`org clinica has used 30 of its 30 minutes: credits.exhausted`), and `credits.exhausted` lands in
the agent's own log first, so a tenant reading its log knows why a call never rang. A model the
box does not lend is refused before the call opens, in a sentence that names `pinecall providers
add`, and `422` when a person picks it in the settings.

## 3. What an org consumed — the usage feed

```http
GET https://<gateway>/v1/ops/usage?after=<cursor>[&org=<org>]
Authorization: Bearer <PINECALL_OPS_KEY>
```

answers the metered rows above the cursor — one per `call.summary` and `call.score` in any
org's log — each with its org, agent, call, minutes, messages, tokens, characters, judge calls
and `cost_eur` (the vendors' price as the runtime knows it, never yours), and `next`, the cursor
to ask from. With `Accept: text/event-stream` it is a stream that never ends. Keep the cursor on
your side and ask from it: nothing is ever counted twice, and a restart of either side resumes
where it stopped. The rows are the log, so there is no table to drift from what happened.

Map them onto your provider's meter (Stripe's Meter Events take an idempotency key: use the call
id and the row's type). Minutes are the call's own; simulations write a `call.summary` like any
call and count the same.

## 4. Where your orgs pay

Set `PINECALL_BILLING_URL` to your plans page. `GET /v1/limits` answers it to every key of an org
beside the org's quotas, and the console links it from the free-minutes meter and from the notice
a refused call leaves; `/.well-known/pinecall` carries it for a page nobody has signed into yet.
Unset, nothing of it is drawn.

## 5. What is not in the runtime yet

Said here so a layer is not built on a mechanism that does not exist:

- **Who paid each component.** A row's `cost_eur` is the call's whole vendor cost; whether the
  box's key or the org's own carried each vendor is not on the row yet, so a pass-through price
  cannot yet leave out what the org paid itself.
- **Periods.** The quotas are counted over the org's whole life. A monthly allowance is your layer
  replacing the row when the month turns.
- **A suspension of its own.** Suspending an org is replacing its row with zeros.

Each lands with its page; the [CHANGELOG](../CHANGELOG.md) says when.
