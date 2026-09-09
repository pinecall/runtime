# The class meets the world: visibility, events in, speaking, the room

The class never touches LiveKit. It reads the room as facts, acts on it with named verbs,
and the outside world reaches it through one hook. What that costs is below.

## Visibility: a decorator, and a map for the class that wants one

`@state({ visibility: "public" })` is a **legacy** property decorator — the toolchain has no
others (see [agent.md](agent.md)) — so it is `(prototype, name)` with no descriptor, which is
all it takes: a field decorator that only records a name in a per-prototype map does not need
one. The map is keyed by prototype exactly like the tool registry, so a subclass inherits its
parent's declarations by walking the chain.

Decorator over a `static visibility = { slot: "public" }` map because the declaration belongs
next to the field: a map is a second place to keep in step, and the field it names can be
renamed without it. But the map cost nothing to keep — `visibilityOf()` reads both, map last —
and it is the door for a class generated at runtime, where there is no syntax to decorate.

An undecorated field sends **nothing**. `tenant` is the wire's own default for a field never
declared, and a framework that re-sends the default is inventing a declaration the tenant never
wrote. `mount()` therefore sends `state_fields` only for the fields somebody decorated.

## Events in: the gate is the pair, not the name

`static events = { "slot.released": { from: ["app"] } }` becomes `AgentConfig.events`, and
`onEvent(name, data, meta)` is the only door in. The runtime asks `accepts(ctor, name, source)`
before the hook runs, so an event declared `from: ["app"]` arriving from a participant's browser
never reaches it: same name, different sender, somebody else's event. The gateway asks the same
question before the entry touches the log; we ask it again because a log can be replayed and a
hook that runs on an undeclared name is a hook the tenant never agreed to.

An event that fails the gate is dropped with **one** `console.warn` per (call, name). A backend
that sends an undeclared event sends it on every tick, and a log that repeats itself is a log
nobody reads.

`onEvent` is a lifecycle hook, so it may write state: the write is authored `event:<name>` via
`withAuthorAsync`, and `changes(agent)` shows the event by name in the author column.

## The cause: the wire has no room for it, so it goes in the log

`state.set` carries `state` and `changed` and nothing else — no cause field, checked against
`protocol/schema/commands/state.set.json`. So the bridge sends the state as it always did and
writes one extra line beside it: `call.log("state.cause", { field, kind: "event", name, seq })`,
only while an event is being dispatched. The reader joins them by the field name and the
adjacent seq. If the wire ever grows `state.set.cause`, this line is what moves into it and
nothing else changes.

`meta.seq` is **not** the entry's seq. `camelEvent` in the sdk hands listeners `{type, data}`
and drops the entry's own `seq`, so the hook is told where the fact sits in *this call's* stream
of outside facts (1, 2, 3 …). The exact fix is on the sdk side: let a listener see the entry.

## "Acknowledged" means the turn it lands as

`this.reply(instructions)` and `this.say(text)` return a `Promise<boolean>`. The command frame
itself is fire-and-forget — the sdk's `send` is `void` — and the gateway answers commands with
the entries they produce, which for both of these is `turn.agent`. So the promise settles when
the next `turn.agent` for this call arrives: `true`, the words were spoken.

It never rejects. A line that hung up mid-sentence must not leave a tool awaiting forever, and a
hang-up is not something the tool can act on, so after 30s the wait answers `false` — sent,
never confirmed — and the call goes on. The words themselves are not returned: the log has them.

## The room, and the four verbs that exist

`this.call.room` is reduced from `participant.joined` / `left` / `speaking`, never asked of
LiveKit. `caller`, `has(kind)`, `participants`. The verbs are the ones the wire already carries,
each exactly one command: `room.invite`, `room.send`, `participant.mute`, `participant.remove`.
There is no unmute on the wire and we did not invent one — a leg that must speak again is
invited again — and there is no raw escape hatch: a need the room cannot express is a new
command with a name.

They are sent through the sdk **agent's** `command(type, call, data)`, which is public, rather
than through `Call`, which today has methods only for say/reply/state/log/event. Adding four
methods to `Call` is the tidier home and belongs to whoever owns the sdk; nothing here changes
when they land.

`this.call.history` is the finished turns from `turn.user` / `turn.agent`, with `collapse(summary)`
compacting the reading and never the log.

## `this.call` is a getter, not a field

The call is kept in the framework's own `WeakMap`, and `Agent` exposes it as a getter on the
base prototype. A field would be state: it would be snapshotted, diffed, rendered by a view and
sent on the wire, which is exactly wrong for a live object full of methods. As a getter on
`Agent.prototype` it is invisible to `snapshot()` (which stops at `Agent`) and to the views.

The cost is that `call` is a reserved name: a class declaring its own `call` field is refused at
the first write, because the prototype's accessor has no setter. That is the right failure — the
name has one meaning here — and it is the reason the framework says so out loud in this doc.

## What convo (~/prueba-abai) taught, and where we went the other way

- **The room is read, never asked.** `convo.session.rooms` calls LiveKit's API for the live view
  and pays a socket per read. We reduce from entries we already receive, so `this.call.room` is
  free, synchronous and identical on a replayed log.
- **One hook, and the gate before it.** convo dispatches outside facts into the session and the
  handler decides what it recognises. Ours refuses at the door on the declared (name, source)
  pair, so an undeclared name is a platform decision rather than an `if` the tenant forgot.
- **`generate_reply` vs speaking verbatim** is convo's distinction (`control.steer` guides the
  model; a notice is said). We kept both words — `reply` and `say` — and made both awaitable,
  which convo's fire-and-forget verbs are not.
- **Its `Event(seq, kind, t_ms, payload)`** is why our history keeps only what was said and
  leaves the metrics in the log: two readings of one stream, not two stores.
- Where we went the other way: convo's supervision verbs live in an HTTP desk over the session.
  Ours are methods on the call the class already holds, because a verb the tenant cannot reach
  from inside a tool is a verb they will build a second time.
