# dispatch — many app sockets on one agent, and a call that sticks to the one that took it

## The decision

An agent may be held by **many app sockets at once**. `Registry._agents` is slug → the
registrations holding it, in the order the sockets claimed it. The rule that refused a second
socket is gone. Three rules replace it:

1. **A new call takes the newest registration** — its socket, and its config.
2. **A call chooses its socket once, when it opens, and keeps it for its whole life.**
3. **Whoever opens a call may name the socket it wants** — `WS /v1/chat?app=<id>` and an `app` in
   the `POST /v1/calls` body — and that id came back to the app in `agent.registered`.

What did not change: a **door** still answers for exactly one agent (`_refuse_a_taken_door`), the
log is still the truth, and delivery is still the log's own fanout.

## Why: `rails console` cannot be refused by `rails server`

We set out to make this CLI feel like Rails. `rails server` is `pinecall run`; `rails console`
is `pinecall chat`. The analogy broke on a real thing rather than on taste.

`rails console` boots your app in **its own process** and does not care whether a server is
running, because the resource the two share — the database — accepts many connections. Ours — the
agent's slug at the gateway — accepted exactly one, and `Registry._refuse_a_second_socket` said so
in words: "already registered on another socket, which keeps it until it disconnects". So
`pinecall chat` was refused while `pinecall run` was up, and Bernardo hit it within a minute of
using it for the first time.

The same one-socket rule is what makes production impossible. Two replicas of one agent cannot
both serve it. A deploy cannot start the new process before stopping the old one. The workaround
was already written down as a virtue, in `packages/sdk/src/agent.ts`: "a rolling deploy's new
process retries until the old one lets go". A comment that describes a limitation we chose is a
limitation we can un-choose, and this is us un-choosing it.

## Newest wins, and only for a call that has not started

Mid-deploy the two processes may declare different tools, a different voice, a different prompt.
The newest registration is the one being rolled out, so **a call that starts now runs on the
newest socket's config**. That is a real choice and it has a real losing case: a deploy that is
being rolled back takes calls on the broken code until the old process re-registers. The
alternative — oldest wins — has the worse one: a deploy would never take effect until every old
process died, which is the behaviour we are here to delete.

"Newest" is the order the **sockets** claimed the agent, not the order of the last frame each one
sent. A socket that re-registers to correct its doors keeps its place in the queue: it is the same
process, not a newer one. It also keeps the config it had already declared, which is the rule that
was there before and is unchanged.

A socket that joins an agent someone else is already holding **starts from that agent's current
config** and corrects it with the `agent.configure` that follows one round trip later. The window
between the two frames is small and a call can land inside it; a call that lands there must find
the agent the tenant declared a millisecond earlier, not an agent with no instructions at all.

## A call is bound to one socket, and the binding is the pump

`Live.serve(call, agent, log, app)` is handed the socket its door already chose — see the next
section — and never resolves one itself. It records it on `Served` and gives the call's
subscription to one `_feeding` pump that closes over that one `send`. Nothing resolves an agent to
a socket again, so a register that lands mid-call cannot move a live conversation.

Before this card the resolution was already a single `_sender_of(agent)` at the moment the call
opened — with one socket per agent the two designs are indistinguishable. With many, "resolve once"
is the whole rule, and it is now written where it happens rather than implied by the shape.

**A call whose socket disconnects keeps running and its tools stop being answered.** The pump's
next send raises, the subscription closes, and the call goes on writing its log with nobody
listening on the app side — exactly what happened before this card when the only app died. The
other socket does **not** inherit it. Handing a live call over to another process is not in this
card and is not designed: it would need the new process to rebuild the tenant's instance for that
call from the log, which is a milestone, not a paragraph.

## One question, one answer, whichever door the call came through

A call reaches an app through exactly two doors: `WS /v1/chat`, where the caller is a browser or
`pinecall chat`, and `POST /v1/calls`, where a worker says a call has started — every phone call,
every web call, and the operator's `pinecall-runtime worker talk`. Both of them ask **`Registry.serving(slug, app)`**, the one
function that answers "which process serves this call": the socket the door was told to claim, or
the newest one holding the agent. An `app` that names no holder of that agent is **refused** at
either door — the chat socket closes with the reason, the worker's POST is a 409 — and never
quietly ignored.

Claiming had to reach the worker's door too, and the reason is not symmetry. If only the chat door
could claim, `pinecall-runtime worker talk` would work while it happened to be the newest socket and stop the
moment a `run` registered after it — right by accident.

What claiming does **not** do is keep a stranger's call out of a terminal. A call that names a
socket goes to that socket; a call that names none went to the newest holder, and a `pinecall chat`
open in somebody's terminal is the newest holder. So while claiming was up, a **real phone call
still landed on that terminal's process instead of on the deployed one** — reproduced with a
`run` registered first and a chat holding its socket: a `WS /v1/chat?agent=clinica-norte` with no
`?app=` ran its whole call, tools included, in the chat process while `run` saw only a pong. Two
halves of one rule, and only one of them had been written. The other half is the next section.

So the wire says it twice, in the shape each door already has: `?app=<id>` on the chat URL, and an
optional `app` beside `agent` and `context` in the `POST /v1/calls` body. That body is a local
`WireModel`, not a generated shape — only `agent.registered` is generated, because only it crosses
to the SDK. Into a worker process the id travels by **environment** — `PINECALL_APP`, beside the
`PINECALL_GATEWAY_URL` and `PINECALL_AGENT` that already go that way — because livekit hands a job
its entrypoint by name and nothing the parent parsed reaches it. An operator running
`pinecall-runtime worker talk` sets it; no verb of the tenant's CLI does.

