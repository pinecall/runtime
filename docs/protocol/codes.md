# Codes — a page that follows a call it did not place

A page can follow a call it started: `POST /v1/tokens` and `POST /v1/agents/{slug}/dial` both
answer a `log_token` for the call they open ([tokens.md](tokens.md)). A call that **arrives** on
the phone is different: it is minted when it rings, by the SFU, and nothing ties it to the browser
tab of the person who dialled. A **code** is that tie. The page shows the agent's number and four
digits; the caller keys them on the phone (or says them, and the agent claims them); from that
moment the page reads the call's log exactly as it reads a call it placed.

Nothing new is stored. The agent's own log is the table — `code.issued` when a code is handed out,
`code.claimed` when a call takes it or its time runs out — and the gateway keeps the live ones in
memory, read back off the log when it starts, as it does WhatsApp's waiting room.

## Issuing one — `POST /v1/codes`

Your server asks, with the org's key (`talk`, as for `POST /v1/tokens`):

```json
{"agent": "clinica-norte", "ttl_s": 600, "log": "public"}
```

`ttl_s` is 60–1800, 600 by default. `log` is the projection the call's `log_token` will read
through once a call claims the code: `public` unless your page draws the tenant's. The answer is
`201`, the protocol's `Code`:

```json
{"code": "4821", "number": "+34910000000", "expires_at": 1790000000.0, "code_token": "eyJ…"}
```

- `code` — four digits no other live code of this agent holds, in either world.
- `number` — the first phone number this agent answers at in the key's world (`GET /v1/numbers`).
- `code_token` — what the page asks with. Hand it to the page with the code; never the key.

| status | when |
|---|---|
| `401` · `403` | no key of ours, or one without `talk` |
| `409` | the agent answers at no phone number in the key's world: `agent {slug} answers at no phone number in {env}: pinecall numbers import` |
| `422` | a `ttl_s` outside 60–1800, or a body key nobody declared |
| `429` | the agent already holds 50 live codes |

## Asking after one — `GET /v1/codes/{code}`

The page asks with the code token, as the bearer or as `?token=` (an `EventSource`-style page has
no header). `?wait=1` holds the request until a call claims the code, at most 25 s — under the
30 s a proxy gives an idle request — and then answers as the code stands; the page asks again.
The answer is `200`, the protocol's `CodeStanding`:

| `status` | `call` | `log_token` | the page |
|---|---|---|---|
| `waiting` | `null` | `null` | shows the number and the code, and asks again |
| `claim_code` | the call id | a log token for that call, minted now, four hours, through the `log` the code was issued with | follows the call: `GET /v1/calls/{call}/events?token=…` |
| `expired` | `null` | `null` | draws it: "the code expired, ask for another". `200`, never `410`: it is an answer |

`401` without a token of ours; `403` for a token of another code (or any other token); `404` for a
code nobody issued. The code token itself dies with the code. Any origin may ask — GET, no
credentials — as it may read a call's own doors: the token the page brings is the whole of what
opens it. A call that changes hands mid-conversation carries its claim in `call.attached`.

## The claim — `POST /v1/calls/{call}/claim` and `call.claim`

Two ways, one effect:

- **The keypad.** The worker writes every tone the **caller** keys — the caller's own SIP leg,
  never a second leg `room.invite` brought in — as `dtmf.received {digit, code}` on the call's log.
  When four digits arrive within 8 s of each other it asks the gateway,
  `POST /v1/calls/{call}/claim {"code": "4821"}` with the worker's key (`app`). A `*` or a `#`
  starts the four over; the same four digits are asked once per call. `404` is the ordinary answer
  — the caller was keying an extension — and the worker does nothing with it.
- **The agent.** The class heard the code said and sends `call.claim {code}` on the app socket
  for that call. Refused with `no_code` when no page waits on it.

Either way the gateway marks the code taken on the agent's log — `code.claimed {code, call}` —
wakes the page waiting on it, and writes `call.claimed {code, via: "keypad" | "agent"}` on the
call's own log: the class learns the person is on the site, and the console shows it. A code is
claimed once: a second claim, by the same call or another, is `404` / `no_code`.

## On the agent's log

| entry | data | written |
|---|---|---|
| `code.issued` | `{code, env, expires_at, log}` | by `POST /v1/codes` |
| `code.claimed` | `{code, call}` | by the claim, with the call; with `call: null` once an unclaimed code's time is up |

An expired code is closed lazily — by the next issue, ask or claim that finds it — not by a loop.
A gateway that restarts reads every `code.issued` not yet closed back into memory before it opens
its doors, so a page waiting through a deploy is answered when its caller keys the code.

## The code token

A LiveKit token, like every token this runtime reads, that opens no room and reads no call:

| claim | value |
|---|---|
| `video.room` | `code:{code}` — and `room_join`, `can_publish`, `can_subscribe`, `can_publish_data` all false |
| `attributes["pinecall.scope"]` | `read` |
| `attributes["pinecall.code"]` · `["pinecall.agent"]` · `["pinecall.env"]` | the code, the agent and the world it was issued for |
| `exp` | the code's `expires_at` |

It names no call, so every door that reads one — a call's events, state or recording, an agent's
log — answers it `403`. The one thing it reads is `GET /v1/codes/{code}` for its own code.

## The sequence, in words

1. The visitor clicks "call it" on the site. The page asks the tenant's server, which asks
   `POST /v1/codes` with the org's key and hands the page `{code, number, expires_at, code_token}`.
2. The page shows "call +34 910 000 000 and key 4821" and asks
   `GET /v1/codes/4821?wait=1&token=…`, again each time it is answered `waiting`.
3. The visitor dials. The call rings, the worker opens its log, the agent answers and asks for
   the code.
4. The caller keys 4-8-2-1. The worker writes four `dtmf.received`, then asks
   `POST /v1/calls/{call}/claim`. (Or the caller says it, and the class sends `call.claim`.)
5. The gateway writes `code.claimed {code, call}` on the agent's log and `call.claimed` on the
   call's, and the page's held request answers `claim_code` with the call and a log token.
6. The page follows the call's log as it follows any call: the transcript, the state, the summary.
