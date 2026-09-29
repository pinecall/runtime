# The token door — `POST /v1/tokens`

Where a tenant's backend mints a browser's token. It implements LiveKit's standard token endpoint
([docs.livekit.io, authentication endpoint](https://docs.livekit.io/frontends/build/authentication/endpoint)),
so a stock `livekit-client` with `TokenSource.endpoint(url, { headers })` joins the agent's room
with what it answers, and no Pinecall client code is involved. LiveKit's contract, in short: a
`POST` with optional `room_name`, `participant_identity`, `participant_name`,
`participant_metadata`, `participant_attributes`, `room_config`; `201` with `server_url` and
`participant_token`; the endpoint adds its own authentication and refuses with a `4xx` the fields
a client may not set.

## What this runtime adds to LiveKit's mint

1. **The org check.** The door takes the org's key holding `talk` (the tenant's backend holds it;
   the browser never sees it) and mints only for an agent somebody holds in the key's org and
   world. Nobody holding it is `404`, since a token for one is a browser joining a room nothing
   answers in. The token's `room_config` is one dispatch to the fleet of the key's world, its
   metadata naming the agent, the org, the world and the holder, so the one worker every org
   shares asks the gateway for that org's doors, declaration and keys.
2. **The contact id, and nothing PII.** `participant_metadata` carries the org's opaque contact id
   (`contact` in the body) and nothing else. A name is refused: the log would carry it. What the
   backend seals in `metadata` rides the signed dispatch; the browser reads its token and changes
   none of it.
3. **One dispatch opens one call.** The call the token names is written into a ledger when it is
   minted, and the dispatch that opens the call spends it. A join that creates the room again after
   the call ended is refused at `POST /v1/calls` with `409` and `token_spent` on the agent's log. A
   second join with the same token while its call is live is bounded by the TTL alone, since
   LiveKit reads a token's room config only on the join that creates the room: keep it short.

## The body

| field | whose | meaning |
|---|---|---|
| `agent` | ours | the agent's slug, or `room_config.agents[0].agent_name` as a stock client sends `agentName` |
| `scope` | ours | `talk` (default): publish the microphone, hear the agent. `chat`: the same room with no microphone. Any other is `400`: `observe` and `supervise` take the API key |
| `contact` | ours | the org's opaque id for the person; becomes `participant_metadata` |
| `metadata` | ours | JSON sealed into the call, reaching the worker in the signed dispatch |
| `ttl_s` | ours | 60 by default, 600 at most |
| `log` | ours | what the answer's `log_token` reads through: `public` (default) or `tenant` ([projections.md](projections.md)) |
| `participant_identity` | LiveKit's | who the browser joins as; `web_<12 hex>` when absent, and the call's `from` |
| `participant_attributes` | LiveKit's | published verbatim; the `pinecall.` prefix is ours and refused |
| `room_name` · `participant_name` · `participant_metadata` | LiveKit's | refused, `400`, each with its reason |

The answer is `201` with LiveKit's two fields and two more every SDK ignores:

```json
{ "server_url": "wss://sandbox.pinecall.io", "participant_token": "eyJ…", "call": "CA_5f1c…", "log_token": "eyJ…" }
```

`server_url` is `LIVEKIT_PUBLIC_URL` when set, else `LIVEKIT_URL`. `call` is the room and the id
its log will have, so the backend can watch it without decoding a JWT.

## The log token

The participant token dies in a minute; a page shows a call longer than that and after it ends.
`log_token` opens no room (`room_join`, publish and subscribe all false), carries
`pinecall.scope: read` and the projection asked, and lives four hours. It reads that one call's
`GET /v1/calls/{call}/events`, `/state` and `/recording`, as `?token=` or as the bearer, before and
after the call ends; another call, an agent's log or a supervisor verb is `403`. Those three doors
answer any origin (`GET`, no credentials): the token the page brings is what opens them.
`POST /v1/agents/{slug}/dial` answers one too.

## Refusals

`400` a scope this door does not mint, a field that is ours, a `pinecall.` attribute, no agent
named · `401` no key of ours · `403` a key without `talk` · `404` nobody holds that agent · `422` a
`ttl_s` past 600 · `429` a quota, in its own sentence · `503` every worker of the fleet is full:
`fleet.full` on the agent's log and a sentence naming `POST /v1/callbacks`.

## A stock client

```ts
import { Room, TokenSource } from "livekit-client";

const tokens = TokenSource.endpoint("https://clinica.example/token");   // the tenant's backend
const room = new Room();
await room.connect(...(await tokens.fetch({ agentName: "clinica-norte" })));
```

The backend's `/token` forwards the body to `POST /v1/tokens` with `Authorization: Bearer
pc_live_…`, adding what it knows: `contact`, `metadata`.
