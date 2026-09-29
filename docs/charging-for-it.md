# Charging for it: a billing layer on top of your runtime

The runtime charges nobody. It holds no plan, no price and no card: whoever runs a box and wants to
bill for it writes a **billing layer** beside it, and the runtime gives that layer mechanisms to
steer. With nothing set, a box has no limits and runs every call on its own vendor keys, which is
what a box run for one's own agents wants.

```
 ┌───────────────────── the runtime ─────────────────────────────────────────┐
 │  admission   what a NEW org is given, per world         /v1/ops/admission │
 │  quotas      what an org may use and keep, per world    /v1/ops/orgs/{org}/quotas │
 │  lends       which of the box's vendor keys it runs on  (a quota)          │
 │  usage       every call's consumption, with a cursor    /v1/ops/usage      │
 │  the log     credits.exhausted when a quota refuses                        │
 └───────────▲────────────────────────────────────────────────────────────────┘
             │ the ops key, over HTTP, when money moves
 ┌───────────┴───────────── your billing layer ──────────────────────────────┐
 │  plans → quotas and lends · usage → invoices · a payment failed → quotas  │
 └────────────────────────────────────────────────────────────────────────────┘
```

Keep the payment provider **out of process**: a webhook, a retry or an outage at the provider must
never fail a call. The runtime decides every call from rows it already holds, and your layer writes
those rows when money moves.

## 1. What a new org is given — admission

The `admission` row of the box's settings, `PUT /v1/ops/admission`: `{first: {production: quotas,
sandbox: quotas}, later: {…} | null}`. `first` is what a person's first org gets in each world;
`later`, when set, what any later org of the same person gets, which is how "one trial per person"
is said. It is read at the moment an org is made, by sign-up or by the operator; an org already made
keeps what it has. A box with no row gives a new org no limits.

## 2. What an org may use — quotas and lends

Per org and per world, set whole at `PUT /v1/ops/orgs/{org}/quotas`: minutes, messages, agents
held, calls at once, memory facts, knowledge chunks, bought numbers, seats, model tokens, and a
monthly `budget_usd` shown beside what was spent and never refused over. `lends` says which of the
box's own vendor keys the org runs on: every one (null), none (`[]`), or named vendors and
`vendor/model` prefixes. An org that brought its own key for a vendor runs on it whatever `lends`
says. What each quota counts and when it bites: [limits.md](limits.md).

## 3. What an org consumed — the usage feed

`GET /v1/ops/usage?after=&org=&limit=`, or the same as a stream with `Accept: text/event-stream`:
one row per `call.summary` and `call.score`, each `{cursor, org, agent, call, type, at, used:
{calls, minutes, messages, input_tokens, output_tokens, characters, judge_calls, cost_usd}}`, and
the totals per org. The cursor is the store's position: a consumer that keeps the last one resumes
and counts nothing twice. `cost_usd` is what the vendors charged the operator, as the providers
row's rates price it; what the operator charges is the layer's own business. An org reads its own
rows at `GET /v1/usage`.

## 4. Where your orgs pay

`PINECALL_BILLING_URL` is answered to every org in `GET /v1/limits`: the page where it buys more.
A quota that refuses writes `credits.exhausted {org, quota, used, limit}` on the agent's log, the
moment the layer or the console can offer it.

## 5. What is not in the runtime

A plan, a price list, a trial's end date, an invoice, a card. They are the layer's, and a box run
for one's own agents needs none of them.
