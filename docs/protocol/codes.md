# Codes — a page that follows a call it did not place

A page follows a call it started with the `log_token` the mint or the dial answered
([tokens.md](tokens.md)). A call that **arrives** on the phone is minted when it rings, and nothing
ties it to the browser tab of the person who dialled. A **code** is that tie: the page shows the
agent's number and four digits, the caller keys them (or says them, and the agent claims them), and
from then on the page reads the call's log as it reads a call it placed.

Nothing new is stored. The agent's own log is the table, `code.issued` when a code is handed out and
`code.claimed` when a call takes it or its time runs out, and the gateway keeps the live ones in
memory, read back off the log when it starts.

## Issuing one — `POST /v1/codes` (`talk`)

```json
{"agent": "clinica-norte", "ttl_s": 600, "log": "public"}
```

`ttl_s` is 60 to 1800, 600 by default; `log` is the projection the call's log token reads through.
`201`: `{"code": "4821", "number": "+59829001199", "expires_at": 1790000000.0, "code_token": "eyJ…"}`,
four digits no other live code of the agent holds, the first phone number the agent answers at in
the key's world, and the token the page asks with. `409` when the agent answers at no phone number
in that world; `429` past 50 live codes for one agent.

## Asking after one — `GET /v1/codes/{code}`

With the code token, as the bearer or `?token=`. `?wait=1` holds the request until a call claims the
code, 25 s at most (under a proxy's idle 30), then answers as it stands: `{code, status, expires_at,
call, log_token}`, `status` one of `waiting`, `claimed` (the call, and a log token for it minted now)
or `expired` (`200`: it is an answer). `403` for a token of another code, `404` for a code nobody
issued. Any origin may ask.

## The claim — `POST /v1/calls/{call}/claim` and `call.claim`

- **The keypad.** The worker writes every tone the caller keys as `dtmf.received`; four digits
  within 8 s of each other are asked of the gateway, `POST /v1/calls/{call}/claim {code}`. `404` is
  the ordinary answer: the caller was dialling an extension.
- **The agent.** The class heard the code said and sends `call.claim {code}` on the app socket;
  refused with `no_code` when no page waits on it.

Either way the gateway writes `code.claimed {code, call}` on the agent's log, wakes the page
waiting on it, and writes `call.claimed {code, via: "keypad" | "agent"}` on the call's. A code is
claimed once. A call keys three codes, by either way: its fourth and every one after are refused
like a code nobody issued, a live one too, so keying at random has three chances a call. An
expired code is closed lazily, by the next issue, ask or claim that finds it.

## The code token

A LiveKit token that opens no room (`code:{code}`, every grant false), `pinecall.scope: read`, the
code, agent and world as attributes, and the code's own expiry. Every call door answers it `403`;
it reads `GET /v1/codes/{code}` for its own code and nothing else.
