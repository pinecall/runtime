# The runtime bridge: the class on the wire

`packages/pinecall/src/runtime/` is the only place where the tenant's class and
`@pinecall/sdk` know about each other. The class never imports the client; the client never
imports the framework. `mount(ClinicaNorte, { pc, view })` is the whole seam.

## One instance per call

`mount` builds one throwaway instance to read the declaration — the doors, the language, the
model, the tools — and then never uses it again. Every `call.started` builds a fresh one,
`seal`s it and keeps it in a map keyed by the call id; `call.ended` runs `onEnd`, drops the
listeners and forgets it. Two callers therefore share nothing: not a field, not a change log,
not a rendered prompt.

convo did the same thing with a `TenantContext` per LiveKit job, and got process isolation for
free because LiveKit forks a process per room. We do not fork: one node process serves every
call at once. That is why the author of a state write is carried by `AsyncLocalStorage` in the
agent seam and not by a module-level stack, and why nothing in this bridge is a module global.

## Send only when the text changed

The rule this card exists for. Every field assignment reaches the bridge through
`onChange(agent, …)`, and each one does three things in order:

1. `call.setState(snapshot, [field])` — always. The state is the log's business, and the log
   wants every change with its author, not a de-duplicated version of it.
2. render the view again — `render(agent, view, { call })` → `{ static, history, dynamic }`.
3. compare each region with what **this call** was last sent, and send only what differs:
   `prompt.set static`, `prompt.set view`, then `tools.set`.

The comparison is on the rendered text itself, kept per call in a small `Sent` record, never
on the state that produced it. A state change is not evidence that the prompt changed: the
clinic writes `slot` on the way to writing `booking`, and a view that reads neither must not
cost the provider a cache miss. The tools are compared as `JSON.stringify(visibleTools())`,
because the only thing that moves mid-call is which specs a `when(state)` lets through, and a
spec is small, ordered and already in wire shape.

`history` is never sent: the wire's `PromptRegion` is `static | view`, and the middle region
is the platform's append-only transcript, not the app's to write.

The opening send is the same `sync()`: the listeners are attached **after** `onCall` has run
and the first prompt has gone out, so a hook that restores a previous conversation costs one
`prompt.set`, not one per field it restored.

## Validation lives in the bridge, and a failure is a sentence

`tools.ts` checks the model's arguments against the tool's own `parameters` — the JSON Schema
the agent seam derived from the signature — and it checks only what that schema can honestly
state: a missing required name, and a value whose JSON type is not the declared one. Anything
deeper (a `Slot`'s own fields) is the method's, because the framework never saw the type behind
the alias.

A failure — bad arguments, an unknown tool, a call already hung up, or the method throwing —
becomes a `ToolFailed` whose message is a sentence a model may read. The SDK's tool runner
awaits our function inside its own `try`, so that rejection lands as one `tool.result` carrying
`error`, against the model's own `call_id`, and the next `tool.call` is served normally. The
loop never throws. This is convo's `ToolError` idea with the plumbing removed: convo had to
raise into livekit-agents' own loop, we hand a rejection to a client that already promises
"one tool.call, exactly one tool.result", and we do not duplicate that promise here.

The method is invoked through the instance (`agent[name](...)`), never through
`declaration.method`: the decorator's wrapper is what authors the writes inside it. Arguments
arrive as an object and the method takes positional parameters, so the schema's property
order — which is the signature's order — turns one into the other. `preview: 2` cuts the
**result the model reads**; the state field keeps every row, which is how the agent offers a
third slot without asking the agenda again.

## What convo taught, and what we did differently

- **Kept**: a tool call is catalogue → check → run → cut → log, in that order, and every step
  that can refuse says so in words the model can speak.
- **Kept**: the prompt is rendered from state, not accumulated.
- **Dropped**: stages. convo re-sent the whole instruction set on a stage handoff, which is a
  re-render whether or not anything the model reads changed. We have no stages and no handoff:
  visibility is `when(state)`, and a re-render that produces the same text is not sent at all.
- **Dropped**: the executor's own logging. convo's executor wrote `tool.call` / `tool.result`
  into its log; here the platform writes both from the wire, and the app's own lines go through
  `this.log(name, data)` → `call.log`, which is the only channel an app has into the log.

## The small decisions

- **The slug** is the class name in kebab-case (`ClinicaNorte` → `clinica-norte`) unless the
  class declares `static slug`. A file name would be prettier and is not available at runtime.
- **There is no `channel.add` command on the wire**, so nothing invents one: the `phone`,
  `whatsapp` and `web` fields become `AgentOptions.routes` at register time, once. A door is
  declared by being truthy; `web = true` is a route with `number: null`.
- **`llm = "haiku"`** and **`voice = "carolina"`** are how the design writes them and the wire
  wants a provider beside the name. `"provider/model"` says both; a bare name is Anthropic's
  (whose short names those are) and ElevenLabs'. An object written out in full passes through.
  If a second provider ever uses the name `haiku`, this is the line that has to change.
- **`stateFields`** carries only what the class declared, as a `static visibility` map. A field
  nobody declared is `tenant` by the wire's own default, and repeating that would be the
  framework inventing a declaration nobody wrote.
- **`instructions` is not sent at register**: the static region is rendered per call and sent as
  `prompt.set static`, because `render` is where the standing rules and the tools' own
  descriptions come together and there is no second copy of that logic.
