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
monthly `budget_usd` shown beside what was spent, and a new call is refused once the month's spend reaches it. `lends` says which of the
box's own vendor keys the org runs on: every one (null), none (`[]`), or named vendors and
`vendor/model` prefixes. An org that brought its own key for a vendor runs on it whatever `lends`
says. What each quota counts and when it bites: [limits.md](limits.md).

## 3. What an org consumed — the usage feed

`GET /v1/ops/usage?after=&org=&limit=`, or the same as a stream with `Accept: text/event-stream`:
one row per `call.summary` and `call.score`, each `{cursor, org, agent, call, type, at, minutes,
messages, input_tokens, output_tokens, characters, judge_calls, cost_usd}` (v1's shape, flat), and
the totals per org, which count `calls` too. The cursor is the store's position: a consumer that keeps the last one resumes
and counts nothing twice. It skips nothing either: a row's position is given as it is written, not
as it commits, so every write of a metered row takes one lock and holds it to its commit, and the
positions a feed reads come in the order their rows became visible. `cost_usd` is what the vendors charged the operator, as the providers
row's rates price it; what the operator charges is the layer's own business. An org reads its own
rows at `GET /v1/usage`.

What a call's `cost` counts, a row per unit billed:

- **the model, the ears and the voice**, as livekit metered them: tokens with their cache reads
  and writes, characters spoken, seconds heard;
- **the model that writes memory at hang-up**: its tokens join the call's usage, so they are billed
  and counted with it;
- **each leg on the phone network**: one row per leg, from when it joined the room until it
  left, in minutes begun billed whole, as a carrier bills. A leg is priced by the longest prefix
  of its number, as a carrier's own table is: the box's number when the call came in
  (`twilio-inbound/+1` is a local number, `twilio-inbound/+1800` a toll-free one), the dialled
  one when it went out (`twilio-outbound/+1907` is Alaska). The row names the prefix it matched,
  never the number. A transfer is a second leg, priced on its own.

**The apps the box hosts** are a second meter, apart from calls: the time each app served, per
UTC day, counted while a process of the org runs under one of its hosts
([protocol/hosting.md](protocol/hosting.md)). `GET /v1/ops/hosted-usage[?month=YYYY-MM]` answers
every org's month, `{since, until, rows: [{org, env, name, day, seconds}]}`, and an org reads its
own at `GET /v1/hosted/usage`. A price per app-month, prorated by those seconds, is the layer's:
Pinecall's cloud charges $5 an app a month.

The judges' tokens are on `call.score` (`judge_cost_usd`), apart from the call's. Not in any row:
a WhatsApp message (Meta charges for templates only, and the runtime answers inside the 24 hours a
person opens), a number's monthly rental, the embeddings of a lookup or a push.

The rates are the operator's, and a model or a leg without one is listed `unpriced` on the call,
never priced at zero. The repository ships `infra/box/prices.csv`, one row per model and unit
(`vendor,model,unit,usd,as_of,source`: tokens per million; characters, seconds and minutes each),
the models taken from [voice-prices](https://github.com/mahimailabs/voice-prices), Twilio's
Elastic SIP Trunking from its US page, each checked against its source on the date its row says.
They are list prices: a plan or a contract that pays less is an edit of the file, then
`pinecall-runtime providers prices infra/box/prices.csv --apply`. A trunk the box reaches only by
its address (a `sip` carrier account) is priced by rows named `sip-inbound/…` and
`sip-outbound/…` the operator writes.

The box's own compute is a row of the same file, `pinecall,pinecall-compute,minutes,<usd>`: a
call's seconds on the worker, every one counted, priced beside the vendors' rows on each call
(`provider: "pinecall"`), so a plan priced under what a call costs to run is visible. Nothing
ships priced: the number is the operator's. Each call's facts keep its cost by stage (model,
ears, voice, phone legs, the platform), and `GET /v1/insights` says per agent what a day cost by
stage and per minute.

An org that spends strangely is said so: at each seal the org's spend today is held against its
own usual day, the mean of its trailing four weeks in both worlds, and once it is three times
that (and the usual day is at least a dollar) `spend.unusual {org, day, today_usd, usual_usd,
multiple}` is written on the agent's log, once a day, and `pinecall_spend_unusual{org}` stands
on `/metrics` while it lasts, for the alert.

## 4. Where your orgs pay

`PINECALL_BILLING_URL` is answered to every org in `GET /v1/limits`: the page where it buys more.
A quota that refuses writes `credits.exhausted {org, quota, used, limit}` on the agent's log, the
moment the layer or the console can offer it.

## 5. What is not in the runtime

A plan, a price list, a trial's end date, an invoice, a card. They are the layer's, and a box run
for one's own agents needs none of them.
