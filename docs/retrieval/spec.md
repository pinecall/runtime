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

Neither runs on a turn that **could not be a query**: one with no letter in it at all, which is a
number being read out — a phone, an order, a card. No prose index answers one, the contact's facts
would come back ranked by nothing, and a caller's digits are the last thing to send to an embedder.

**The score cannot make that decision, and this is why.** A chunk's score is the fused rank read
`relative_to_the_best` of THAT query (`knowledge/store.py`), so the top chunk is 1.0 whatever was
asked: the sums a reciprocal-rank fusion produces are not comparable between two queries, which is
the very reason they are normalised. `min_score` therefore cuts the tail of an answer and can
never say "nothing here answers this" — a cleaning company's base returned its data-center page,
at 1.0, for a caller reading out a phone number (2026-09-20). A relative score orders; it does not
judge. So the judgment is made before the search, on the words.

That is the whole of the decision, deliberately: choosing when to retrieve has a literature of
trained classifiers and fine-tuned reflection tokens (Adaptive-RAG, Self-RAG, FLARE), and none of
it belongs in the path a caller is waiting on. An agent that wants the model to decide turn by
turn attaches its base with `--mode tool` instead. Saying it with a NUMBER instead would mean an
absolute signal — a raw cosine, or a reranker that calibrates one — which is a different design
and is not what is built.

