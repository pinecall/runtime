# A deploy never cuts a call

A live call belongs to the runtime, and its durable record is its log. No process on the path — the
gateway, the worker, the tenant's app, the page in the browser — may hold in memory something the
call needs and cannot get back. This page is what each of them does when it restarts in the middle
of a conversation, and what the one after it does. The caller never hears any of it: the audio is
LiveKit's and the worker's, and neither is touched by the others restarting.

## A call is its agent's, not a socket's

An app process holds an agent over `WS /v1/apps` ([gateway-api.md](gateway-api.md) §1). A call is
handed to one socket when it opens — the one it named, or the newest that takes unclaimed calls —
and that socket serves it **until it goes**, not for the call's whole life.

When the socket serving a call closes, the call is **parked**: served by nobody, still running. The
gateway hands it at once to the socket that would take a new call of that agent in that corner, if
there is one; otherwise it waits for the next process that registers the agent. A console
(`takes_unclaimed: false`) never takes one.

The socket that takes a call over hears, before anything else of it:

```jsonc
{"seq": 57, "call": "call_ab…", "agent": "clinica-norte", "type": "call.attached",
 "data": {"app": "app_9f…", "started": {…the call.started data…}, "state": {…}, "seq": 56}}
```

| field | |
|---|---|
| `app` | the socket that serves the call from now on |
| `started` | the call's `call.started`: who, where, when |
| `state` | the agent's state as the call's last `state.changed` left it |
| `seq` | the last entry of the call before it changed hands |

Then every `tool.call` still waiting for an answer, sent again with its own seq: the model is still
waiting on it, and a `tool.result` with that `call_id` lands where it always would. The socket must
send the call's **whole prompt and its tools** (`prompt.set`, `tools.set`): the log keeps only a
prompt's hash, and nothing the old process sent reached this one. `pinecall` does all of this for a
class; its state is restored from `state`, and `onCall` does not run again.

`call.attached` is in the log, so a reader sees when a call changed hands and to whom. The public
projection drops it; the tenant projection masks its `state` as it masks `state.changed`'s.

## A process that leaves on purpose

A deploy stops the old process and starts the new one. The old one says so first, for every agent
it holds:

```jsonc
// → the command
{"type": "agent.drain", "agent": "clinica-norte", "call": null, "id": "9", "data": {}}
// ← the answer, written to the agent's log once its calls have moved
{"type": "agent.draining", "data": {"app": "app_9f…", "env": "production", "handed": 1, "parked": 1}}
```

From then on the socket is handed no new call, and each call it served went to the socket that
would take a new one (`handed`) or waits for the next process that registers the agent (`parked`).
It still holds the agent, so the tools it is running answer; it is sent no new one. It closes once
they have, and `agent.detached` follows as always. `pinecall start` does this on `SIGTERM` and
`SIGINT`: the process needs a few seconds of grace between the signal and the kill.

## A tool asked while nobody holds the agent

A tool the worker asks while the call is parked is written as `tool.call` and **waits** its own
`timeout_s` for the next socket, which is sent it with `call.attached`. A deploy that takes a few
seconds costs a tool that long; one that takes longer than the tool's timeout answers the model
with the timeout's error, as a slow app would. Only an agent nobody in the org ever registered is
refused at once (`409`).

## The gateway restarts

The gateway keeps the calls it serves in memory, and a restart forgets them. The worker does not:
it holds each call and the context it opened it with. Every door that names a call — appending to
its log, sealing it, its commands, its tools, a lookup — answers `404` for a call the gateway is not
serving, and the worker then says the call again, once, and asks again:

```
POST /v1/calls/{call}/reopened      {agent, context}      → 204
```

The gateway checks the call's head row — it exists, it is the key's org's, it has not sealed (`409`
if it has) — and serves the call as it was: no quota asked, no token spent, no second
`call.ringing`. The socket holding the agent hears `call.attached`. The worker's command stream,
cut by the restart, is opened again the same way.

The gateway itself stops in seconds — uvicorn gives the requests in flight five, systemd kills at
thirty — because it drains nothing: nothing it held is lost to the call.

While the gateway is away — between the old process stopping and the new one answering — the
worker asks again rather than giving up: an entry of the log, the seal, a tool, and the command
stream, each on a backoff from half a second doubling to five, for as long as the gateway answers
nothing or a `5xx`. The command stream is opened again whenever it ends while the call runs — cut,
or ended cleanly by a gateway stopping with grace — because the worker itself is what seals the
call, and it closes that stream when it does. A `4xx` is an answer and is never asked again. A tool is asked again only within
its own deadline, which the model is waiting on; a seal within thirty seconds, after which the
reaper seals the call once its room is gone. An entry whose answer was lost in flight — written,
and the connection cut before the gateway said so — may be written twice; one is never lost.

## The page

A page follows its call's log with the `log_token` its server's mint (or dial) answered
([tokens.md](tokens.md)), straight from the gateway — the call's reads answer any origin — and
plays the recording with it at the end.
Nothing on the tenant's server remembers which calls it opened, so the tenant's server restarting
touches no call a page is showing. A gateway restarting cuts the page's stream; it reconnects with
`Last-Event-ID` and misses nothing (§3 of [gateway-api.md](gateway-api.md)).

