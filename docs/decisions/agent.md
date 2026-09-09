# The Agent class: reactive state, authored writes, tools from docstrings

The tenant writes one class. Its fields are what the agent remembers, its `@tool` methods
are what the model may do, and its JSDoc is what the model reads. Everything below is what
that costs and what it buys.

## Why a Proxy, and what it is not

`new Agent()` returns a `Proxy` over the instance. The `set` trap is the whole state API:
`this.patient = row` inside a tool is one change with a field, a previous value, a next value,
an author and a `seq`, and every listener — the console, the log, the view — hears it. The
alternative was a `setState()` call the tenant has to remember; a framework the tenant has to
remember is a framework the tenant gets wrong at 2am.

The Proxy costs three things, all of them declared. Field initializers land through
`defineProperty`, not `set`, so they never look like changes — which is what we want, because
the declaration is the baseline, not history. Object identity: `Object.is(prev, next)` is what
decides whether a write is a change, so an object rebuilt from the same row is a new value and
is logged as one; the framework does not deep-compare, and no view should assume it. And a
write from outside — `agent.slots = []` in a test, in a helper, in a `setTimeout` — throws
`UnauthoredWrite`, because a state change nobody authored is a change no log can explain.

`seal(agent)` is what turns the recording on. Field initializers run after the base constructor
returns, so the base class cannot know when the declaration ends; the runtime (and every test)
calls `seal()` once, after construction, before the call starts.

## The author rule

Two places may write state: a tool and a lifecycle hook. `@tool` wraps the method so that for
as long as it runs — awaits included — the author is the tool's own name; `runHook` does the
same with `hook:onCall`. Nothing else pushes an author, so nothing else can write. That is the
milestone's rule made mechanical rather than reviewed: `changes(agent)` is a list where every
row names a verb the tenant wrote, and `state.changed` on the wire inherits it for free.

`restore()` is the one deliberate exception: it authors itself when nobody else is running,
because putting a caller back where they left off is a legitimate write with no tool behind it.

## Docstrings: read the class, not a second declaration

The design says the prompt is the JSDoc above the method and the schema is the TS signature.
We took that literally. The toolchain leaves JSDoc comments inside the transpiled class body,
so `Ctor.toString()` gives us, at runtime and with no build step, every tool's docstring and
every parameter's **name**. What it does not give us is the TS **types** — those are erased —
nor the class's own docstring, which sits outside the class text.

So there are two doors and they are the same door. `describe(Ctor, source)` takes the class's
own `.ts` source and parses docstrings, parameter names and parameter types out of it; without
it, the runtime scrape fills in everything except the types (a parameter of an unknown type
becomes an open object schema, which is what a `Slot` or a `Patient` would be anyway). The
build-time transformer this seam did not write is exactly a call to `describe(Ctor, source)`
emitted next to the class — that is its whole interface, and `docsVersion()` already
invalidates any spec built from the poorer answer. An explicit `params: z.object({...})` on the
decorator overrides both, for the case where a signature genuinely cannot say enough.

### The parser is oxc's, not regexes and not a second TypeScript

The first version read the class body with regexes, and it lost the moment a signature was more
than `name: string`: a parameter whose type carries parens or commas of its own (`onDone: (row:
string) => void`) cut the list in half, and `slot: Slot` could never be followed to the interface
that says what a `Slot` is. A signature is a language; the only honest reader of one is a parser.

`parseClassSource` is now `parseSync(file, source, { sourceType: "module", lang: "ts" })` from
`oxc-parser` — a string in, an ESTree program with TypeScript nodes out, no files on disk and no
type checker — walked for the class (`ClassDeclaration`, or the `declaration` of an
`ExportDefaultDeclaration`), its `MethodDefinition`s and `PropertyDefinition`s, and their
parameters (`Identifier`, `AssignmentPattern` for a default). The runtime scrape did not go
anywhere: `Ctor.toString()` is valid TypeScript minus the types, so it goes through the same
parser and the same code path. Both doors are one door now, and there is no regex over source
text anywhere in the module.

What the parser buys us, per parameter: keyword types map to their JSON Schema type; an array
maps to `items`; a type literal expands inline; **an interface or a type alias declared in the
same file expands into `properties` and `required`**, recursively, so `book(slot: Slot)` finally
tells a model what a slot is; a union with `undefined` or `null` makes the parameter optional; a
union of string literals becomes an `enum`; and a function type is marked `callback` and left out
of `required`, because the model cannot write one.

