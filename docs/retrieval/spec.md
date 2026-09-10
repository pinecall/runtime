# Retrieval — what it promises, what it costs, and how both are measured

An agent that answers from documents makes two promises. That what it says is in the documents, and
that saying it does not make the caller wait. Neither is a matter of opinion: both are numbers, both
are already in the call's own log, and this page says which numbers and where they come from.

**This is a public contract.** A tenant can compute every figure below from entries their own log
already carries, with no instrumentation of ours to trust and none of theirs to add.

For where a retrieved passage lands in a request and why, read
[`../security/prompt-injection.md`](../security/prompt-injection.md). This page is about measuring
it.

## The two lookups

| tool | what it answers | when it runs | what it costs |
|---|---|---|---|
| `search` | passages of the tenant's knowledge base, by the words of the question | while the caller is still speaking, four words in | two index scans and one embedding |
| `recall` | facts held about this contact from earlier calls | the same moment | two index scans and one embedding |

Both are declared tools. The platform runs them on the app's behalf when `docs.mode` is `retrieved`
(the default); with `mode: "tool"` the model calls `search` itself. Either way the answer reaches
the model as a `tool_result`, and either way the call's log gets the same entry.

## What the log already carries

Four entry types, none of them added for this page:

| entry | fields |
|---|---|
| `docs.sources` | `query`, `sources[{id, path, heading, score, excerpt}]`, `took_ms`, `speech_id` |
| `memory.ops` | `ops[{op, contact, query, facts[{id, text, category, score, source}], took_ms}]`, `speech_id` |
| `metrics.eou` | `end_of_utterance_delay`, `transcription_delay`, `on_user_turn_completed_delay` |
| `metrics.llm` | `ttft`, `duration`, `prompt_tokens`, `prompt_cached_tokens`, `cache_creation_tokens`, `completion_tokens` |

`speech_id` joins a lookup to the turn it served, which is what makes every figure below a
per-turn figure rather than an average over a call.

## The four numbers

### 1. The silence retrieval costs — `metrics.eou.on_user_turn_completed_delay`

livekit's own definition of that field is *"time taken to invoke the user's
`Agent.on_user_turn_completed` callback"*, and that callback is where the session collects its
lookups. So livekit measures, per turn, exactly what retrieval cost the person on the line. Nothing
of ours needs to be trusted for this number.

**Target: p95 under 50 ms.** A lookup starts while the caller is still speaking, so by the time
they stop the answer is usually already there and the callback returns having waited for nothing.

Measured on a live call the day the eager path landed: five turns of six waited **0 ms**, the sixth
waited 251 ms — its budget. With the lookups run at the end of the turn instead, the same six turns
waited 125 · 138 · 166 · 166 · 198 · 251 ms and one lost both of them.

`end_of_utterance_delay` and `transcription_delay` are the other half of the same picture: together
they are the window a lookup had to run in before anybody was waiting on it.

### 2. Turns that went without — `error` entries with `search_skipped` or `recall_skipped`

A lookup that is not back when its budget expires is cancelled, and the model answers that turn
without it. The entry says which tool and why.

**Target: under 1% of turns.** Anything above that is a budget, an embedder or a database that
cannot keep the promise, and the fix is one of those three and never a larger budget: a budget
large enough to always be met is a budget the caller pays for in silence.

Read it as `count(error where code in (search_skipped, recall_skipped)) / count(turn.user)`.

### 3. What the passages cost in tokens — `metrics.llm.prompt_tokens`

A turn that retrieved carries its chunks in the request. The difference against a turn that did not
is what retrieval costs per turn, in the only currency a model bills in.

`prompt_tokens` minus the agent's own baseline (its static blocks, which `prompt.changed` gives the
character count of) is the working figure. `k` and `min_score` are the two knobs that move it, and
this is the number that says whether moving them was worth it.

### 4. Whether the cached prefix is holding — `prompt_cached_tokens / prompt_tokens`

The static blocks — identity, the knowledge file, the tool list — are meant to be read from the
provider's cache from the second turn on. When that ratio is low the prefix is being rewritten every
turn, and the usual cause is a knowledge file that is not stable or a tool list that moves.

**Target: above 0.8 from the second turn of a call**, with the caveat that Anthropic's minimum
cacheable prefix is 1024 tokens on Sonnet 5 and **4096 on Haiku 4.5**, which is the default model:
an agent whose whole prefix is smaller than that caches nothing at all, and the ratio is honestly
zero rather than broken.

