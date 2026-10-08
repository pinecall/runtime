# The line: hold, transfer, a person, a call back, never again

Seven commands act on a call already running, and they are how an agent hands what it is doing to
somebody else. They travel down the app socket like every call-scoped command (`"call": "<id>"`,
refused with `no_session` when no gateway of the platform runs that call), and every one but
`call.opt_out` answers in the call's own log: the outcome is an entry, never a return value. A
command reaches the call whichever gateway the app is connected to: it is told on the call's own
channel, and the gateway holding its worker's command stream, or running its written session,
takes it.

| command | what happens |
|---|---|
| `call.transfer` | the caller is sent on, or the far end dialled in (below); lands as `call.transferred` |
| `call.attention` | a person is wanted: the caller holds until a supervisor takes the line or `wait_s` runs out; `attention.requested`, then `attention.answered` |
| `call.hold` · `call.unhold` | the caller waits with the hold melody and the agent goes mute and deaf, and back; `call.line` |
| `call.dtmf` | touch tones down the caller's own leg, for an IVR on the far end: `0-9`, `*`, `#`, a comma for a pause |
| `call.opt_out` | the caller asked never to be called again: the number joins the org's do-not-call list, with the call beside it; nothing lands in the log, so an SDK that predates it reads the call as before |
| `call.callback` | the caller wants ringing back; `callback.requested` with `via: "agent"`, which `GET /v1/callbacks` lists beside the widget's and the overflow's |

## A transfer is two different things

**Cold** is a REFER on the caller's own SIP leg: the carrier takes the call, the leg leaves the
room, and this call ends as `transferred`. It needs a phone call.

**Warm** dials the destination into this call's room over the org's outbound trunk
([numbers.md](numbers.md)). Nobody moves: the caller hears ringing, and when the far end answers the
agent goes mute and deaf and the two people have the call, until either hangs up.

A dialled leg passes the org's dial guards and lands in the same ledger as `POST
/v1/agents/{slug}/dial`: the number's shape, the dials this minute and today. Refused, nothing is
dialled and `call.transferred` names the guard. The one guard a leg does not pass is the stranger
fence: the colleague a caller is put through to never had to ring the org. `room.invite` is judged
the same way.

`mode` is optional; unsaid, it is cold for a caller on a SIP leg and warm for one in a browser.
Asked by name, it is refused rather than swapped. A written conversation has no line:
`call.transferred` comes back with `ok: false` and says to ask for a person instead.

## Asking for a person

`call.attention {reason, wait_s}`: the reason is what a supervisor reads before taking the line;
`wait_s` has no default, because how long a caller will hold is the app's to decide. While the ask
is open the caller is on hold and `state.attention` is `open`. It ends as a supervisor's `takeover`
(`ok: true`, with who), as `wait_s` passing (`ok: false`, the agent has the caller back), or with
the call. A tool that asked for a person is still running while the caller waits, so its own
timeout must outlast `wait_s`; and a tool that comes back while a supervisor holds the line
produces no reply.