Both are declared tools. The platform runs them on the app's behalf when the base is attached with
`mode` `retrieved` — one field of the ATTACHMENT in the org's settings (`pinecall docs attach
<base> --mode retrieved`, the default), never a class declaration — when `docs.mode` is `retrieved`
(the default); with `mode: "tool"` the model calls `search` itself. Either way the answer reaches
the model as a `tool_result`, and either way the call's log gets the same entry.

## What the log already carries

Four entry types, none of them added for this page:

| entry | fields |
|---|---|
| `docs.sources` | `query`, `sources[{id, base, path, heading, score, excerpt}]`, `took_ms`, `speech_id` |
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

## What a base is made of

A `.md` is cut at its headings (one to three hashes), each cut kept under 350 tokens, and each
chunk carries its heading path in the text both indexes read — `tarifas.md › Tarifas › Revisión`.
A file's **front matter is not a chunk**: the fenced block of metadata every scraper and every
static-site generator opens a file with (`source:`, `title:`, `scraped_at:`) is dropped before the
cut. Left in it was the file's first section — embedded, indexed, retrievable — which on a scraped
site was 75 of 537 chunks, one in seven, and one of them came back as the evidence of a turn
(2026-09-20). A push rebuilds the base whole, so a base pushed before that carries them until it
is pushed again.

What a base holds is still the tenant's: a page of nav and footer scraped as a document competes
for the handful of slots a turn has, and it answers nothing. Retrieval quality is decided at
extraction more than at embedding, and nothing here can tell a nav bar from a paragraph.

## Several bases, one search

An agent reads every base the world attached to it (`bases` in its settings, one entry per
`pinecall docs attach`), and a turn searches **all of them in one pass** — one query embedded
once, both branches reading the union, one fusion over everything that came back, and then each
chunk against the floor of its OWN attachment.

That is not an optimisation, it is the only ranking that means anything. A fused score is read
`relative_to_the_best` of its own query, so a base searched **alone** always answers 1.0 for its
own best chunk, whatever it is about. Searched one at a time and merged afterwards — which is
what this did until 2026-09-20 — three attached collections took three of a turn's four slots
before the ranking had said a word about any of them: the vending machine's manual and the
clinic's tariffs arrived as equals. Read together they are ranked against each other, and a
collection with nothing to say about the question takes no slot at all.

Two consequences worth stating:

- **The candidate pool grows with the bases asked** (`CANDIDATES_PER_BRANCH × len(bases)`), so a
  small collection is not crowded out of the fusion by a big one before either is read.
- **`k` is the turn's, and `min_score` is the attachment's.** How many chunks reach the model is
  one number for the turn (the most generous `k` of the attachments); the floor is read against
  the base each chunk came from, because a threshold was set on that collection and says nothing
  about the others.

And the log says which collection answered: `docs.sources` carries `base` per source, so the rate
of `held` below can be read per collection and a base that never wins a slot is visible.

## Quality: what it says is in the documents

The three numbers above say retrieval was fast and cheap. They say nothing about whether it found
the right passage. That is judged in two places.

### In production, on every call, already

Ring 4 judges every finished call at hang-up — unless its org declined judging, and then
`POST /v1/evals/judge/{call}` judges one on request — and the panel's grounding judge checks that
what the agent stated appears in the evidence it was given. Since retrieval now arrives as a `tool_result`,
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

**The golden's shape**, one file per AGENT at `test/<agent>/goldens/docs.json` — the same folder
its spoken goldens live in, not beside the documents:

```json
[
  { "asks": "¿cuánto tengo que pagar de copago en la consulta?", "expects": "seguros-y-autorizaciones.md › Seguros, autorizaciones y facturación › Copagos" },
  { "asks": "¿hay que ir en ayunas a la analítica?", "expects": "preparacion-de-pruebas.md › Analítica general" },
  { "asks": "¿me cubre Asisa la primera consulta?", "expects": "seguros-y-autorizaciones.md › Qué necesita autorización previa" }
]
```

`expects` is the heading path a chunk carries, which is what a person can write by reading their own
documents. Fifty to a hundred questions per base is the size that stops being noise.

A golden is fixed and the index is the variable. **A question is never softened so a change can
pass** — the same rule the conversation goldens are held to.

## Quality: the right facts about this caller

Everything above is about `search`. `recall` makes the same promise over the other table — the
facts this caller taught earlier calls, the best six of them in front of the model — and it is
just as invisible to a ring, for the same reason. A ring watches a conversation, so it only ever
sees the facts memory handed over; ring 4's grounding judge weighs what the agent said against
those facts, and the better fact that was never handed over is invisible to it.

So memory has a golden of its own, deterministic in the same way and with the same two figures.
What differs is the question. Nobody can name "the fact that should have won" for a contact the
way they can name a heading in a file they wrote, because a contact's facts are whatever their
earlier calls taught. So a memory question **brings its own facts**:

```json
[
  { "holds": ["Prefiere mañanas", "Paciente de la doctora Vidal desde 2024", "Alérgica a la penicilina"],
    "asks": "¿le va bien el martes?",
    "expects": ["Prefiere mañanas"] }
]
```

`holds` is what memory holds about this question's contact, `asks` is the caller's words, and
`expects` is the fact or facts that should come back. No contact has to exist anywhere:
`POST /v1/contacts/memory/eval` writes each question's facts to a scratch contact of the org, asks,
and deletes them before the next question. Writing them is the point — it is what makes the figures
the ranking a call would get, the same two index scans, the same reciprocal-rank fusion, the same
embedder, rather than an arithmetic of ours over a list. Every fact of one question is written at
the same moment, so the recency weighing treats them alike and what is measured is the words and
the meaning; a golden that wanted to measure recency would have to carry dates, and none does.

**A fact answers when what came back CONTAINS what was expected**, both folded: accents dropped,
case folded, runs of whitespace collapsed. A fact is a sentence a model wrote, and a golden is
written by a person who knows the substance and not the wording — "prefiere mañanas" is answered by
*"Prefiere mañanas, nunca después de comer"*, and is not answered by *"Alérgica"*, which says less
than was asked for. Equality in either direction would make every golden brittle, and the fold is
the one the spanish text configuration behind the words branch already applies.

The two figures are the two above, generalised once: a memory question may expect several facts, so
`recall@k` is the share of the facts asked for that came back and `nDCG@10` is normalised by the
best places those facts could have taken. A question that expects one fact reduces to exactly the
base's arithmetic — it *is* the base's arithmetic, `types/golden_scores.py`, shared by both goldens.

**Write questions whose contact holds more facts than a turn asks for.** A turn recalls six. A
question whose contact holds four is answered whole by any ranking at all, and its `recall@6` is
`1.00` however badly those four were ordered — the question discriminates nothing. `nDCG@10` still
does, and a smaller `k` is what makes recall bite:

```bash
pinecall memory eval                 # test/<agent>/goldens/memory.json
pinecall memory eval --k 1           # the best fact alone: is the right one first?
```

A golden is fixed and the ranking is the variable, here too. What you change when a question fails
is the vocabulary a fact is written in, the embedder, `k`, or the weighing — never the question.

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
