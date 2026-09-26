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
 "data": {"app": "app_9f…", "started": {…the call.started data…}, "state": {…}, "seq": 56,
          "claimed": "4821"}}
```

| field | |
|---|---|
| `app` | the socket that serves the call from now on |
| `run_init` | the call's `call.started`: who, where, when |
| `state` | the agent's state as the call's last `state.changed` left it |
| `seq` | the last entry of the call before it changed hands |
| `claim_code` | the code a page showed that the call claimed ([codes.md](codes.md)), or `null` |

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

## A written call

A voice call — in a browser or on the phone — runs in a worker, and a web chat through
`@pinecall/room` joins the same kind of room: all of them go on through a gateway restart as above.
A call written over `WS /v1/chat` (`pinecall chat`, the console) or on WhatsApp runs in the
gateway's own process, and a restart ends its session — never the call, whose log is whole and head
row unsealed. It is **taken up** the next time it is spoken to (`live/resuming.py`):

- **`WS /v1/chat?call=<id>`**: the caller's socket dropped with the gateway, and it dials again
  naming the call. `pinecall chat` and the console do that by themselves, for about a minute.
- **WhatsApp**: the contact's next message finds their newest call with the agent still open, and
  goes on it. One that went a whole idle period (two hours) without a word while nobody was
  watching is ended then, as it would have been, and the message opens a new call.
- **A WhatsApp message nobody can answer yet** — it arrived in the seconds no app held the agent,
  a deploy or the gateway starting — is never dropped. It is written onto the agent's own log as
  `message.waiting` and answered, in order, the moment a socket holds the agent again (the gateway
  looks every two seconds, and a new message from the same person brings theirs along first);
  `message.taken` says on which call. The log is the queue, so a second restart loses none: a
  gateway that starts reads back what is still waiting. One older than WhatsApp's 24-hour
  customer-service window can no longer be answered with free text, and is let go with
  `message.taken` naming no call.

Taken up, the session is rebuilt from the log — the conversation as the model reads it (turns,
tools and what they returned), the state, the numbering — no quota is asked again and nothing is
said; the app's socket hears `call.attached` and sends its prompt and tools again.

## A call nobody is running

A call can still lose everybody who could end it: a worker killed with no time to drain, a written
call whose caller never came back. The gateway's reaper (`api/calls/reaper.py`) looks every minute:

- **A call in a room** is running while an **agent** is in its room. A room with only people left
  in it — the caller's tab still open, a supervisor's seat — is nobody's call: after five quiet
  minutes it is ended as `drained`, and the room is taken down, so whoever is still in it is told.
- **A written call** is running while a gateway process serves it. One that no process serves —
  its gateway restarted and its caller never came back — is ended as `timeout` once it has been
  quiet as long as its door waits: five minutes for a chat, two hours for a WhatsApp thread.

## The page

A page follows its call's log with the `log_token` its server's mint (or dial) answered
([tokens.md](tokens.md)), straight from the gateway — the call's reads answer any origin — and
plays the recording with it at the end.
Nothing on the tenant's server remembers which calls it opened, so the tenant's server restarting
touches no call a page is showing. A gateway restarting cuts the page's stream; it reconnects with
`Last-Event-ID` and misses nothing (§3 of [gateway-api.md](gateway-api.md)).

