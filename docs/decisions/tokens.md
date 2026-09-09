# tokens — LiveKit's endpoint, our three things in front of it, and where "once" is kept

Written 2026-09-08 for tk-83adc3, after the LiveKit doc audit that rewrote the card. The public
contract is `docs/protocol/tokens.md`; this page is why it is shaped the way it is.

## We did not design a token format

The first spec of this card designed one: `POST /v1/tokens {scope, agent, metadata, ttl}` with a
Pinecall response. The audit found that LiveKit publishes a standard token endpoint and that every
client SDK — `livekit-client`, Swift, Kotlin, Flutter, React Native — already carries a
`TokenSource.endpoint` that speaks it, with caching and refresh built in. A format of our own would
have needed a client of our own on every platform, which is the thing this milestone's first rule
forbids. So the door is LiveKit's shape at LiveKit's status code, and a stock client joins with the
answer. What is ours is exactly what LiveKit's page leaves to the implementer: the authentication
in front of it, which fields a client may not set, and what goes into the token.

`tokens/` is four files, one idea each: `endpoints.py` the door, `room.py` what the token
tells the room, `ledger.py` where "once" is kept, `spending.py` where it is spent. The mint itself
is not there: `auth/scopes.py:a_room_token` was already the one place a LiveKit `AccessToken` is
built, and it grew the three parameters a talk token needs — the participant metadata, the
attributes, the room config — instead of a second minter beside it.

## The agent rides the dispatch, and the dispatch rides the token

The worker's router reads `job.metadata` first and resolves the agent named there without waiting
for a SIP leg (`worker/router.py`). A token with a `roomConfig.agents[0]` makes LiveKit create
exactly that dispatch when the participant's join creates the room — the "dispatch via token"
half of LiveKit's agent-dispatch page — so the agent, the scope, the visitor and the tenant's
sealed JSON all reach the worker through the one field a dispatch already has. It is inside the
JWT, signed with the rest: a browser can decode it and cannot change it. That is the whole of what
"sealed metadata" meant in the design, with no signing code of ours.

