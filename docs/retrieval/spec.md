# Retrieval — what a call looks up, when, and how it is measured

An agent that answers from documents makes two promises: that what it says is in the documents,
and that saying it does not make the caller wait. Both are numbers, and both are in the call's own
log. This page says what a lookup is, when it runs, what the log keeps of it, and which figures a
tenant can compute from entries they already have. A retrieved passage reaches the model as data
inside a tool result, never as an instruction, and it never carries the operator's authority.

## The two lookups

| tool | answers | from | runs when |
|---|---|---|---|
| `search` | up to `k` chunks of the bases the agent reads | the knowledge bases pushed to the org | the caller has said four words, on a turn that could be a question |
| `recall` | up to six facts held about this contact | the contact's memory, taught by earlier calls | the same moment, when the agent keeps memory and the platform knows who is on the line |

Both are tools of the platform, never of the app. With a base attached as `retrieved` (the
default of `pinecall docs attach`) the platform runs `search` before each turn and splices the
answer into the request as a tool result; with `mode: tool` the model calls it when it wants to.
`recall` runs before each turn whenever the agent declares a memory policy. Either way the model
reads the same shape: `{"chunks": [{path, heading, text}]}` and
`{"facts": [{text, source, since}]}`, with no score in either, because a score means something
to the ranking and nothing to the model.

A lookup starts on the first interim transcript worth a query, so its answer is usually in before
the caller stops talking. What a turn waits for it is a budget: 250 ms on a spoken call, 3 s on a
written one (`PINECALL_VOICE_LOOKUP_BUDGET_MS`, `PINECALL_TEXT_LOOKUP_BUDGET_MS`). A lookup that
is late is cancelled and the turn goes on without it; a lookup that cannot run at all is a
recoverable `error` entry, `search_skipped` or `recall_skipped`, saying why.

Neither runs on a turn with no letter in it: a number being read out finds nothing in a prose
index, and a caller's digits are the last thing to send to an embedder.

## Why a score cannot decide whether to look

A chunk's score is its fused rank read relative to the best of that query, so the top chunk is
1.0 whatever was asked. Reciprocal-rank fusion produces sums that are not comparable between two
queries, which is why they are normalised. `min_score` therefore cuts the tail of an answer and
can never say "nothing here answers this": a relative score orders, it does not judge. The
judgment is made before the search, on the words, and it is deliberately the whole of it.

What the log does carry is `evidence`: the best cosine of the search, read as `strong` at 0.45 and
above, `none` below 0.30, `weak` between. It is data for the reader and for the grounded judge,
measured on a golden of real questions, and it gates nothing.

## What a base is made of

A base is a folder of Markdown pushed whole (`PUT /v1/knowledge/{base}`) or a file at a time
(`PUT /v1/knowledge/{base}/files/{path}`). Each file is cut at its headings, one to three hashes,
each piece kept under 350 tokens and cut again at a paragraph or a sentence when a section runs
long; a fenced code block is one paragraph whatever blank lines it holds, and is never cut, so a
class is never split between two of its members. A piece carries its heading path in the text
both indexes read, `Tarifas › Revisión`. Front matter is not a chunk. A file keeps the SHA-256 of the text it was cut from, so a push embeds only
the files that changed and the rest are kept as they were; a push that changes nothing costs one
statement and no embedding.

Every chunk is found by its vector and by its words: one query embedded once, two index scans
(`halfvec` cosine, BM25 in Spanish text configuration), fused by rank in SQL.

What a turn is handed is sections, not loose pieces, as megabrain handed them in v1: a chunk found
comes back as its section, the chunks of the same heading two either side of it joined in the
file's order, so a section cut at 350 tokens reads whole again. Two hits in one section come back
once. The two best pages give every section found in them; every page after them gives only its
best one, so the answer reads the right page whole and the rest of the base one line each. An org's chunks
count against its `knowledge_chunks` quota, sized before the push, and a plan with the quota at
zero searches nothing and writes no entry.

What belongs in a base is what the agent should look up: tariffs, hours, procedures, the long
tail. What belongs in the agent's own `knowledge` block, the Markdown the class ships, is what it
should always know: who it is, what it may promise, the handful of facts every call needs. The
block is read on every turn and cached by the model's provider; a base is read when the turn
asks for it.

## Several bases, one search

An agent reads every base attached to it, and a turn searches all of them in one pass: one
embedding, both branches over the union, one fusion over everything that came back, then each
chunk against the floor of its own attachment. Searched one at a time and merged afterwards, each
base would answer 1.0 for its own best chunk whatever it was about, and a collection with nothing
to say about the question would take a slot all the same.