`Live` was left holding no registry at all. It used to resolve the agent to a socket itself, which
was the second place that answered the question; now `serve()` is told the socket and obeys. A
table that keeps no truth of its own cannot disagree with the one that does.

## `?app=<id>` is what makes `chat` the Rails console

`pinecall chat` holds **both** ends: an app socket, where the tenant's class is mounted and their
breakpoints live, and the caller's chat socket. It must be served by **itself**. Without a way to
say so, a breakpoint would land in whichever process happened to register last — usually the
server the developer is running in the other terminal, which is the opposite of what a console is
for.

So `agent.registered` carries back `app`, an opaque id for the socket that claimed the agent, and
the chat door accepts `?app=<id>`. It is honoured only when that socket is holding **that** agent,
and refused otherwise with a sentence naming both.

The fleet check is that same check. A slug answers for one fleet at a time — the second rule this
card had to write down, because the one-socket refusal was silently enforcing it before: with the
refusal gone, a socket keyed to another fleet could have registered a slug that was not its own
and taken over its doors and its calls. `_refuse_another_fleet` says it out loud. So a socket
holds an agent only if its key's fleet is the agent's fleet, and "this app is not holding this
agent" already covers "this app belongs to somebody else's fleet". One refusal, not two.

On the TypeScript side the id arrives as `agent.registered`'s `app`, typed by the generated zod
schema, and nothing in the SDK stores it yet. The card that makes `chat` hold both ends is the one
that needs a handle on it, and it will add one with the test that uses it.

## `takes_unclaimed`: a console holds the agent and takes only the call it opened

A socket says at register time whether the gateway may hand it a call that named no app.
`agent.register` carries `takes_unclaimed`, absent means yes, and `pinecall chat` is the caller
that says no — through `mount({ takesUnclaimed: false })` and the `takesUnclaimed` option on
`pc.agent()`. The name is what the field means **to the gateway**:
whether this socket takes calls nobody asked it for. A field called `console` would have been a
field about a CLI, and the gateway has never heard of one.

Only `Registry.serving(slug, None)` reads it — the unclaimed path, and the only question in the
process whose answer is "who will be handed this stranger's call". It walks the holders newest
first and returns the first that takes unclaimed calls. `of()` still means the newest socket
holding the agent, whatever it declared, because everything else that asks — the worker's config
door, the state-field projection at the log's sink, the door table — is asking what the agent IS,
not who answers for it. Making `of()` skip a console would have broken `pinecall-runtime worker talk` outright:
its worker fetches the agent's config by slug, and the only socket holding that agent is the
console.

A console is a full holder in every other way. It registers, it declares, it configures, it is
listed in `holding()` and in `GET /v1/agents`, its `?app=` is honoured, and the call it opens runs
on it — which is the whole point of `pinecall chat` being the Rails console.

### When every holder is a console, the call is refused

The one thing the shape does not decide by itself: an unclaimed call arrives and every socket
holding the agent said it takes none. **It is refused** — `409` with `NO_UNCLAIMED` at
`POST /v1/calls`, and the chat socket closed with the same sentence — and the sentence names the
agent and says `start pinecall run`.

The alternative is to fall back to the newest console, and that is the bug itself with an extra
step: the case where it fires is exactly a developer with a `chat` open, a `run` that died, and
a customer on the phone. A rule that holds until the moment it matters is not a rule.

Letting the call run with no app socket is the other tempting answer, because that is what a call
whose app disconnected mid-setup already does, and that case stays as it was — a caller who is
already on the line is not hung up on for a race. But a console-only agent is not a race, it is a
standing state, and a call served that way is worse than a refusal for the person on the line: no
app socket is on the call, so every tool the model reaches for goes out to nobody and comes back
as a timeout, and the log fills with a conversation that could never have been completed. The
refusal happens before the caller has been greeted.

The honest losing case is the door table. Doors are claimed by an agent's newest socket, console or
not — a console mounts the same class and so declares the same routes — so a number an agent
answers still reads as answered on the routes screen while only a console is up, and the call
arrives, and dies at `POST /v1/calls` with that sentence in the worker's log. Making the console
drop the doors was rejected: a console is a second process of the same app, its declaration is the
app's own, and an agent whose doors flicker as somebody opens and closes a terminal would be a
worse thing to reason about than an agent that refuses one call with a sentence saying why.

## The socket id is minted, not `id(websocket)`

`SocketId` was `int` — `id(self._websocket)`, on the reasoning that two live objects are never the
same one and nothing had to invent an id. That reasoning ends where the id leaves the process:
`?app=` arrives from a client that read the value seconds or minutes ago, and CPython reuses an
address the moment the object at it is collected. A stale `?app=` would then be indistinguishable
from a live one and would name a socket somebody else now holds. So a socket mints `app_<12 hex>`
when it connects, it is unique for the life of the process, and it means nothing to anyone who did
not receive it.

## What this cost, and what it did not

`Registry` keeps the same three tables. Two of them are untouched; `_agents` holds a list per slug
instead of one registration, `of()` is its last element, `on(slug, app)` is the lookup every "is
this command coming from a socket that speaks for this agent?" check now uses, and `serving()` is
the one question the two doors that open a call ask: the socket the caller named, or the newest
holder that takes unclaimed calls.
`_free()` is gone: freeing a door was only ever half a rule, and the whole rule is
`_claim_doors(slug)` — **the doors an agent answers are its newest socket's, and only those**,
recomputed on every register and every disconnect. Doors are part of a declaration, so newest wins
there too: a rollout that dropped a number stops answering at it, and if that process dies the
number rings again for whoever is left. One socket leaving never takes a door the newest still
claims.

Nothing about auth, scopes, keys or the operator API moved.
