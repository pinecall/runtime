# The line: hold, transfer, a person, a call back

Six commands act on a call that is already running, and they are how an agent hands what it is
doing to somebody else. They travel down the app socket like every other call-scoped command
(`"call": "<id>"`, refused with `no_session` when that call is not running), and every one of them
answers in the call's own log: the outcome is an entry, never a return value. The tenant's
framework wraps them as `this.call.transfer(…)` and the rest — **agents**' `docs/contact-center.md`.

| command | what happens |
|---|---|
| `call.transfer` | the caller is sent on, or the far end is dialled in — see below; lands as `call.transferred` |
| `call.attention` | a person is wanted on this call: the caller holds until a supervisor takes the line, or until `wait_s` runs out. Lands as `attention.requested`, then `attention.answered` |
| `call.hold` · `call.unhold` | the caller waits with the hold melody and the agent goes mute and deaf, and back. Lands as `call.line` |
| `call.dtmf` | touch tones down the caller's own leg, for an IVR on the far end: `0-9`, `*`, `#`, and a comma for a short pause |
| `call.callback` | the caller wants ringing back. Lands as `callback.requested` with `via: "agent"`, which `GET /v1/callbacks` lists beside the widget's and the overflow agent's |

## A transfer is two different things

**Cold** is a REFER on the caller's own SIP leg: the carrier takes the call, the leg leaves the
room, and this call ends as `transferred`. It needs a phone call — a browser has no leg to refer.

**Warm** dials the destination INTO this call's room, over the org's own outbound trunk
(`POST /v1/carrier/outbound` provisions one; the worker reads it at
`GET /v1/agents/{slug}/outbound-trunk`). Nobody moves: the caller hears a ringing tone, and when
the far end answers the agent goes mute and deaf and the two humans have the call. It ends when
either of them hangs up, as `transferred`.

`mode` is optional, and unsaid the runtime picks: **cold** for a caller on a SIP leg, **warm** for
one in a browser. Ask for one by name and it is refused rather than quietly swapped — the two
leave different calls behind. A written conversation has no line at all: `call.transferred` comes
back with `ok: false`, `mode: null`, and says to ask for a person instead.

## Asking for a person

`call.attention` is the one that does not send the caller anywhere. `{reason, wait_s}` — the reason
is what a supervisor reads before taking the line, and `wait_s` has no default, because how long a
caller will hold is the app's to decide.

While the ask is open the caller is on hold (a spoken call plays the melody; a thread simply goes
unanswered), and the state carries it: `state.attention` is `open`, and so is the `attention` of
every session line, which is how a list shows a caller waiting.

It ends one of three ways, all of them `attention.answered` or the state's own fold of the ending:

- a supervisor sends `takeover` (`supervisor.verb`, `supervise` scope) — `ok: true`, with who;
- `wait_s` passes with nobody there — `ok: false`, and the agent has the caller back;
- the caller hangs up first — the ask lapses with the call.

A tool that asked for a person is still running while the caller waits, so **the tool's own
timeout must outlast `wait_s`**; the runtime does not stretch it. And a tool that comes back while
a supervisor holds the line produces no reply: the result is in the log and in the history, but
the model does not speak over the person.