Why oxc and not the TypeScript compiler API, which is the obvious answer: **TypeScript 7.0.2 —
the version this workspace type-checks with — ships no JS compiler API at all.** It is the Go
port; its package exports are `./unstable/{sync,ast,...}`, and the parser behind them is a Go
language server spawned over files on disk. Reading a class's source through it would mean
either a spawned process and a temp file, or a second TypeScript — `typescript@5.x` as a runtime
dependency of `packages/pinecall`, which pnpm then links a `tsc` bin for, shadowing the 7 the
repo pins and forcing the package's own `build` and `lint` scripts to name
`../../node_modules/.bin/tsc` by path. Two TypeScripts and a bin-shadowing path for one parse.

`oxc-parser` costs one napi dependency and answers the same questions: it is the parser this
toolchain already transforms with (vite/rolldown and vitest are oxc downstream, `@oxc-project/types`
was already in the lockfile), it is native and synchronous, it takes a string, and it needs
neither a file on disk nor a server. So there is one TypeScript in the workspace again — 7.0.2,
at the root, as a devDependency — and `build`, `lint` and the JSX-view test all say plain `tsc`.

Where the two ASTs differ, and how the behaviour was kept: oxc attaches no JSDoc to nodes, it
returns a flat `comments` array with ranges, so a docstring is *the last `Block` comment starting
with `*` whose end is separated from the node's start by whitespace only* — which is the actual
definition of a JSDoc, and it survives decorators because oxc puts a decorator inside its
member's own span. There is no `getText()` either: a type's source text is
`source.slice(node.start, node.end)`. `null` in a type is a `TSNullKeyword`, not a
literal type wrapping a null keyword. Everything else maps one for one.

What it still cannot do, and this is the honest limit: **it reads one file**. A type imported
from another module is `{ type: "object", description: "<Name>" }` — the name, not the shape —
because resolving it needs a module graph and a filesystem, which a runtime parse of a single
class has no business doing. A type that is genuinely bigger than its file writes
`params: z.object({...})` on the decorator, which overrides everything above.

## Legacy decorators, because that is what is on

`@tool` and `@state` are legacy TS decorators — `(prototype, name, descriptor)` — because the
transform that runs every test, vite 8's oxc, refuses a TC39 standard decorator with a syntax
error before any of our code runs. The spike that measured this, tool by tool with versions,
is the last section of this file. The one flag it needs, `experimentalDecorators`, lives in
exactly one place: `tsconfig.tenant.json`, the preset a tenant extends and never copies. A
legacy decorator runs once per class at definition time, so the registry is keyed by prototype
and a subclass inherits its parent's tools; the migration, when the toolchain moves, is written
down in that same section.

## What the bridge card gets from this seam

`ToolSpec` here is the wire's `ToolSpec` from `protocol/schema/defs.json`, field for field and
in snake_case (`side_effect`, `timeout_s`), so registering an agent is `agent.tools()` sent as
it stands — no mapping layer, and no second name for the same thing. `visibleTools(agent)` is
what goes in a model request. `when` and `preview` are deliberately not on the spec: the
gateway never sees them, because visibility is decided where the state lives.

Two hooks are declared and not wired, on purpose. `provideLast(source)` is where the sdk bridge
hands `last(contact)` a store; until it does, `last()` rejects with a sentence saying so rather
than inventing a memory. `log(name, data)` records into the agent and notifies `onLog`
listeners; the bridge forwards those entries to the call log. Neither shape has to change for
that to happen.

## What convo (~/prueba-abai) taught, and where we went the other way

convo solved this problem once in Python, and reading it first is why the vocabulary here is not
invented. Nothing was copied — every line above is new TypeScript against the v2 design — but
four ideas came straight from it.

**The tool contract is the platform's, not the model's.** `convo/domain/tools.py` declares
`side_effect`, `confirm`, `pii`, `timeout_s`, and `convo/tools/guard.py` vetoes a call before an
adapter ever runs: a wrong timeout, a missing confirmation token, a spent one, one minted for
another call. Our `ToolOptions` is the same five words for the same reasons, and `specFor()`
refuses the same declarations at declaration time rather than at call time.

**Consent is a one-shot token with an audience.** `convo/tools/confirm.py` hashes tool + args
into the token's audience so a yes to one booking cannot pay for another. That is the shape the
confirm-gate card inherits; this seam only marks which tools need it (`confirm` present ⇒
`side_effect: "irreversible"`), because the gate belongs on the server, never in the tenant's
process.

**PII is masked by declaration.** `guard.mask` masks the argument names the spec named and
scrubs their values wherever else they appear. We keep the declaration (`pii: ["name","phone"]`,
validated against the real parameter names) and leave the masking to the log, which is where the
values pass.

**The log is the interface.** convo's `Event(seq, kind, t_ms, payload)` is append-only and
numbered; our `Change` and `LogEntry` carry the same `seq` discipline, and `state.changed` on the
wire is that line with an author on it.

Three places we deliberately went the other way.

