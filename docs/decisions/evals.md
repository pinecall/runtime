# Evals — the four rings, and the chapter each one lives in

Why `pinecall eval` is one HTTP call, what livekit's own testing framework already carries,
who grades what it does not, and the one rule all four rings ask. This file grew to three
chapters in three cards; it is now the index, and every chapter below was moved out of it whole.

## The chapters

| ring | the question it answers | the chapter |
|---|---|---|
| 1 — a turn does what the class says | one utterance in, one reply out | [evals-rings-1-and-2.md](evals-rings-1-and-2.md) |
| 2 — a tool is called with the right arguments | which tool, which arguments | [evals-rings-1-and-2.md](evals-rings-1-and-2.md) |
| 3 — one real call, re-checked | consent, register, errors, latency | [evals-ring-3.md](evals-ring-3.md) |
| 4 — every finished call, scored | did this call do its job | [scoring.md](scoring.md) |
| the judges, the matrix, promotion, drift | many goldens over two models, over time | [evals-judges.md](evals-judges.md) |
| the rule every ring asks | an irreversible tool ran only after a yes | [evals-consent.md](evals-consent.md) |

## The vocabulary, so a chapter can use it without redefining it

- **A ring** is a question asked of a different thing: a turn, a tool call, a finished call, a
  fleet of finished calls. The rings do not layer, they widen.
- **A check answers four words**, not two: `passed` · `failed` · `deferred` · `skipped`. The last
  two exist so that a check which could not judge anything never reads as a check that held —
  `deferred` is the runtime not doing it yet, on purpose, with a date; `skipped` is nothing in this
  call to judge. [evals-ring-3.md](evals-ring-3.md) is where they were decided.
- **A hard policy is never asked of a model.** It is a `PolicyJudge` — livekit's own
  `evals.Judge`, overridden the way its base class tells you to: the shape of a judged check, the
  answer of a function. The eight ways an LLM judge fails a rule that has a right answer are listed
  in [evals-judges.md](evals-judges.md).
- **The judgement runs in the runtime; the CLI builds the case.** That split decides which of the
  two CLIs owns a verb, and it is argued in [evals-ring-3.md](evals-ring-3.md).
