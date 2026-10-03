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

## What the box runs, and what a new org is given

Four rows of `box_settings`, each read and written whole, the console's box screens their editor:

- `GET` · `PUT /v1/ops/providers`: the providers row: the default vendor and model of each stage,
  the models a vendor named alone runs, the voice per vendor and language, what each vendor is told
  (`tuning`), the language hints, the rates a call is priced at in dollars, the judge model and what
  judging one call may spend on it (`judge.ceiling_usd`: a model judge past it is `skipped`), the
  embedder. A vendor not installed, or not doing the stage it is named for, is `400` where it is
  written. No vendor is listed in code: every livekit plugin installed is one.
  How a spoken turn ends is the ears' `tuning` too, `"stt/<vendor>": {ends_the_turn, turn_model}`:
  ears with `ends_the_turn` close the turn themselves; the others get a local model that reads it
  off the caller's audio on the worker's CPU, no transcript: `turn_model` `v1-mini` (livekit's own,
  the default) or `smart-turn-v3` (Daily's Smart Turn v3, 23 languages; its 8 MB of weights come
  from Hugging Face on the first call that asks for it).
  A default stage may name who takes over, in order: `"defaults": {"llm": {"vendor", "model",
  "fallbacks": [{"vendor", "model"}]}}`. Each is checked as the default is; a fallback names none
  of its own, the judge names none, and the ears' fallbacks end the turn as the default does
  ([provider-keys.md](provider-keys.md#when-a-vendor-fails)).
- `GET /v1/ops/provider-keys`, `PUT` · `DELETE /v1/ops/provider-keys/{vendor} {key | credentials}`:
  the box's own vendor keys, sealed and never read back. Offering a vendor is holding its key; an
  org runs on it where its `lends` allow.
- `GET` · `PUT /v1/ops/admission`: what a newborn org is given in each world, `{first: {env:
  quotas}, later: {env: quotas} | null}`: `first` for a person's first org, `later`, when set, for
  any other (one trial per person). Read when an org is made; a box that never said gives none.
- `GET` · `PUT /v1/ops/fleets`: the fleet of workers each world's calls are dispatched to,
  `{production, sandbox}`.

## Orgs

- `GET /v1/ops/orgs`: every org, oldest first, `[{id, slug, name}]`.
- `POST /v1/ops/orgs {slug, name?}`: a new org, its id minted here, born with what the box's
  admission gives one in each world; `409` for a slug taken, `400` for one that is no slug.
- `GET /v1/ops/orgs/{named}`: the org as it stands, `{id, slug, name, quotas: {production, sandbox},
  dialling, holding: {memory_facts, knowledge_chunks, numbers, seats}}`. `holding` is what it has
  now, across both worlds; usage is a fold of the log and lives at `/v1/ops/usage`.
- `DELETE /v1/ops/orgs/{named}`: `204`; the org erased whole: every log it owns, every recording,
  then the org and what cascades from it, with one row in the erasure trail that outlives it
  ([gateway-api.md](gateway-api.md) §Erasing). `409` while a live key or a route still names it.
- `PUT /v1/ops/orgs/{named}/agents {agent}`: an agent registered with the wrong org's key moved
  into this one, its logs and its numbers with it: `{agent, org, logs, numbers, stayed}`. `409`
  while somebody holds it, `404` for a slug that never wrote a log; a number the org already
  answers at stays where it was and is named.

## Quotas and dial guards

`PUT /v1/ops/orgs/{named}/quotas {env, quotas: {limits: {minutes, messages, agents,
concurrent_calls, memory_facts, knowledge_chunks, numbers, seats, llm_tokens, hosted_apps},
budget_usd, lends}}`
replaces the org's limits **in one world**, whole: a limit left out is no limit, `lends` null lends
every key the box holds, `[]` none, else vendors or `vendor/model`. They bite the next call and
the next register. `budget_usd` is whole dollars a calendar month, both worlds together, shown
beside what was spent; a new call is refused once the month's spend reaches it. `PUT /v1/ops/orgs/{named}/dialling {dial_anywhere?, per_minute?, per_day?,
max_duration_s?}` replaces the dial guards whole; one left out is the default. What the limits
mean is [limits.md](../limits.md).

## People

- `GET /v1/ops/orgs/{named}/members`: `{members, seated}`.
- `POST /v1/ops/orgs/{named}/members {email, name, role, agents?, production?}`: a person invited
  into the org, taking no seat of its plan, the link's token and the `link` itself (on the box's
  public name) answered this once, and mailed when the box can mail. How an org gets its first admin on a box that takes no sign-up.
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
  the world named, `pc_live_` or `pc_test_`, answered this once; every scope but `fleet`,
  `runner` and `join` when `scopes` is left out; `subject` and `name` make it a person's key.
- `GET /v1/ops/orgs/{named}/keys`: every key by fingerprint, the revoked ones said; never a key.
- `POST /v1/ops/keys/{fingerprint}/revoke`: stops the key from the next request; the row stays, so
  the entries that name it still read.
- `POST /v1/ops/fleet/join-tokens {fleet, worker}` → `{token, url, expires_at}`: a key of scope
  `join` named for a machine the fleet loop is about to make, ten minutes, spent at
  `POST /v1/fleet/join {worker}` for the machine's own fleet key, the LiveKit pair and the store's
  secret (`infra/fleet/README.md`); 404 when no world's calls go to the fleet.
- `DELETE /v1/ops/fleet/{worker}/keys`: the machine's fleet key and any join token it never spent
  revoked, as the loop does when it deletes the machine.

## Provider keys

`GET /v1/ops/orgs/{named}/provider-keys`, `PUT …/provider-keys/{vendor} {key | credentials}`,
`DELETE …/provider-keys/{vendor}`: the org's own vendor keys, set for it by the operator, the same
rows the org sets at `/v1/provider-keys` ([provider-keys.md](provider-keys.md)).

## Traceback

`GET /v1/ops/traceback?number=<E.164>&since=<epoch>` answers a carrier's traceback, of every org:
`{number, since, calls, dials}`. `calls` is every phone call with the number, oldest first, as
`{call, org, env, direction, from_number, to_number, started_at, ended_at, end_reason, erased}`:
a call still kept, from its facts, or an erased one, from the detail record its erasure left
(`erased: true`; the nightly run forgets it 24 months on). `dials` is every
dial to the number, placed or refused, forgotten by the same run after 24 months:
`{org, env, agent, call, shown, asked_by, refused, at}`.
`since` defaults to 24 months back. The terminal's twin is `pinecall-runtime traceback`.

## Routes

`GET /v1/ops/routes?org=&env=` (production unless asked), `POST /v1/ops/routes {org, number, agent,
channel, env}` (one row per number per org: added again, it moves) and `DELETE
/v1/ops/routes/{number}?org=` (`404` for a number nobody typed) are the operator's rows, written
with `origin: typed`. A row is admitted on the SFU when it is written, the way a hooked number is
(the org's trunk, fenced to Twilio's networks, and its world's rule), and let go of there when it is
deleted; a number another org's trunk already lists is `409` and nothing is written. The carrier is
never touched: a typed row rings only where the carrier already sends the number to this box. A
number an org brings itself is [numbers.md](numbers.md).

`GET /v1/ops/numbers` is every route of the box at once, every org and both worlds, by number: what
a call to each number would do now. A row is `{number, channel, org, env, agent, came_in, running,
answered_by}`: `org` is the slug; `came_in` is `bought` (on the box's own account), `twilio`, `sip`
or `whatsapp` (imported from an account of the org of that kind), `imported` (from an account since
forgotten), `hooked` (the org pointed the number at the box itself) or `typed` (a row an operator
wrote here or with `routes add`/`routes seed`), read from the row's `origin`; `running` says a
process holds the agent in that org and world now, so a call is picked up; `answered_by` names the
org whose older row answers the number instead of this one (two orgs typed it), and is null when
this one does. Changing a number is the org's own door, which writes the carrier, the SFU and the
row together.

## Carriers and the fence

The box knows a catalog of carriers (`channels/telephony/carriers.csv`: each one's published SIP
signalling networks, the page they were read from and the day). `GET /v1/ops/carriers` is
`{carriers, fence}`: each carrier `{kind, name, control, networks, source, read_on, admitted, fixed,
numbers}` (`control` when the box drives its API, `fixed` for Twilio, the box's own carrier,
admitted always, `numbers` the numbers of every org that reach the box through it), and the fence,
`{openings: [{network, reason}], applied_at, applied}`. `PUT /v1/ops/carriers/{kind} {admitted}`
admits a carrier or stops: admitted, every org sees it in `GET /v1/carriers/catalog` and may hook
numbers `via` it; `409` for Twilio off, `404` for a kind the catalog lacks.

What an org declares beyond the catalog (a SIP peer's `addresses`, a hooked number's `networks`)
waits for the operator: `GET /v1/ops/carrier-networks?state=waiting|approved|refused` lists each
`{id, org, source, network, state, asked_at, decided_by, decided_at}`, and
`POST /v1/ops/carrier-networks/{id}/approve` or `…/refuse` answers it, the org's trunks made to
follow at once (a number fenced by nothing approved is taken off them). A network wider than a
`/24`, or not public, never reaches the list: `PUT /v1/carrier` and `POST /v1/numbers` refuse it.

The fence is written by `pinecall-fence apply`, as root, every minute
(`pinecall-fence.timer`): the admitted carriers' networks and the approved ones, each checked again
(none wider than a `/16` from the catalog or a `/24` from an org), into
`/etc/pinecall/nftables.d/carriers.nft`, which `nftables.conf` reads beside Twilio's own set; the
gateway never runs `nft`. On a GCP box the cloud's firewall stands in front: its rule
`pinecall-runtime-sip` (priority 500) admits `carrier_signalling`, and `pinecall-runtime-sip-deny`
(600) denies everyone else's 5060, ending a flow the allow no longer covers. A network beyond
Twilio's reaches that rule when the operator runs, from a laptop,
`ssh <box> sudo pinecall-runtime fence export > infra/terraform/environments/production/carrier_signalling.auto.tfvars.json`
and `make tf-apply` (the file is the orgs' addresses and is never committed). Until then it is open
on the host and closed in the cloud.

## The fleet

`GET /v1/ops/fleet`: `{now, stale_after_s, workers: [{fleet, worker, agent_name, active, max_jobs, load,
draining, cordoned, seen_at, ended, failed, errors, turns, first_audio_p95_s}], totals: [{fleet, workers, active, seats, free, accepting, full}]}`,
every worker heard from in the last hour, both fleets, and each fleet summed over the workers heard
from in the last 30 s. `POST /v1/ops/fleet/{worker}/cordon?fleet=` and `DELETE …/cordon`: the
worker is told on its next heartbeat, takes no new call, finishes what it holds and leaves;
`404` for a name nobody has. The loop that grows and shrinks a fleet is [scaling.md](../scaling.md).

`POST /v1/livekit/webhook` is LiveKit's own door, not the operator's: `livekit.yaml` sends it every
room event, signed with the box's LiveKit key (`Authorization: <token>`, the body's sha256 in the
token; anything else is a `403`), and it answers `204` to all of them. One event is acted on: an
**agent lost** mid-call, `participant_left` of kind `AGENT` whose `disconnect_reason` says its
connection was lost (`SIGNAL_CLOSE`, `CONNECTION_TIMEOUT`, `STATE_MISMATCH`, `JOIN_FAILURE`,
`MEDIA_FAILURE`, `AGENT_ERROR`), in a room whose call is open, has no `call.ended`, still holds a
person and no agent. The gateway then writes `call.ended {reason: "drained", ended_by: "platform"}`
once (the head row locked: a second delivery finds it written and does nothing), writes
`callback.requested` on the agent's log for a phone call, and dispatches the call's world's fleet
into the room with `worker_gone: true` and `entries_written`, how many entries the log took from
the dead worker's writer, where the job's own writer follows on. That job says `PINECALL_OVERFLOW_SAYS`, deletes the room
and seals the call. A worker that ends a call, drains or hands a ring to the sandbox leaves with
`CLIENT_INITIATED`, and a deleted room says `ROOM_DELETED`: neither is acted on.

## Usage

`GET /v1/ops/usage?after=&limit=&org=`: every org's metered rows after the cursor, `{rows, totals:
{org: totals}, next}`, one row per `call.summary` and `call.score` folded; the cursor is the store's
position of the last row read, so a billing consumer resumes and counts nothing twice, and skips
nothing: the positions of metered rows are given in the order they commit. With
`Accept: text/event-stream` the same rows stream, `id:` the cursor, then new ones as they land.
An org reads its own at `GET /v1/usage`.

`GET /v1/ops/hosted-usage[?month=YYYY-MM]`: the time every org's hosted apps served, both worlds,
one row per app and UTC day, `{since, until, rows: [{org, env, name, day, seconds}]}` — what a
billing layer charges hosting on ([hosting.md](hosting.md)).
