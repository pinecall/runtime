# The token door — `POST /v1/tokens`

The door a tenant's backend mints a browser's token at. It is a **public contract**, and most of
it is not ours: it implements LiveKit's standard token endpoint —
[docs.livekit.io/frontends/build/authentication/endpoint](https://docs.livekit.io/frontends/build/authentication/endpoint)
— so that every LiveKit client SDK already speaks it. What that page specifies, quoted:

> Build your own token endpoint for production use. Your backend generates JWT tokens, and your
> frontend uses an endpoint `TokenSource` to fetch them.

> **Method**: POST with JSON body. The endpoint accepts these optional fields: `room_name`
> (string), `participant_identity` (string), `participant_name` (string), `participant_metadata`
> (string), `participant_attributes` (map<string, string>), `room_config` (RoomConfiguration).
> **Status Code**: 201 Created. **Response fields**: `server_url` (string): Room connection URL;
> `participant_token` (string): Room connection token.

> You must add your own custom header-based authentication to the endpoint to ensure that your
> endpoint is secure. For fields clients aren't allowed to set, return a 4xx status code.

> The client SDKs automatically package agent information (like `agent_name`, `agent_metadata`,
> and the optional `deployment`) into `room_config` before sending the request, so your endpoint
> implementation only needs to pass `room_config` to the token builder.

The token is minted with `livekit-api`'s `AccessToken` — the same minter that signs every call
token this runtime reads back (`docs/decisions/auth.md`) — and a stock `livekit-client`,
`TokenSource.endpoint(url, { headers })`, joins the agent's room with what it answers. No Pinecall
client code is involved.

## What we put in front of LiveKit's mint — the three additions

1. **The organisation check.** The door takes the org's API key (`Authorization: Bearer pk_…`),
   never a browser's, and mints only for an agent that key's fleet answers **on the web** — the
   very tables the worker asks at `GET /v1/routes` when the job arrives. The token's `room_config`
   is one `RoomAgentDispatch` to our worker pool, whose metadata names the agent, so the worker's
   router resolves it without a routes lookup. An agent the fleet does not answer on the web is
   `404` in the words the config door uses.
2. **The contact id, signed, and nothing PII.** `participant_metadata` — LiveKit's own claim —
   carries the org's opaque contact id (`contact` in our body) and nothing else. A name is refused
   (`participant_name`, `400`): the log would carry it. What the tenant's backend seals in
   `metadata` reaches the worker inside the signed dispatch; the browser can read its token and
   alter none of it.
3. **Single use — one dispatch opens one call.** LiveKit has no notion of "once" and,
   self-hosted, no revocation. So the call the token names is written into a ledger when it is
   minted, and the dispatch that opens the call spends it. A join that creates the room again
   after the call ended creates a second dispatch, finds the row spent, and is refused at
   `POST /v1/calls` with `409` — the worker's job dies naming the call, and the agent's own log
   carries an `error` with `code: token_spent` saying why. **What "once" does not cover today:** a
   second join with the same token *while its call is still live*. LiveKit reads a token's room
   config only on the join that creates the room ("if the room already exists, the token's
   dispatch configuration is ignored"), so no dispatch happens, nothing of ours runs, and the
   joiner takes the token's seat in the live call — an identity is unique per room, so the first
   browser is evicted. Until the worker refuses a second seat for an identity the call already
   holds, the token's TTL is the only bound on that case: keep it short.

## Authentication

The fleet's API key, as `Authorization: Bearer <key>`, on the one bearer parser every door uses.
**The key never reaches the browser**: the tenant's backend holds it, proxies its own `/token`
route to this door, and the browser's `TokenSource` points at the tenant's backend. `401` with
`WWW-Authenticate: Bearer` for a wrong or missing key, and nothing about why.

## `POST /v1/tokens`

LiveKit's body, plus ours. Every field is optional but the agent, which may be named either way.

```json
{ "agent": "clinica-norte",
  "scope": "talk",
  "contact": "c_9f3a2b",
  "metadata": { "order": "o_77" },
  "ttl_s": 60,
  "participant_identity": "web_the_visitor",
  "participant_attributes": { "theme": "dark" } }
```

| field | whose | meaning |
|---|---|---|
| `agent` | ours | the agent's slug. Or `room_config.agents[0].agent_name` — what a stock client's `agentName` becomes on the wire, either spelling |
| `scope` | ours | `talk` (default): publish the microphone, hear the agent. `chat`: the same room with **audio off both ways** — data only. Any other scope is `400`: `observe` and `supervise` take the API key |
| `contact` | ours | the org's opaque id for the person. Becomes `participant_metadata`. Never a phone number, never a name |
| `metadata` | ours | JSON the backend seals into the call. It rides the signed dispatch and reaches the worker as the call's metadata |
| `ttl_s` | ours | how long the token lives: 60 by default, 600 at most (`422` past it). One dispatch opens one call whatever the TTL; a join into that call while it is live is bounded by the TTL alone (addition 3) — so a minute, unless the page has a reason |
| `participant_identity` | LiveKit's | who the browser joins as; minted as `web_<12 hex>` when absent. It is the call's `from` in the log |
| `participant_attributes` | LiveKit's | published to the room verbatim; the `pinecall.` prefix is ours and refused |
| `room_config` | LiveKit's | read for the agent a stock client named; the room config the token carries is ours (below) |
| `room_name` | LiveKit's | **refused**, `400`: the room is the call id this door mints |
| `participant_name` | LiveKit's | **refused**, `400`: a name is PII |
| `participant_metadata` | LiveKit's | **refused**, `400`: it carries the contact id; send `contact` |

The answer is LiveKit's, with one field more that every client SDK ignores:

```json
{ "server_url": "wss://livekit.clinica.example",
  "participant_token": "eyJhbGciOi…",
  "call": "call_5f1c…" }
```

`server_url` is `LIVEKIT_PUBLIC_URL` when the box sets one and `LIVEKIT_URL` otherwise — a box
reaches its LiveKit on localhost and a browser cannot. `call` is the room the token opens and the
id its log will have, so the backend that minted it can watch the call without decoding a JWT.

## What the token says

Read back with LiveKit's own `TokenVerifier`:

| claim | value |
|---|---|
| `sub` | the identity |
| `video.room`, `room_join` | the call id; join it |
| `video.can_publish`, `can_subscribe`, `can_publish_sources` | `talk`: true, true, `["microphone"]`. `chat`: false, false, none |
| `video.can_publish_data` | true for both: the DataChannel is how a widget speaks to the call |
| `metadata` | the contact id, or absent |
| `attributes["pinecall.scope"]` | `talk` or `chat` — and the other attributes the body sent |
| `roomConfig.agents[0]` | `agent_name: pinecall`, `metadata: {"agent", "scope", "caller", "metadata"}` — the dispatch |
| `exp` | now plus `ttl_s` |

The same string reads the call's log: `GET /v1/calls/{call}/events?token=…` and
`GET /v1/calls/{call}/state?token=…` accept it as the guest reader of that one call, through the
public projection (`projections.md`). A browser needs one token to speak and to watch its own call.

## Refusals

| status | when |
|---|---|
| `400` | no agent named either way; a scope this door does not mint; `room_name`, `participant_name` or `participant_metadata` present; a `pinecall.` attribute; a `room_config` that is not one, in the parser's words |
| `401` | not the fleet's key |
| `404` | the fleet does not answer that agent on the web |
| `422` | a `ttl_s` outside 1–600, or a body key nobody declared |

And at the dispatch, when the browser joins: `POST /v1/calls` answers the worker `409` for a token
already spent (*"call … was already opened by its token: a call token opens one call, once"*) or
one this runtime never minted. Both land in the agent's log as `error`, `code: token_spent`.

## A stock client, end to end

```ts
import { Room, TokenSource } from "livekit-client";

// The tenant's backend: holds the org key, forwards the browser's request to this door.
const tokens = TokenSource.endpoint("https://clinica.example/token");
const room = new Room();
await room.connect(...(await tokens.fetch({ agentName: "clinica-norte" })));
```

The backend's `/token` is a `fetch` to `POST /v1/tokens` with `Authorization: Bearer pk_…` and the
same body, plus whatever it knows: `contact`, `metadata`. The runtime dispatches the worker into
the room the moment the browser joins; the worker resolves the agent from the dispatch, opens the
call, and answers.