**No stages.** convo's unit is a `Stage` with its own prompt file and a `hand_off` to the next
one; the milestone bans that outright. A tool's visibility is `when(state)` — one predicate over
a snapshot — so there is no graph to keep in sync with the conversation, and `visibleTools()` is
the whole of what convo spread across stage classes and handoffs.

**State is fields on the class, not a context object.** convo carries per-session state on
`TenantContext` and records events at the call sites that change it, which means a write and its
log line are two things a developer has to remember to keep together. Here the Proxy makes them
one thing, and an unauthored write throws instead of going unrecorded.

**The docstring is the prompt.** convo renders `prompts/<stage>.md` files through
`prompting/render.py`; v2 puts the sentence where the method is, because a tool's description and
its code drift apart the moment they live in different files. The three prompt regions convo's
`layout.py` fixes — knowledge, view, protocols — survive as this milestone's static · history ·
dynamic, and belong to the views card, not this one.

## Decorators: the spike that settled it, and what it will take to move

The paragraph above ("Legacy decorators, because that is what is on") was a claim on file. On
2026-09-06 it was measured, because `experimentalDecorators: true` is a 2015 flag in a framework
published in 2026 and nobody should inherit it on somebody's word. A throwaway class — one method
decorator using `(method, context: ClassMethodDecoratorContext)` with `context.addInitializer`,
two field decorators using `ClassFieldDecoratorContext`, `experimentalDecorators: false` — went
through all four tools this repo actually runs.

| tool | version | TC39 standard decorators |
| --- | --- | --- |
| `tsc`, type-check and emit to ES2022 | typescript 7.0.2 | accepts; emits `__esDecorate` helpers; the emitted file runs on node 24 |
| esbuild, `--target=es2022 --format=esm` | esbuild 0.28.2 | accepts; transforms; the output runs |
| tsx, the loader `pinecall run` uses | tsx 4.23.13 | accepts; runs the class straight from source |
| vitest, as this repo configures it | vitest 5.0.0 on vite 8.2.2 / rolldown 1.2.7 (oxc) | **refuses**: `SyntaxError: Invalid or unexpected token` |

Three of four is not a majority, it is a wall. vite 8 no longer transforms TypeScript with
esbuild: it transforms with oxc, through rolldown, and oxc's `decorator` option implements the
legacy proposal only. The failure was reproduced under four configurations — no `oxc` block at
all, `decorator: { legacy: false }`, that plus `emitDecoratorMetadata: false`, and
`target: "esnext"` — and every one of them left the `@` in the output for node to choke on. The
same harness with a decorator-free module passes, so the syntax is the variable and nothing else.
That transform is not ours to bypass: it is the one that runs the framework's 103 tests, the
example's 22, and the tenant's own `pinecall test`.

So legacy stays, and the flag stops being the tenant's problem instead: it moved into
`packages/pinecall/tsconfig.tenant.json`, the preset a tenant extends. The example's tsconfig now
reads `"extends": "@pinecall/pinecall/tsconfig.tenant.json"` and declares no compiler flag of
ours; the package's own tsconfig extends the same preset, so `experimentalDecorators` is written
in exactly one file in this repo and a `grep` proves it.

`context.metadata` is worth knowing about before anybody plans the move: it is the natural home
for a per-class registry, and it is not available. tsc's ES2022 emit only creates a metadata
object when `Symbol.metadata` exists at runtime, and node 24 does not define it, so the field
arrives as `undefined` and the decorator throws. `addInitializer` is the alternative, and it
registers per instance against the instance's own prototype — never the declaring class's — which
is the one semantic difference the migration has to absorb.

The day oxc implements the standard proposal, this is one card:

- `agent/decorators.ts` — `@tool` takes `(method, context: ClassMethodDecoratorContext<This>)`,
  returns the `withAuthorAsync` wrapper as the replacement method, and calls `register` from
  inside `context.addInitializer`.
- `agent/tools.ts` — the registry key stops being the prototype the decorator was handed and
  becomes `Object.getPrototypeOf(instance)`, which flattens the chain: `toolsOf` no longer walks
  it, because a subclass's initializers already registered the parent's tools too. `owner` is then
  that prototype's constructor for every tool, so any caller that used it to tell an inherited
  tool from an own one needs a different question.
- `agent/visibility.ts` — the same move for `@state`, plus its one real consequence:
  `visibilityOf(ctor)` is answered today from declarations made at class-definition time, and
  per-instance registration means it must be asked of an instance, or the `static visibility`
  map becomes the only class-time answer.
- `packages/pinecall/tsconfig.tenant.json` — `experimentalDecorators` comes out, and with it the
  last flag of ours a tenant inherits; `vitest.config.ts` here and in the example drop
  `decorator: { legacy: true }`.

Nothing outside those four files moves, and no tenant file moves at all — which is the point of
the preset.
