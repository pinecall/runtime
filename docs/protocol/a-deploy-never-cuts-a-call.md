# A deploy never cuts a call

A live call belongs to the runtime, and its durable record is its log. No process on the path (the
gateway, the worker, the tenant's app, the page) holds in memory something the call needs and
cannot get back. The caller never hears any of it: the audio is LiveKit's and the worker's, and
neither is touched by the others restarting.

## A call is its agent's, not a socket's

A call is handed to one app socket when it opens and served by it **until it goes**. When that
socket closes the call is **parked**, and handed at once to the socket that would take a new call
of that agent in that scope, or to the next process that registers it. A console
(`takes_unclaimed: false`) never takes one. The socket that takes it hears `call.attached` first,
`{app, started, state, seq, claimed}`, then every `tool.call` still waiting for an answer, and sends
the call's whole prompt and tools again: the log keeps a prompt's hash, not its text.

## A process that leaves on purpose

`agent.drain` on the socket: it is handed no new call, each call it served goes to another socket
or waits parked, and `agent.draining {app, env, handed, parked}` lands on the agent's log. It still
holds the agent, so the tools it is running answer. `pinecall start` drains on `SIGTERM`.

A tool asked while nobody holds the agent is written as `tool.call` and waits its own `timeout_s`
for the next socket.

## The gateway restarts

The gateway keeps the calls it serves in memory; the worker holds each call and the context it
opened it with. A door that names a call the gateway is not serving answers `404`, and the worker
says the call again, once, `POST /v1/calls/{call}/reopened {agent, context}`, and asks again: no
quota asked, no token spent, no second `call.ringing`; `409` for a call already sealed. While the
gateway is away the worker retries an entry, the seal, a tool and the command stream, backing off
from half a second to five, for as long as nothing answers or a `5xx` does; a `4xx` is an answer. A
tool retries within its own deadline, the seal within thirty seconds.

A retried entry is written once when it travels in a batch, `POST /v1/calls/{call}/entries
{after, entries}`: a call's log has one writer, which sends its entries in order and says how many
the log already took from it, and the log's head counts them. A batch that starts where the count
stands is written, whole, under the next seqs; the same batch sent again because its answer was
lost is answered with the seqs it was given and writes nothing; anything else is a `409` that says
both counts. Each entry keeps the worker's `ts`, when it happened, clamped to the gateway's clock.
The gateway's own entries take seqs and are not counted.

The worker writes its call's entries through this door and no other: what is queued goes as one
batch, and a batch retried goes again unchanged, after the same count, so a retry can no longer
write an entry twice. A `409` refuses the whole batch and the call goes on with the next. The
entries a worker writes outside its session (the overflow's sentence and its `call.ended`, a
command the session refused, the `call.ended` of a leg nobody answered) still take the one-entry
door, which counts nothing. A written call's session batches too, straight to its log; taken up
after a restart, it goes on from the count its log's head keeps.

## A written call

A chat over `WS /v1/chat` or a WhatsApp thread runs in the gateway's own process, and a restart ends
its session, never the call. It is taken up the next time it is spoken to: `WS
/v1/chat?call=<id>` dials again naming the call; a WhatsApp contact's next message finds their open
call. A WhatsApp message that arrives while nobody holds the agent is written on the agent's log as
`message.waiting` and answered, in order, the moment a socket holds it again (`message.taken` says
on which call); one older than Meta's 24-hour window is let go. The session is rebuilt from the
log, and the app's socket hears `call.attached`.

## A call nobody is running

The reaper looks every minute. A call in a room with no **agent** left in it, quiet five minutes,
is ended as `drained` and its room taken down. A written call no process serves is ended as
`timeout` once quiet as long as its door waits: five minutes for a chat, two hours for WhatsApp.

## The page

A page follows its call with the `log_token` its server's mint answered, straight from the gateway.
A gateway restarting cuts the stream; it reconnects with `Last-Event-ID` and misses nothing.