## Quality: what it says is in the documents

The three numbers above say retrieval was fast and cheap. They say nothing about whether it found
the right passage. That is judged in two places.

### In production, on every call, already

Ring 4 judges every finished call at hang-up, and the panel's grounding judge checks that what the
agent stated appears in the evidence it was given. Since retrieval now arrives as a `tool_result`,
that evidence *is* the chunks.

**The rate of `held` over calls that carry a `docs.sources` entry is the precision of retrieval,
measured on real traffic, at no extra cost.** It is a `call.score` field, per call, already in the
log.

There are two implementations of this judge in reach and only one should survive: our
`GroundedJudge`, which matches prices, hours, dates and names by code first and only asks a model
about what did not match; and livekit's own `accuracy_judge`, whose instructions are *"responses
must be supported by function call outputs… fail if the agent states facts not supported by the
function call outputs"*, and which asks a model every time. Ours is cheaper and more deterministic,
which is this codebase's own preference, but the choice is owed a measurement over the same calls
rather than a preference. Until that measurement exists, ours runs.

### Offline, against a golden, when you change something

The production judge tells you the answer was grounded. It cannot tell you the index missed a better
passage, because the model never saw the one it missed. That needs a golden: a set of questions with
the chunk that should have won, and two figures computed by code with no model in the loop.

**`recall@k`** — the share of questions whose expected chunk is among the `k` returned. This is the
one that matters: a chunk the model never sees cannot be used, whatever its rank.

**`nDCG@10`** — how high the expected chunk ranked, discounted logarithmically. Two indexes that
both find the passage are not equal if one puts it first and the other seventh, because `k` cuts.

Neither needs a model, so both are deterministic, free, and safe to run on every change. They are
what makes "we changed the embedder" a statement with a number after it.

**The golden's shape**, one file per base, beside the documents it asks about:

```json
[
  { "asks": "¿cuánto cuesta una revisión?",        "expects": "tarifas.md › Tarifas › Revisión" },
  { "asks": "¿hay que ir en ayunas a la analítica?", "expects": "preparacion-de-pruebas.md › Analítica general" },
  { "asks": "¿me cubre Asisa la primera consulta?", "expects": "seguros-y-autorizaciones.md › Qué necesita autorización previa" }
]
```

`expects` is the heading path a chunk carries, which is what a person can write by reading their own
documents. Fifty to a hundred questions per base is the size that stops being noise.

A golden is fixed and the index is the variable. **A question is never softened so a change can
pass** — the same rule the conversation goldens are held to.

## The mapping to OpenTelemetry GenAI

The runtime exports no traces today; the milestone that adds it should invent no vocabulary,
because the standard already names all of this and our entries already carry the fields. Written
down here so the entries do not drift away from it in the meantime.

| our entry | the span | the attributes |
|---|---|---|
| `docs.sources` | `gen_ai.operation.name = "retrieval"` | `gen_ai.retrieval.query` ← `query`; `gen_ai.retrieval.documents` ← `sources`, each `{id, score, content ← excerpt, source ← path, metadata ← {heading}}` |
| `memory.ops` op `recall` | `search_memory` | the same document shape over `facts` |
| `memory.ops` op `remember` | `create_memory` · `update_memory` | one span per op the extraction answered |
| `memory.ops` op `forget` | `delete_memory` | — |
| the embedder | `gen_ai.operation.name = "embeddings"` | `gen_ai.request.model` ← the embedder's model; `gen_ai.embeddings.dimension.count` = 1024; `gen_ai.request.encoding_formats` ← what the vendor took |
| `metrics.llm` | `chat` | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |

One thing the standard would want that we do not carry: a document's `metadata` is free-form there
and ours is two named fields. Nothing to change; the mapping is lossless in the direction that
matters.

## What is deferred, and named

- **A monitor over `memory.ops`.** The entries are there; nothing reads them looking for a fact that
  reads like an instruction. The security page names this as the fourth of four defences and the one
  not built.
- **BM25 first as a gate.** The design's own note — a short or conversational turn with a low lexical
  score need not be embedded at all. Today every turn past four words embeds. Measurable with
  number 3 above once the golden exists to say what it costs in recall.
- **`docs.mode = "tool"` unexercised.** The path is written and no example uses it.
