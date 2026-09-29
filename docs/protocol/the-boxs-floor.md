# The box's floor — `GET /v1/ops/events`

Part of the [operator API](operator-api.md): every org's floor changing, on one stream, for
whatever serves the box as a whole. One org's key reads its own floor at `GET /v1/events`; this is
the same moments, every org at once, on the operator's key alone. There is no `?token=`: nothing
that reads this runs in a browser.

SSE, live only, from the moment it opens: no cursor, nothing replayed. The entries are the org
feed's: `agent.registered` · `agent.detached`, `call.ringing` · `call.dialing` · `call.started` ·
`call.ended`, `attention.requested` · `attention.answered`, `supervisor.took_over` ·
`supervisor.released`. A turn is never on it. Each frame is one entry wrapped with the org whose
log it is and the world its call runs in (`production`, `sandbox`); an agent's own entries serve
both worlds, and their `env` is `null`. A reader that serves one world, such as a notifier that
pages people for production's calls, keeps the frames of that world and drops the rest:

```
id: 1
event: call.ringing
data: {"org":"org_…","env":"production","entry":{"seq":1,"ts":1727170000.1,"call":"CA_…","agent":"clinica-norte","type":"call.ringing","ephemeral":false,"data":{…}}}
```

`id:` is the entry's `seq` in its own log, so ids from two calls interleave and a reconnect opens
from now. `retry: 1000` opens the stream and `: ping` comes every 25 s of quiet. An entry whose log
nobody claimed yet carries no org and is left out.
