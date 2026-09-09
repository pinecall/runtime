# orgs — the tenant is a row, the key is the tenant, the log says whose everything is

Written 2026-09-08, the seam of ms-11. Before it, a tenant was the word `fleet` on a key, on a
route and on a token — checked at every door and created nowhere, so whatever `keys issue --fleet`
typed was a tenant. Now a tenant is a row in `orgs`, every row that carried a fleet carries its org,
and a log — a call's, an agent's — knows which org it belongs to. Nothing here prices anything.

## The request path, in one picture

```
  key ──► org ──► quota check ──► call ──► call.summary ──► usage
   │        │          │            │            │             │
   │        │          │            │            │             └─ GET /v1/ops/usage?after=<cursor>
   │        │          │            │            │                a projection over the log, per org,
   │        │          │            │            │                resumed from the store's position
   │        │          │            │            └─ written by the session at hang-up: minutes,
   │        │          │            │               turns, one usage row per model, the cost
   │        │          │            └─ POST /v1/calls · WS /v1/chat · POST /v1/tokens
   │        │          │               the log is claimed for the org (call_log_head.org)
   │        │          │               and served under it (Live.running counts it)
   │        │          └─ Admission.a_call: quotas row vs the meter's totals and the calls open
   │        │             refused → credits.exhausted in the agent's log, then 429 / close
   │        └─ KeyRecord.org: an API key IS the org; no door takes an org from a parameter
   └─ sha256 in api_keys, found by hash; PINECALL_DEV_KEY is the default org's
```

Every arrow is one module: `auth/keys.py` (key → org), `orgs/admission.py` (the check),
`orgs/meter.py` (the fold), `log/usage.py` (what one row is), `orgs/usage_endpoint.py`
(the door). The two tables — `orgs` and `quotas` — are in `migrations/0006_orgs.sql` and read
through `orgs/table.py`.

## An org has an id and a slug, and they are different things

`orgs(id, slug, name, created_at)`. The **id** is minted (`org_` + 12 hex) and never changes: every
row of the tenant's names it — its keys, its routes, its tokens, its logs' head rows — and a word
that every row names must not be a word anybody types wrongly or wants to rename. The **slug** is
the word an operator types and a URL carries: lowercase, digits, dashes. Every operator door takes
either, and `orgs/table.py:an_org` is the one place the word becomes a row; a door that
typed the resolution itself would be a door that could skip it.

The exception is the tenants 0006 migrated: their id is the word the old `fleet` column held, so a
key issued to fleet `default` is a key issued to org `default` with no reissue — which is what
"existing fleets migrate without a manual step" means. The default org is the first row.

`PINECALL_DEV_KEY` is the default org's key. A laptop is one tenant; a clone that has migrated its
database has logs owned by `default`, and the dev key must read them back. It was `dev` before, and
`dev` would have been refused its own history the morning after 0006.

## Whose log this is, lives on the log

`call_log_head.org` is set once, at the moment a door opens the call (`Store.owned`), and for an
agent's own log at the moment a key registers the slug. The first claim stands and nothing moves a
log: `owned` is `coalesce(head.org, excluded.org)`.

This is what makes two things true at once:

- **A slug is one org's.** `Registry._refuse_another_orgs_slug` asks the agent log's owner, not
  the live table: an org that registered `clinica-norte` last month still owns it today with no
  socket open, and a second org picking the same word is refused before it writes a line into the
  first one's log. The live check that used to do this (`_refuse_another_fleet`) is gone — one
  rule, and it is the durable one.
- **An org never reads another's log.** `api/calls/sink.py:refuse_another_org` is asked by every
  door that reads a log by an API key — the page, the stream, the state, the sessions list, the
  attach socket, the eval replay and the runs list — and answers 403 in one sentence, never 404:
  whether that call exists is not another tenant's business. A log nobody has claimed is empty,
  and reading empty leaks nothing.

Logs written before 0006 are backfilled to `default`: a box had one tenant by construction. A box
that had two fleets and calls under both has its history under `default` and its keys under the
right orgs, which is the honest answer to a question the old schema could not have answered.

## Quotas are a mechanism; NULL is the number a self-hosted box has

`quotas(org, minutes, messages, agents, concurrent_calls, set_at)`, one row per org, replaced whole
by `PUT /v1/ops/orgs/{org}/quotas`. A limit left out is NULL, and NULL is no limit. A self-hosted
box never sets a row; the cloud that charges sets the numbers through the operator API and never
touches the mechanism. Zero is a real limit and refuses everything.

The check is `Admission`, and it reads the quotas table and the meter — never the gateway's live
tables. What is a live fact is handed in by the door that has it: `Live.running(org)` for calls at
once, `Registry.holding(org)` minus the slug being registered for agents. So admission imports
nothing of connected.py and the app socket imports nothing of the text channel, which is the
isolation the layout tests hold.

The order at a call door: the org check (403), the token spend (a token opens one call), then
admission — concurrent calls first because it is free, then minutes and messages only when a limit
is set on either, because those ask the meter. A refusal is written to the **agent's own log** as
`credits.exhausted` — the org's log is its agents' logs — before the door says no, and the door's
sentence is the same one: `org clinica has used 2 of its 2 minutes: credits.exhausted`. The
worker's `POST /v1/calls` hears a 429, the chat socket hears it as the close reason, the token door
answers 429 before a browser ever joins the room, and `agent.register` hears it as the `error`
frame after the entry.

Concurrent calls are counted from the process's memory and not from `sealed` on the head row: a
worker that died mid-call never seals its log, and a quota that counted it would refuse the org
for ever.

Eval runs open text calls through the same session and write call.summary rows, so they **count**
in usage; they are not **gated**, because they are the tenant's own tests and not the public's
calls. A tenant that burns its minutes on evals sees the minutes in usage and the next real call
refused, which is the right order.

## Usage is a projection, never a table

`GET /v1/ops/usage?after=<cursor>&org=<id or slug>` folds the log's own `call.summary` and
`call.score` rows — `log/usage.py:METERED_TYPES`, and nothing else is metered — into one row each:
minutes and turns and the model rows' tokens and characters off the summary, the judge's questions
off the score, and the provider cost as the log knows it, informational. The **cursor** is the
store's position across every log (`call_log.position`, 0006), because a seq counts one log and a
projection over every org's calls cannot resume from one. `next` moves past every row read,
filtered or not, so a cloud asking for one org never re-reads everybody else's page. SSE is the same
rows as they land, polled from the store every second; a summary lands in some call's log, not on
a topic, and there is no fanout to wait on.

`Meter` is the same fold kept per process for the quota check: one cursor, totals per org, caught
up through the runtime's one paging loop (`log/replay.py:pages`, now generic over the cursor) every
time somebody asks. A restart folds from zero and lands on the same numbers, which is the whole
argument for a projection: the log is the truth and this is arithmetic over it.

## What was deliberately not built

**Billing.** No plan, no price, no credit. `prices.py` is provider cost in the log; `cost_eur` in a
usage row is that number carried through, and the runtime never turns it into an invoice.

**Provider keys per org.** Landed in the card after this one, on the seam this one left:
`provider_keys(org, vendor, ciphertext)`, the org's own key for a vendor or the box's when it
brought none. See [provider-keys.md](provider-keys.md).

**A rename of the slug.** The id exists so that it can happen; the door that does it lands with
the operator API card, which owns `/v1/ops/*` as a public contract.

**An org-level log.** `credits.exhausted` goes to the agent's log because that is where the tenant
is already reading. A log per org would be a third kind of log, and nothing else needs one yet.