The dispatch names our worker pool by `agent_name`, and that word had to be spelled once. It was
`worker/main.DEFAULT_FLEET = "pinecall"` — a twin of `auth/keys.DEFAULT_FLEET = "default"`, which is
a *tenant's* fleet and a different idea with the same name. It is now `domain/dispatch.py:
WORKER_NAME`, beside the metadata keys the router reads (`agent`, `direction`, `caller`) and the
two ours adds (`scope`, `metadata`). The gateway writes those keys and the worker reads them; a
package both hold is where a word both spell belongs, and `types/` holds no framework because
these are strings.

The tenant's sealed JSON sits under its own key, `metadata`, and not flattened beside `agent` and
`scope`. `CallContext.metadata` is the whole dispatch today, as it was before this card; the card
that writes `call.metadata` to the log picks the one key out. Two namespaces in one dict would have
needed a rule about which keys a tenant may not use, and a rule is more than a key.

## `agentName` is how a stock client names the agent

A `TokenSource.fetch({ agentName })` has no place for a Pinecall field: the SDK packages the name
into `room_config.agents[0].agent_name` and sends LiveKit's body. So the door reads the agent from
`agent` in the body — what a tenant's backend writes — and, absent that, from the room config the
client packaged. protobuf's own `ParseDict` reads either spelling of the key, so neither is spelled
here, and a room config that is not one is refused in the parser's words. The rest of the client's
`room_config` is not passed to the builder: the room config the token carries is the dispatch and
nothing else, because everything else in it — egress, timeouts, a room name — is the box's to set.

## Which fields a client may not set

LiveKit's page: "for fields clients aren't allowed to set, return a 4xx status code." Three are.

- `room_name`: the room is the call id, minted here, so the log and the room share one name with
  nothing to keep in step (`docs/decisions/worker.md`). A client that could name the room could
  join somebody else's call.
- `participant_name`: a name is PII. LiveKit publishes it to the room, `participant.joined` carries
  the seat's attributes verbatim, and the log would have carried a name in every widget call. The
  contact id is opaque by the PII rule that already masks tool arguments and learned state
  (`docs/decisions/log.md`): the org knows who `c_9f3a2b` is, the log does not.
- `participant_metadata`: it is where the contact id goes, and `contact` is how it is said. Taking
  the raw claim as well would be two ways to write one field, one of which nobody checks.

They are declared on the body model rather than left to `extra=forbid`, so the refusal is a
sentence naming the field and why, not a 422 about an unknown key.

## Single use, and where "once" is kept

This is the one semantics LiveKit's token does not have. A JWT is valid until `exp`; self-hosted
there is no revocation list; and a TTL of a minute is not "once" — a page can join, leave and join
again inside it, and each join that creates the room creates a dispatch and a call.

What "once" means here is bounded by when LiveKit reads a token's room config at all. The
agent-dispatch page (docs.livekit.io/agents/server/agent-dispatch, "Dispatch via token"):

> When the first participant connects and creates the room, LiveKit dispatches the specified
> agents.

> Agent dispatch from the token only occurs when the room is first created. If the room already
> exists, the token's dispatch configuration is ignored.

So there are two second joins, and the ledger sees only one of them. **The room is gone** — the
first call ended, the worker left, LiveKit closed the room — and the join creates it again: a
new dispatch, a new job, `POST /v1/calls`, the spent row, `409`. That is the case the criterion
names and the ledger refuses. **The room is still live** — the first call is in progress — and
the same token joins again: LiveKit ignores the room config, no dispatch is created, nothing of
ours runs, and the joiner is seated in the live call under the token's identity. An identity is
unique per room, so the seat is taken over: the first browser is evicted, the second hears the
agent mid-sentence. For the token's remaining TTL, a leaked talk token is a hijack of a live call
and not a refusal. The ledger cannot see it because no dispatch happens; the door cannot see it
because the join is LiveKit's. What can see it is the worker, which is in the room and already
writes `participant.joined` for every seat (`docs/decisions/room.md`): a `participant_connected`
for an identity the call has already seated is the fact, and refusing it — `RemoveParticipant`,
`participant.left` with the reason — is a line in the room's facts. It is the hot path and it is
tk-64b2c2's, the integration card; until it lands, the TTL is the only bound on that case, and the
public contract says so in as many words.

The record lives in **Postgres**, `migrations/0005_tokens.sql`, one row per token keyed by the
call it opens. Three places were possible:

- **A dict in the gateway process** — refused by the hygiene rule on module-level state, and wrong
  on a box even as per-process state: two gateway replicas would each honour the token once.
- **On the org's row** — the org has no row yet (`api_keys` is the table, keyed by the key), and a
  token is not a fact about the org; a JSON column of open tokens would be a table inside a column.
- **Its own table**, keyed by the call. The room's name IS the call id, a token names exactly one
  room, so there is no second id to invent and no `jti` claim to add to a minter that has none.
  The dev clone gets the memory twin the routes and the keys already have (`tokens_for(pool)`),
  and it forgets on restart — which is what a clone with no database means everywhere else too.

Spending is one guarded `UPDATE … WHERE call = $1 AND spent_at IS NULL`. Two workers racing for one
token reach that row in some order and exactly one changes it; the command tag says which. Only a
refusal asks the second question, whether the row exists at all, so the sentence can say *already
spent* or *never minted* — the second being a token minted by another runtime, or by a dev clone
before it restarted. A spent row is kept, never deleted: it is the record of who opened what, and
an expired token has already been refused by the media plane on its own `exp`.

## Where it is spent: the dispatch, not the join

LiveKit lets the participant in; nothing of ours runs at the join. The first thing of ours that
runs is the worker's `POST /v1/calls` — the call's dispatch to an app socket
(`docs/decisions/dispatch.md`) — and that is where the row is spent, before a log exists for the
call. Only a dispatch that carries a `scope` is checked: a phone call's room is named by the media
plane, a console's by livekit, and neither holds a row. The refusal is `409` to the worker, whose
job dies with the sentence in its process log, and an `error` with `code: token_spent` in the
agent's own log, where the console reads it. The losing case is the browser's: it is in a room no
agent will join, and learns so only by the agent not arriving. A worker that deleted the room on
refusal would say it better, and that is a line in `worker/entry.py` for the card that owns the
worker's refusals.

## What changed around it

- `types/token.py`: `talk` and `chat` now read the log, own call only. The spec's "so a browser
  needs one token to speak and to watch its own call" is a row in the grants table, not a second
  token, and every derived set — `PROJECTION_OF`, `READS_ITS_OWN_CALL`, `MINTED_FOR_A_VISIT` —
  follows from it. The guest door (`a_reader`) and the room's DataChannel (`_is_a_widget`) both
  admit the derived set; neither spells a scope.
- `auth/scopes.py`: `Participate` became `CallToken` with a `scope`, the verify and the minter
  were renamed for what they now are, and the grants come from the scope's row: `chat` is audio
  off both ways, `participate` publishes nothing. The simulated caller in `evals/` publishes a
  microphone and so mints `talk` now, which is what it was doing all along.
- `Settings.livekit_public_url`: a box reaches its LiveKit on localhost and tells the browser a
  public URL; unset, the browser hears `LIVEKIT_URL`, which is right on a laptop.
- `a_call_id()` and `THE_WIDGET` moved to `types/`: the chat door and the token door mint the
  same call id, the router and the token door name the same channel.

## The two seat doors, and why one of them publishes (2026-09-09, tk-646671)

`POST /v1/calls/{call}/listen` and `POST /v1/calls/{call}/supervise` are one function,
`tokens/seating.py`: one identity shape, one 404, one 409, two scope rows. The `supervise` row
publishes the microphone and is NOT hidden, because livekit delivers no track from a hidden
participant — a hidden supervisor would take the line and speak into a silence — and there is
nobody to hide from: a phone shows no participant list, and the agent's ears are pinned to the
caller by `room_io.set_participant`. `observe` stays hidden and silent.

And a supervise token is now verifiable at all. `a_call_token` gated on `READS_ITS_OWN_CALL`,
derived as `reads_log and own_call_only`, so the desk's own token — minted for ONE call, reading
past no other — fell outside it and verified nowhere. The set a room token may carry is
`BOUND_TO_ONE_CALL`, derived as `READS_ITS_OWN_CALL | {the scopes that send verbs}`: still a
derivation, so a scope added tomorrow cannot be remembered in one list and forgotten in the other.
`PROJECTION_OF["supervise"]` stays `tenant`. `observe` is not in it: a listener's token is a seat
in a room, not a read. See `docs/decisions/supervise.md`.

## What is not here

A token for a dashboard user: the tenant's own doors take the API key, and a login is the milestone
that has one. The chat socket (`WS /v1/chat`) still opens on the dev key: a `chat` token exists
now, and the door that verifies it is the integration card's. The worker's `Contact(id=…)` from the
seat's `metadata` is that card's too.
