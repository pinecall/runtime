# auth — one token for text and for voice

## The decision

A participate token **is** a LiveKit room token whose room is the call id.

There is no second token format for the browser, no second secret to set, and no signing code of
ours. `auth/scopes.py` mints with `livekit.api.AccessToken` and reads back with
`livekit.api.TokenVerifier`; the string a widget uses to read a call's log over SSE in ms-2 is the
same string it will hand to `Room.connect()` in ms-7, unchanged.

The version this replaced signed `pt_<call>.<expiry>.<tag>` by hand: HMAC-SHA256 over two base64
fields, with `PINECALL_TOKEN_SECRET`. Every argument for it was true — no untrusted `alg` header,
no claim set nobody reads, no library — and it was still wrong, because the runtime already ships
the library. `livekit-agents` brings `livekit-api`, which brings `pyjwt`; the JWT was in the image
either way. What the hand-rolled version actually bought was a second token the browser could not
use, which is the one property nobody wanted.

## What the claims carry

| claim | what it is |
|---|---|
| `video.room` | the call id. This is the binding: `verify_participate(token, call, …)` compares it |
| `video.room_join`, `can_publish`, `can_subscribe` | what the browser needs to be *in* the call rather than to watch it. The log reader ignores them; ms-7 does not |
| `sub` (identity) | who is reading. Passed in, or minted as `web_<12 hex>` — the shape `session/text/chat.py` already gives a visitor |
| `attributes["pinecall.scope"]` | `participate`. LiveKit publishes attributes to the room, so a participant's scope is readable on the media plane too, without a second lookup |
| `exp` | LiveKit stamps it from the TTL. `a_participate_token` takes an absolute `expires_at` and hands LiveKit the difference |
| `iss` | the API key, which is what makes the pair below the whole of the secret |

The verifier runs with **no leeway**. LiveKit's default forgives a minute of clock skew because
its tokens cross machines; ours is verified by the process that minted it, so expired means
expired. `TokenVerifier.verify` drops `exp` on its way back, so the expiry is read with one
signature-less `jwt.decode` *after* the verification — a payload nobody could have forged by then.

The scope attribute is checked, not assumed. A perfectly valid LiveKit token minted for some other
purpose against the same pair opens a room; it does not open this tenant's log.

## Why the secret is LiveKit's pair

Because the token is LiveKit's token. `secret_for(settings)` returns a `LivekitKeys(api_key,
api_secret)` from `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` — the same two variables the media plane
and the doctor already read. `PINECALL_TOKEN_SECRET` is gone: a variable whose only job was to sign
a format that no longer exists.

A clone that has a `PINECALL_DEV_KEY` and no LiveKit at all still works: the pair is derived from
the dev key (`devkey` / `sha256("participate:" + dev_key)`), which signs and verifies our own reads
and opens no real room — which is exactly what development means. A process with neither refuses to
verify anything rather than accepting everything.

`is_a_participate_token` is now "three non-empty dot-separated parts", the shape of a JWT. A
Pinecall API key is 256 opaque bits with no dot in it, so the door still picks the verifier before
it verifies either. It is a pre-check, never a verdict: the verify is the truth.

## NotGiven

`pinecall._types` used to define its own `NotGiven` / `NOT_GIVEN` / `NotGivenOr`, byte for byte the
class in `livekit/agents/types.py`. It now re-exports LiveKit's. Two identical sentinels are one
bug waiting: a value crossing from a LiveKit plugin into our code would have been "given" to one of
them and "not given" to the other. Every importer still writes `from pinecall._types import
NOT_GIVEN`, and `is_given` is still ours.

This means `pinecall/_types.py` imports LiveKit. The pure-ring invariant is unchanged and still
enforced: `protocol/`, `log/`, `types/` and `lang/` import **no** framework, LiveKit included —
`_types.py` sits at the package root, not in any of the four, and `protocol/metrics.py` names
LiveKit only in prose. `tests/test_isolation.py` says so where the list is declared.

## Left for ms-7

- **The browser joining a real room with this same token.** Nothing in the token has to change;
  what is missing is the room itself and the worker on the other side of it.
- **Who gets a token, and when.** There is still no route that issues one — `a_participate_token`
  is called by tests and by whatever ms-7's `/v1/calls` door becomes.
- **The other scopes.** `talk`, `chat`, `observe` and `supervise` are in `PROJECTION_OF`; only
  `participate` is minted. The chat socket still opens on the dev key.
- **The identity becoming an author.** `Reader.viewer` is now the token's identity, so a guest sees
  the `event.received` entries it caused. Nothing mints an identity that matches a real
  participant's yet, which lands with the room.

## Postscript, 2026-09-08 — the names, after ms-7's token door

Everything above holds; the names moved to say what they now are. A participate token was the
only room token the runtime minted, so the functions were named after it. `POST /v1/tokens`
([tokens.md](tokens.md)) mints `talk` and `chat` tokens through the same minter, and a
talk token reads its own call too, so:

| was | is |
|---|---|
| `Participate` | `CallToken`, with a `scope` |
| `a_participate_token(call, expires_at, secret, identity)` | `a_room_token(call, scope, expires_at, secret, identity, *, metadata, attributes, room_config)` |
| `a_participate` · `verify_participate` | `a_call_token` · `verify_call_token` |
| `is_a_participate_token` | `is_a_jwt` |
| `PARTICIPATE`, compared by `==` | `domain/token.READS_ITS_OWN_CALL`, derived from the grants table |

The grants a token carries come from its scope's row now: `participate` publishes nothing and
`chat` is audio off both ways. The three items under "Left for ms-7" landed with that card.
