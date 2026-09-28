# An inbound number, end to end, with curl

Three writes: the org's carrier account, the number pointed at an agent, and a call. The key is
an org's server key with the `numbers` scope; a `pc_test_` key acts in the sandbox, a
`pc_live_` key in production.

```sh
export PINECALL_URL=https://sandbox.pinecall.io
export PINECALL_KEY=pc_test_...          # minted in the console, Keys → server key
```

## 1. The carrier account

A Twilio account, by an API key SID and its secret (Twilio → Account → API keys):

```sh
curl -X PUT $PINECALL_URL/v1/carrier \
  -H "Authorization: Bearer $PINECALL_KEY" -H "Content-Type: application/json" \
  -d '{"kind":"twilio","account_sid":"AC...","user":"SK...","secret":"...","label":"Clinic"}'
```

The secret is sealed in the vault; the answer names the account by its id, never its secret.
`GET /v1/numbers/available` lists every number the account owns.

## 2. The number, routed to an agent

```sh
curl -X POST $PINECALL_URL/v1/numbers \
  -H "Authorization: Bearer $PINECALL_KEY" -H "Content-Type: application/json" \
  -d '{"number":"+13617334133","agent":"front-desk","account":"AC..."}'
```

The gateway points the number at the box in Twilio (a SIP trunk to `PINECALL_DOMAIN`), admits
the carrier's networks on the box's inbound trunk, and writes the route. The answer lists each
step and whether it `stands`. A number from a carrier the box cannot configure is imported with
`"hooked": true` and the `networks` it will call from; the org points it at the box itself.

## 3. The call

Start the agent from its own repository (`pinecall start` with the SDK) so its app socket is
open, then dial the number. The call's log appears at `GET /v1/calls` and streams live at
`GET /v1/calls/{call}/log?follow=1`.

`docs/protocol/numbers.md` is the reference for every field of these doors.
