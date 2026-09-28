# An outbound call, with curl

The agent must be running (its app socket open) and the org must hold a number to show; the
number's account is made dialable once.

```sh
export PINECALL_URL=https://sandbox.pinecall.io
export PINECALL_KEY=pc_test_...
```

## 1. Make the account dialable

```sh
curl -X POST $PINECALL_URL/v1/carrier/outbound \
  -H "Authorization: Bearer $PINECALL_KEY" -H "Content-Type: application/json" \
  -d '{"account":"AC..."}'
```

On Twilio this creates a termination trunk and a credential; a SIP peer needs nothing.

## 2. Dial

```sh
curl -X POST $PINECALL_URL/v1/agents/front-desk/dial \
  -H "Authorization: Bearer $PINECALL_KEY" -H "Content-Type: application/json" \
  -d '{"to":"+34600000001","from":"+15550100133"}'
```

The answer is `202` with the call's id, the two numbers, the world and a `log_token` that reads
this one call's log without the org's key:

```sh
curl "$PINECALL_URL/v1/calls/$CALL/log?follow=1" -H "Authorization: Bearer $LOG_TOKEN"
```

The guards run before anything rings: the org's outbound quota, the stranger fence of the
world, and whether the agent is held by a running process. A refused dial is a `4xx` with the
sentence that says why; a placed one is `call.dialing` in the log, then `call.started` when the
far end answers.
