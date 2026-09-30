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

What a call is, its context and the config it runs on, is kept in Postgres when it opens, so a
gateway that restarted, or another gateway of the box, serves any door of it from one read the
first time a door asks, and from memory after. A call sealed is `409` at every door. A call an
older release opened kept nothing: its door answers `404`, and the worker says the call again,
once, `POST /v1/calls/{call}/reopened {agent, context}`, and asks again: no quota asked, no token
spent, no second `call.ringing`. While the
gateway is away the worker retries an entry, the seal, a tool and the command stream, backing off
from half a second to five, for as long as nothing answers or a `5xx` does; a `4xx` is an answer. A
tool retries within its own deadline, the seal within thirty seconds. A tool call is run once per
call id: a retry joins the round trip still running, and one that finished, before the answer was
lost or the gateway restarted, is answered with the `tool.result` its log holds and never reaches
the app a second time.

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
entries a worker writes outside its session take the same writer: a command the session refused
is an `error` on the session's own, and the `call.ended` of a leg nobody answered and the
overflow's sentence and `call.ended` go through one the job opens with the call. The job sent into
a room whose worker died writes the sentence through a writer that follows on from the dead
worker's: the dispatch carries the count the log's head kept (`entries_written`). The one-entry
door, `POST /v1/calls/{call}/events`, still answers for a worker of an older release, and counts
nothing. A written call's session batches too, straight to its log; taken up after a restart, it
goes on from the count its log's head keeps.

## A release of the workers

A worker told to stop takes no new call, finishes the ones it holds (up to ten minutes) and
leaves; nothing moves a call from one worker to another. So a release keeps a fleet open by never
stopping its last worker: the box runs two per world, `pinecall-worker-a@` and `-b@`, and
`release.sh` restarts every `b@`, then every `a@`. Each unit is `Type=notify`: `systemctl restart`
returns once the old process drained and the new one is registered with LiveKit and heard by the
gateway, and only then is the other stopped. While one drains the other takes every new call of its
world, so no caller of a deploy hears the overflow's sentence. A fleet of machines is replaced by
generation the same way: the new machines first, then the old ones cordoned
([scaling.md](../scaling.md)).

## A written call

A chat over `WS /v1/chat` or a WhatsApp thread runs in the gateway's own process, and a restart ends
its session, never the call. It is taken up the next time it is spoken to: `WS
/v1/chat?call=<id>` dials again naming the call; a WhatsApp contact's next message finds their open
call. A WhatsApp message that arrives while nobody holds the agent is written on the agent's log as
`message.waiting` and answered, in order, the moment a socket holds it again (`message.taken` says
on which call); one older than Meta's 24-hour window is let go. The session is rebuilt from the
log, and the app's socket hears `call.attached`.

## A call nobody is running

A worker that dies mid-call is heard of at once: LiveKit's webhook says its agent's connection was
lost, and the gateway ends the call as `drained`, offers the caller a call back and sends a job into
the room that says the overflow's sentence and closes it ([scaling.md](../scaling.md)). What that
leaves, and a call whose caller had already gone, is the reaper's.

The reaper looks every minute. A call in a room with no **agent** left in it, quiet five minutes,
is ended as `drained` and its room taken down. A written call no process serves is ended as
`timeout` once quiet as long as its door waits: five minutes for a chat, two hours for WhatsApp.

## The page

A page follows its call with the `log_token` its server's mint answered, straight from the gateway.
A gateway restarting cuts the stream; it reconnects with `Last-Event-ID` and misses nothing.
