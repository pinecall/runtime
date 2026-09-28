# The operator's doors — `/v1/ops/*`

Everything the person who runs the box does that no tenant may: the orgs and what each holds, its
people and keys, its limits, every number on the box, the fleet, every org's meter. The tenant's
own doors are [gateway-api.md](gateway-api.md); the box's own settings (mail, brand, sign-in) are
[the-box.md](the-box.md), and every org's floor is [the-boxs-floor.md](the-boxs-floor.md). The
CLI over these doors is [the-runtime-cli.md](../the-runtime-cli.md).

## Authentication

`Authorization: Bearer <key>`, where the key is the box's own, `PINECALL_OPS_KEY`, or the key of a
person the box made an operator (`PUT …/members/{id}/operator`). Anything else is `401`, the same
sentence for a tenant's key and a guess. `GET /v1/ops/whoami` answers `{operator: true, version,
domain, name, org}`: the console opens its Box screens when it answers 200.

An org is named by its **id or its slug** wherever a path says `{named}`.

## Orgs

- `GET /v1/ops/orgs`: every org, oldest first, `[{id, slug, name}]`.
- `POST /v1/ops/orgs {slug, name?}`: a new org, its id minted here, born with what the box's
  admission gives one in each world; `409` for a slug taken, `400` for one that is no slug.
- `GET /v1/ops/orgs/{named}`: the org as it stands, `{id, slug, name, quotas: {production, sandbox},
  dialling, holding: {memory_facts, knowledge_chunks, numbers, seats}}`. `holding` is what it has
  now, across both worlds; usage is a fold of the log and lives at `/v1/ops/usage`.
- `DELETE /v1/ops/orgs/{named}`: `204`; `409` while a live key or a route still names it.
- `PUT /v1/ops/orgs/{named}/agents {agent}`: an agent registered with the wrong org's key moved
  into this one, its logs and its numbers with it: `{agent, org, logs, numbers, stayed}`. `409`
  while somebody holds it, `404` for a slug that never wrote a log; a number the org already
  answers at stays where it was and is named.

## Quotas and dial guards

`PUT /v1/ops/orgs/{named}/quotas {env, quotas: {limits: {minutes, messages, agents,
concurrent_calls, memory_facts, knowledge_chunks, numbers, seats, llm_tokens}, budget_usd, lends}}`
replaces the org's limits **in one world**, whole: a limit left out is no limit, `lends` null lends
every key the box holds, `[]` none, else vendors or `vendor/model`. They bite the next call and
the next register. `budget_usd` is whole dollars a calendar month, shown beside what was spent,
never refused over. `PUT /v1/ops/orgs/{named}/dialling {dial_anywhere?, per_minute?, per_day?,
max_duration_s?}` replaces the dial guards whole; one left out is the default. What the limits
mean is [limits.md](../limits.md).

## People

- `GET /v1/ops/orgs/{named}/members`: `{members, seated}`.
- `POST /v1/ops/orgs/{named}/members {email, name, role, agents?, production?}`: a person invited
  into the org, taking no seat of its plan, the link's token answered this once and mailed when
  the box can mail. How an org gets its first admin on a box that takes no sign-up.
- `PUT /v1/ops/orgs/{named}/members/{id}/operator {operator}`: whether the member runs the box;
  false takes it back at once. Here and not on the tenant door, or an admin could make itself one.
- `DELETE /v1/ops/orgs/{named}/members/{id}`: out for good, keys revoked; `409` for the last
  active admin.
- `GET /v1/ops/orgs/{named}/sso`: the org's provider, never its secret.
  `PUT /v1/ops/orgs/{named}/sso/required {required}` is the break-glass: `false` lets a password
  open the org again while its provider is down. Never the other way; requiring the provider is the
  org's own call.

## Keys

- `POST /v1/ops/orgs/{named}/keys {env, label?, scopes?, subject?, name?}`: a key of the org in
  the world named, `pc_live_` or `pc_test_`, answered this once; every scope but `fleet` when
  `scopes` is left out; `subject` and `name` make it a person's key.
- `GET /v1/ops/orgs/{named}/keys`: every key by fingerprint, the revoked ones said; never a key.
- `POST /v1/ops/keys/{fingerprint}/revoke`: stops the key from the next request; the row stays, so
  the entries that name it still read.

## Provider keys

`GET /v1/ops/orgs/{named}/provider-keys`, `PUT …/provider-keys/{vendor} {key | credentials}`,
`DELETE …/provider-keys/{vendor}`: the org's own vendor keys, set for it by the operator, the same
rows the org sets at `/v1/provider-keys` ([provider-keys.md](provider-keys.md)).

## Routes

`GET /v1/ops/routes?org=&env=` (production unless asked), `POST /v1/ops/routes {org, number, agent,
channel, env}` (one row per number per org: added again, it moves), `DELETE /v1/ops/routes/{number}?org=`
(`404` for a number nobody typed). A number an org imported through its own carrier is
[numbers.md](numbers.md); these are the operator's rows.

## The fleet

`GET /v1/ops/fleet`: `{now, stale_after_s, workers: [{fleet, worker, active, max_jobs, load,
draining, cordoned, seen_at}], totals: [{fleet, workers, active, seats, free, accepting, full}]}`,
every worker heard from in the last hour, both fleets, and each fleet summed over the workers heard
from in the last 30 s. `POST /v1/ops/fleet/{worker}/cordon?fleet=` and `DELETE …/cordon`: the
worker is told on its next heartbeat, takes no new call, finishes what it holds and leaves;
`404` for a name nobody has. The loop that grows and shrinks a fleet is [scaling.md](../scaling.md).

## Usage

`GET /v1/ops/usage?after=&limit=&org=`: every org's metered rows after the cursor, `{rows, totals:
{org: totals}, next}`, one row per `call.summary` and `call.score` folded; the cursor is the store's
position of the last row read, so a billing consumer resumes and counts nothing twice. With
`Accept: text/event-stream` the same rows stream, `id:` the cursor, then new ones as they land.
An org reads its own at `GET /v1/usage`.
