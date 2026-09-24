# The box's floor — `GET /v1/ops/events`

Part of the [operator API](operator-api.md), on its own page: every org's floor changing, on one
stream, for whatever serves the box as a whole — a notifier that pushes to people's phones, a
wallboard over every tenant. One org's key reads that org's floor at `GET /v1/events`
([gateway-api.md](gateway-api.md) §7b); this is the same moments, every org at once.

## Who may read it

The operator's key only — `PINECALL_OPS_KEY`, or the key of a person the box made an operator
([operator-api.md](operator-api.md#authentication)) — as `Authorization: Bearer <key>`. There is
no `?token=`: an EventSource cannot set a header, and nothing that reads this runs in a browser.
Anything else is `401`.

## What it carries

SSE, live only, from the moment it opens: nothing before, and no cursor. The events are the org
stream's, no more: `agent.registered` · `agent.detached`, `call.ringing` · `call.dialing` ·
`call.started` · `call.ended`, `attention.requested` · `attention.answered`,
`supervisor.took_over` · `supervisor.released`. A turn is never on it.

Each frame is one entry of some log, wrapped with the org whose log it is — the protocol's
`BoxEvent`:

```
id: 1
event: attention.requested
data: {"org":"org_…","entry":{"seq":1,"ts":1727170000.1,"call":"CA_…","agent":"clinica-norte","type":"attention.requested","ephemeral":false,"data":{"reason":"…","wait_s":60}}}
```

- `org` is the org's **id**, what that org's keys answer as `org` at `GET /v1/whoami`.
- `entry` is the envelope as the store holds it, unprojected: the operator is the box's, and
  what the store holds was masked when it was written.
- `id:` is the entry's `seq` in its own log, so ids from two calls interleave: a reconnect opens
  from now and whatever passed while it was away is not replayed. What a call said is that call's
  log, at `GET /v1/calls/{call}/events` with the org's own key.
- `retry: 1000` opens the stream and `: ping` comes every 25 s of quiet, as on every SSE door.
- A reader that falls 256 entries behind is dropped; it reconnects and carries on from now.

## Reading it from a service

```js
const answer = await fetch(`${box}/v1/ops/events`, {
  headers: { authorization: `Bearer ${opsKey}`, accept: "text/event-stream" },
});
for await (const chunk of answer.body.pipeThrough(new TextDecoderStream())) {
  // split on blank lines; `event:` names the entry, `data:` is the BoxEvent
}
```

On a box the reader runs beside the gateway and reads `http://127.0.0.1:8080`, with the ops key as
a systemd credential (`ImportCredential=PINECALL_OPS_KEY`), never in a file of its own.
