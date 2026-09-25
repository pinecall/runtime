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
token this runtime reads back (the *auth* decision page in the maintainer's notebook) — and a stock `livekit-client`,
`TokenSource.endpoint(url, { headers })`, joins the agent's room with what it answers. No Pinecall
client code is involved.

## What we put in front of LiveKit's mint — the three additions

1. **The organisation check.** The door takes the org's API key (`Authorization: Bearer pc_live_…`, the server's token),
   never a browser's, and mints only for an agent **somebody is holding in this key's world, in
   this key's org** — the same two questions the chat socket asks of the same live registry. There
   is no web door to hold: a number is a row somebody bought and a browser is not, so every agent
   a key opens can be talked to from a page, and an agent with a telephone and no widget is one of
   them. An agent nobody is holding is `404` in the words the config door uses, because a token
   for one is a browser joining a room nothing will answer in. The token's `room_config` is one
   `RoomAgentDispatch` to our worker pool, whose metadata names the agent and the corner, so the
   worker's router resolves it without a routes lookup — the widget's route is made out of that
   metadata (`worker/router.py`), never looked up.
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

The org's API key, holding the `talk` scope, as `Authorization: Bearer <key>`, on the one bearer
parser every door uses.
**The key never reaches the browser**: the tenant's backend holds it, proxies its own `/token`
route to this door, and the browser's `TokenSource` points at the tenant's backend. `401` with
`WWW-Authenticate: Bearer` for a wrong or missing key, and nothing about why; `403` for a key
that does not hold `talk`.

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
| `scope` | ours | `talk` (default): publish the microphone, hear the agent. `chat`: the same room with **no microphone** — typed text goes out on `lk.chat`, and the token subscribes, because LiveKit hands the agent's text streams (`lk.transcription`) to subscribers only; the page attaches no audio. Any other scope is `400`: `observe` and `supervise` take the API key |
| `contact` | ours | the org's opaque id for the person. Becomes `participant_metadata`. Never a phone number, never a name |
| `metadata` | ours | JSON the backend seals into the call. It rides the signed dispatch and reaches the worker as the call's metadata |
| `ttl_s` | ours | how long the token lives: 60 by default, 600 at most (`422` past it). One dispatch opens one call whatever the TTL; a join into that call while it is live is bounded by the TTL alone (addition 3) — so a minute, unless the page has a reason |
| `log` | ours | what the answer's `log_token` reads the call through: `public` (default) or `tenant` — the tools, the latency, the cost, a `pii` field masked. The tenant's server chooses; the page cannot |
| `participant_identity` | LiveKit's | who the browser joins as; minted as `web_<12 hex>` when absent. It is the call's `from` in the log |
| `participant_attributes` | LiveKit's | published to the room verbatim; the `pinecall.` prefix is ours and refused |
| `room_config` | LiveKit's | read for the agent a stock client named; the room config the token carries is ours (below) |
| `room_name` | LiveKit's | **refused**, `400`: the room is the call id this door mints |
| `participant_name` | LiveKit's | **refused**, `400`: a name is PII |
| `participant_metadata` | LiveKit's | **refused**, `400`: it carries the contact id; send `contact` |

The answer is LiveKit's, with two fields more that every client SDK ignores:

```json
{ "server_url": "wss://livekit.clinica.example",
  "participant_token": "eyJhbGciOi…",
  "call": "call_5f1c…",
  "log_token": "eyJhbGciOi…" }
```

`server_url` is `LIVEKIT_PUBLIC_URL` when the box sets one and `LIVEKIT_URL` otherwise — a box
reaches its LiveKit on localhost and a browser cannot. `call` is the room the token opens and the
id its log will have, so the backend that minted it can watch the call without decoding a JWT.
`log_token` is the page's own reader of that call — below.

## What the token says

Read back with LiveKit's own `TokenVerifier`:

| claim | value |
|---|---|
| `sub` | the identity |
| `video.room`, `room_join` | the call id; join it |
| `video.can_publish`, `can_subscribe`, `can_publish_sources` | `talk`: true, true, `["microphone"]`. `chat`: false, true, none |
| `video.can_publish_data` | true for both: the DataChannel is how a widget speaks to the call |
| `metadata` | the contact id, or absent |
| `attributes["pinecall.scope"]` | `talk` or `chat` — and the other attributes the body sent |
| `roomConfig.agents[0]` | `agent_name`: the instance's fleet (`PINECALL_FLEET`, `pinecall` unless set), `metadata: {"agent", "scope", "caller", "metadata", "org", "env", "holder"}` — the dispatch. `org` and `env` are the minting key's, `holder` the corner it holds (a developer's, in the sandbox; absent otherwise): the one worker every org shares reads them and asks the gateway for THAT org's doors, declaration and keys |
| `exp` | now plus `ttl_s` |

The same string reads the call's log: `GET /v1/calls/{call}/events?token=…` and
`GET /v1/calls/{call}/state?token=…` accept it as the guest reader of that one call, through the
public projection (`projections.md`). A browser needs one token to speak and to watch its own call.

## The log token

The participant token dies in a minute; a call lasts longer, and a page shows it after it ends.
`log_token` is what the page follows the call with, and it needs no relay on the tenant's server:

| claim | value |
|---|---|
| `video.room` | the call id — and `room_join`, `can_publish`, `can_subscribe`, `can_publish_data` all false: it opens no room |
| `attributes["pinecall.scope"]` | `read` |
| `attributes["pinecall.projection"]` | the `log` the mint asked for |
| `exp` | now plus four hours |

It reads `GET /v1/calls/{call}/events`, `/state` and `/recording` of that one call, as `?token=`
or as the bearer, through the projection it names — before the call ends and after, until it
expires. Another call is `403`, an agent's log is `403`, and a supervise verb is `403`: it reads,
and never steers. Those three doors answer a page on **any origin** (CORS `*`, `GET`, no
credentials — `Authorization`, `Last-Event-ID` and `Range` may be sent): what opens them is the
token the page brings, never a cookie, so a page on another site reads nothing it did not bring the
token for. Every other door answers only the origins `api/app_origins.py` names. `POST /v1/agents/{slug}/dial` answers one too, with the same `log`.

## The code token

A phone call is minted when it rings, so no token can name it in advance. `POST /v1/codes`
answers a `code_token` instead: `read` scope, a room of `code:{code}` that opens nothing, the code,
the agent and the world as `pinecall.code` · `pinecall.agent` · `pinecall.env`, and the code's own
expiry. It reads `GET /v1/codes/{code}` for that code and nothing else — every call door answers it
`403` — and once a call claims the code, that door hands the page a log token for the call.
[codes.md](codes.md).

## Refusals

| status | when |
|---|---|
| `400` | no agent named either way; a scope this door does not mint; `room_name`, `participant_name` or `participant_metadata` present; a `pinecall.` attribute; a `room_config` that is not one, in the parser's words |
| `401` | no key, or not a key of ours |
| `403` | a key that does not hold `talk` |
| `404` | nobody is holding that agent in the key's org and world |
| `422` | a `ttl_s` outside 1–600, or a body key nobody declared |
| `429` | one of the org's quotas admits no more calls, in the quota's own sentence |
| `503` | every worker of the fleet is full: `fleet.full` in the agent's log first, and a sentence naming `POST /v1/callbacks` |

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

The backend's `/token` is a `fetch` to `POST /v1/tokens` with `Authorization: Bearer pc_live_…` and the
same body, plus whatever it knows: `contact`, `metadata`. The runtime dispatches the worker into
the room the moment the browser joins; the worker resolves the agent from the dispatch, opens the
call, and answers.