`k` is the turn's, the most generous of the attachments (eight by default), and an app that asks
`this.knowledge.search(query, {k})` may say its own; `min_score` is the attachment's. The
candidate pool grows with the bases asked, thirty per branch per base, so a small collection is
not crowded out before it is read. `docs.sources` says which base each source came from.

## Memory

A contact is the app's contact id when it named one, else the number the call came from; a web
visitor nobody named has no memory. At hang-up, between `call.ended` and `call.summary`, the
platform reads the call's turns, asks the agent's own model on the org's own keys what the call
taught about the contact, keeps only what the agent's policy says it remembers and nothing it
says it forgets, refuses a fact that names one of the agent's tools, and writes the rest embedded.
That model call is the whole cost of a hang-up and it has a budget, `PINECALL_REMEMBER_BUDGET_S`
(8 s): past it, or broken, the log carries a recoverable `remember_failed` and the call seals all
the same. An org at its `memory_facts` cap asks no model: the refusal goes on the agent's log as
`credits.exhausted` and the call's says an empty `remember`. A memory at its cap is still read,
since a cap is about keeping.

`recall` ranks the contact's current facts by the same two branches, then by recency and the
confidence the extraction gave them, and hands the model the best six with the call that taught
each and the date it was first held. The contact recalled is the one the platform knows, never one
the model wrote into its arguments.

## What the log carries

| entry | fields |
|---|---|
| `docs.sources` | `query`, `sources[{id, base, path, heading, score, excerpt}]`, `took_ms`, `speech_id` |
| `memory.ops` | `ops[{op, contact, query, facts[{id, text, category, score, source}], took_ms}]`, `speech_id` |
| `error` | `code` in `search_skipped`, `recall_skipped`, `remember_failed`; `recoverable: true` |
| `metrics.eou` | `on_user_turn_completed_delay`: what retrieval cost the person on the line |
| `metrics.llm` | `prompt_tokens`, `prompt_cached_tokens`: what the passages cost in tokens |

`speech_id` joins a lookup to the turn it served, so every figure below is a per-turn figure. No
entry is written when a lookup did not run: an empty `docs.sources` would read to the grounded
judge as a search that found nothing.

## The figures

1. **The silence retrieval costs** is livekit's own `on_user_turn_completed_delay`: the callback
   it times is where the session collects its lookups. Target: p95 under 50 ms, because a lookup
   started on an interim is usually back before anybody waits.
2. **Turns that went without** is `count(error where code in (search_skipped, recall_skipped)) /
   count(turn.user)`. Target: under 1 %. Above it the fix is the budget, the embedder or the
   database, never a larger budget: a budget always met is a budget the caller pays in silence.
3. **What the passages cost** is `prompt_tokens` on a turn that retrieved against one that did
   not; `k` and `min_score` are the knobs.
4. **Whether the cached prefix holds** is `prompt_cached_tokens / prompt_tokens` from the second
   turn on. Target above 0.8, with the caveat that a provider caches nothing under its minimum
   prefix.

## Quality

The figures say retrieval was fast and cheap, not that it found the right passage. That is judged
twice. On every call, the hang-up panel's `grounded` judge checks what the agent stated against
the chunks it was handed, so the rate of `held` over calls carrying `docs.sources` is the
precision of retrieval on real traffic, at no extra cost. Offline, a golden per base
(`POST /v1/knowledge/{base}/eval`: `[{asks, expects}]`, `expects` a heading path a person can
write by reading their own documents) gives `recall@k` and `nDCG@10` by code alone; a golden per
agent's memory (`POST /v1/contacts/memory/eval`: `[{holds, asks, expects}]`) writes each
question's facts to a scratch contact, asks, and deletes them, so what is measured is the ranking
a call would get. A question is never softened so a change can pass. A memory fact answers when
what came back contains what was expected, accents dropped, case folded, spaces squeezed: a golden
knows the substance and not the wording.

## Deferred, and named

- **A reranker.** An absolute signal over the candidates would let a score gate a lookup; it is a
  different design, and it waits for a golden that says what the fusion misses.
- **BM25 first as a gate.** A short turn with a low lexical score need not be embedded at all;
  measurable with figure 3 once the golden exists to say what it costs in recall.
- **A monitor over `memory.ops`**, reading for a fact that reads like an instruction.
