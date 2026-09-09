# Tools and the state: `when` is the primitive, `stage` is how it reads

Written 2026-09-07, after the first real voice call. Bernardo asked the question the design
had not written down: "un `@tool` puede solamente activarse si `state.stage == 'something'`".
It was already there — as a predicate. What was missing is the spelling, and the page that
says out loud what tools and state have to do with each other.

## One mechanism

`when(state)` is the only visibility there is. It is asked of a snapshot of the class on every
state change (`packages/pinecall/src/agent/tools.ts`, `visibleToolsOf`), and when the list it
answers with is different from the one this call was last sent, the bridge hands livekit the
new list (`runtime/connect.ts`, `sync`). Nothing else hides a tool: not the prompt, not the
model, not a flag on the wire. The first real call is the proof — `tools.changed visible:
[findPatient, transfer]`, then `[freeSlots, transfer]` after `findPatient` ran.

`stage` does not add a second one. `@tool({ stage: "book" })` is lowered, in the decorator, at
declaration time, to `when: (s) => stages.includes(s.stage)`; the registry stores a `when` like
any other and `visibleToolsOf` learned nothing. A declaration that writes both is visible where
both hold — the stage says which part of the conversation this is, the predicate says whether
there is anything to do there:

```ts
@tool({ stage: "book", when: (s) => s.slots.length > 0, confirm: "…" })
```

That is the whole implementation, and it is the reason there is no state machine in this
framework. A stage is a plain field of the tenant's state: assigning it inside a tool writes
`state.changed`, re-renders the view, moves the visible tools, and shows up in the console's
STATE panel beside `patient` and `slots`. Nothing is hidden in the framework, which is the
thesis — fields are state.

## What a tenant can rely on

- A tool with neither `stage` nor `when` is always there. `transfer` is that tool, and it must
  be: the way out of a conversation cannot itself be gated.
- A tool with `stage: "book"` cannot be called in `"identify"`, because the model never sees
  it. This is not a refusal after the fact; the list sent with the request does not contain it.
  Haiku invented a `transfer` call on the first real call precisely because it WAS visible.
- Moving the stage is an ordinary write, so it is authored, logged and replayable like every
  other. `pinecall eval` replays a call by replaying its state.

## Types are contracts

`stage:` on a tool is typed against the class's own `stage` field, so a misspelling is a
compile error and not a tool that never appears:

```
error TS2820: Type '"identifi"' is not assignable to type '"book" | "choose" | "identify" | …'.
Did you mean '"identify"'?
```

`StageOf<T>` reads that union off the class the decorator was handed — the same inference that
already gave `when` a typed snapshot. A class that names a stage on a tool but declares no
`stage` field gives `never` there, so the compiler refuses it first; a class written in
JavaScript is refused at mount instead, by the sentence `toolsOf` throws, with the field to add
written in it. Both doors, one rule.

`Stages<"identify" | "choose" | "book" | "done">` is the alias the field is written with. It is
`Named` and nothing else — it exists so the four words are typed once, where the reader looks
for them, and so `stage: Stages<…> = "identify"` reads as a sentence.

## The model never learns about stages

Nothing is appended to the prompt for a stage. The model is not told that phases exist, which
ones there are, or which one it is in: it is handed fewer tools, and that is the whole message.
A tenant who wants the model to know says so in the view, in their own words.

The person writing the class is the one who needs to see the machine, so `pinecall run`
(`--show-prompt` and the `s` key) and `pinecall prompt --state` print, under the three prompt
regions, the stage and every tool with what gates it:

```
── tools ── stage: book

  ○ findPatient  identify
  ● freeSlots    choose · book
  ● book         book
  ● transfer     always
```

A filled circle is a tool the model can call right now. A staged tool that its own stage does
not bring back says `when(state) says no` — which is the answer to the only question this page
is ever opened for.

## What is not decided here

Whether the framework should offer livekit's `AgentTask` and handoffs when the phases stop
being four words and become a real graph — a different class per phase, with its own prompt and
its own tools. Gating is the norm until somebody measures the other one: `tk-eb0788` (ms-10)
owns that question. Until then, if a tenant needs `slots.length > 0 && !done`, they write
`when`; `stage` is for the phases that have names.
