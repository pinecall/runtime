# The judges — livekit's `Judge`, and the four policies that answer for free

One chapter of [evals.md](evals.md), which indexes the rest. It replaces `evals-graphs.md`,
which said the same things about DeepEval's `DeepAcyclicGraph`. **DeepEval left this tree on
2026-09-08**, and the paragraph that follows is the whole reason.

## Why the library changed

Two judge abstractions in one repo is the consent-rule-written-twice disease one layer up. The
LiveKit doc audit found that `livekit.agents.evals` — already installed, already a dependency of
the runtime — carries the whole shape: `Judge`, `JudgeGroup`, `Evaluator`, `JudgmentResult` and a
three-word `Verdict` (`pass` · `fail` · `maybe`). Its own base class says outright what a hard
policy is:

> "Subclass and override `evaluate` to implement deterministic or programmatic checks that don't
> need an LLM" — `livekit/agents/evals/judge.py:166-184`

That is `DeterministicNode`, in the library, without a second graph engine, a second model
interface and sixty transitive packages behind it. So `nodes.py` is gone, `matrix.py` is ours
again, and the four policies are `Judge` subclasses. The judge machinery is theirs; the rules,
the goldens and the log the verdicts live in are ours.

## Why a hard rule is still never asked of a model

The lesson convo left, written out because every judge here is built on it. Eight ways an LLM
judge fails a rule that has a right answer:

1. **Order.** A judge reads a transcript as a bag of sentences; "the tool ran before the yes" is
   nothing but an order, and the log already numbers it.
2. **Counting.** Two bookings and one grant reads as "the caller confirmed".
3. **Non-determinism.** Same evidence, two answers. A red build nobody can reproduce gets ignored.
4. **The agent's own words.** "Queda confirmado" persuades the judge that it was — the very failure.
5. **Fluency read as correctness.** A warm reply scores well on the rules it broke.
6. **Knowledge the judge brought itself.** A model that believes the price says yes without looking.
7. **Price.** One prompt per golden per model per metric, for answers the log already held.
8. **No citation.** "It seems the agent did not confirm" is not a finding. `seq 4` is.

## The shape, file by file

`policy.py` is the whole of it: `PolicyJudge` extends livekit's `Judge`, keeps their `evaluate`
signature whole so it is still an `Evaluator` (`evals/evaluation.py:17-31`) and can ride in a
`JudgeGroup` beside their eight, and hands the one thing a subclass writes to `decide(chat_ctx)`.
`held()` and `broken()` are the two `JudgmentResult`s a binary policy ever returns. After deciding,
`evaluate` hangs the criteria on the result exactly as their LLM judge does after it has judged
(`evals/judge.py:250`), so a reader of a mixed report finds the question in the same field whoever
answered it.

`judges.py` now has ONE judge factory. There were two only because there were two libraries:
`a_judge()` for livekit's `RunResult.judge` and `a_graph_judge()` for a DeepEval metric. It also
holds `Counted`, which wraps the judge model and tallies the questions actually put to it.

**`judge_calls`, and no invented cost.** livekit's `JudgmentResult` carries a verdict, a reasoning
and the instructions — and nothing else (`evals/judge.py:34-57`). So this package counts questions
rather than pricing them: a judge's tokens are an LLM row like any other, and this chapter's rule
is that a report never computes a metric the log does not already carry. Zero is the happy path,
and `tests/test_consent.py` and `tests/test_matrix.py` assert exactly that.

## `bridge.py` and `case.py` — the log in, one `Case` out

`Case` is ours now, because the shape it replaced was a vendor's. It carries the log's own rows:
`Said` per turn (role, text, **seq**, speech id, the metrics block, the typed blocks, what
retrieval put in front of the model, and its `Called`s), the gate's trace, the facts that arrived,
each tool's declared contract, the knowledge, and `call.summary` verbatim.

`Case.chat_ctx` is a cached view of those turns as livekit's `ChatContext`, because a judge is
handed a ChatContext and nothing else. One truth, one view. A tool call reaches a judge twice, and
they are not the same fact:

- in the ChatContext, as a `FunctionCall` and its `FunctionCallOutput` — which is what `grounded`
  reads as evidence and what any of livekit's own judges expects to find.
- on `Case.gate`, as an ordered trace of every `tool.call` and every `confirm.*` in **seq** order,
  carrying the side effect the app declared. Consent is a question about an order, so it is given a
  list and not a set.

`Called.answer` is the text `pinecall.log.as_text` put in front of the model — the same function
`session/voice/tools.py` puts on the wire. A judge that read the structured output instead would be
judging a call that never happened.

Two things changed while rewriting it, and both are corrections:

- A tool call and a retrieved chunk hang on the **agent's** turn alone. One speech id covers the
  caller's turn and the reply to it; hanging the calls on both counted every tool answer twice in
  the evidence. The metrics blocks are the speech's own and still reach both turns.
- There is no `latency_ms` any more. DeepEval's field was named in milliseconds and livekit measures
  in seconds, so the bridge converted. `Said.metrics` now carries `e2e_latency` under livekit's own
  name, in livekit's own unit — nothing renamed, nothing converted, nothing summarised.

## Two judges are given the trace, not the transcript

`consent` and `replies` are questions about **where in the log** something landed, and livekit's
items carry a `created_at` and never the log's own numbering. So those two take the call's rows at
construction and do not read the transcript at all. Everything else — `register`, `leakage`,
`grounded`, `tools`, `says`, `silence` — reads the ChatContext, which is what lets the same judge
grade a live session at hang-up and a replayed golden with one class.

**A policy that costs nothing judges every run** (2026-09-08). Ring 1 used to pick its
judges from the golden's `expect` alone, so a golden that asked nothing was scored by nothing while
rings 3 and 4 ran consent over every call. `api/evals/scoring.py::_judges_for` now puts
`ConsentJudge` at the head of every panel: `consent` is a column of every matrix, and a golden with
`expect: {}` is judged. Only a policy that asks nobody *and* needs no declaration from the golden
may join it that way — which today is consent alone. What the experiment behind it found, and why
`no-reserva-antes-del-si.json` kept its phrases, is the agents repo's `docs/decisions/pinecall-test.md`.

The rule itself is still `pinecall/types/consent.py`, which ring 3's door and ring 4 read too: one
order, one set of sentences, one place a policy change lands. Why it moved out of this package is
[evals-consent.md](evals-consent.md). The one line to carry away: the confirmation gate was removed
from the runtime on 2026-09-06 (`docs/decisions/confirm.md`), so no log written by a live call today
carries a `confirm.granted` at all. The rule answers `ungated` for that and this judge holds it,
with the deferral sentence and its date as the reason. **Never read a green consent cell as evidence
that anybody consented: read the reason in the cell, and which log it ran on.**

## `grounded.py` — code first, and one question if code could not

`GroundedJudge(extractors, evidence)` is built per call, because the evidence is the call's. An
`Extractor` is a name, a pattern and a `Scope`: `TEXT` for a fact that should be in what was written
down (a price), `CALL` for one from a tool's answer (an hour, a day, who they will see). The scopes
are the tenant's to move; the shapes are the framework's. Code matches every stated fact against the
half of the evidence its scope names, whitespace flattened and case folded. All matched → `pass`, no
prompt, which is the criterion `tests/test_grounded.py` counts.

### The evidence has three halves, and the third one is the state

Added 2026-09-08. `Evidence` was `text` (the knowledge and the retrieved chunks) and
`calls` (what the tools answered), and a golden that opens in a state scored zero on every fact it
was seeded with: *no call evidence carries the date 'jueves'* about a call where the agent read the
date straight off its own view. The state is evidence — the app put those facts in front of the
model — so `Evidence` carries a third field, `state`, and `bridge.py` fills it from the
`state.changed` entries the way it already fills `events` from `event.received`.

Three things about the shape, because each of them was the alternative that was not taken:

- **`Scope.CALL`, not `TEXT`.** A state is written by the business's own systems — a tool moved it,
  or the app seeded it — never by the knowledge base. So `_within(CALL)` returns `calls + state`,
  and the near-miss sentence grew a word: *though a tool answer or the state does*.
- **The whole state, never a diff.** Every `state.changed` carries the state entire
  (`protocol/events.py:268`), so the bridge stores what the entry stored and reconstructs nothing.
  Identical readings are dropped and the order kept, which leaves one block per state the call was
  actually in rather than one per field somebody assigned.
- **The rendered view is not stored, and does not need to be.** The card asked for "the seeded state
  and the rendered dynamic region"; only the first is reachable, because `prompt.changed` carries a
  region, a hash and a char count and never the text. But the view is `render(state)` and nothing
  else goes into it, so the state is that region's whole source — storing the rendered text beside
  it would be the same facts twice under a second name.

`tests/test_grounded.py` pins all of it, including that a date the state does *not* carry is still a
finding and that the seeded facts reach the one question a judge is ever asked here.

### A tool answer is quoted with its name and its arguments

Added 2026-09-09. `Evidence.calls` held the answer alone, so an agenda that answered
`[]` reached the judge as `[]` — no tool, no day, and a model reads that as "no information about
anything". In ring 4 under gpt-5.4-mini it failed `no-inventa-horas-de-un-dia-sin-agenda`, where the
agent called `freeSlots("domingo por la mañana")`, read `[]` and correctly said there were no slots
(call_1575735cba5341e6ad8b7b09075257c0, one judge call, 0.001474 EUR): a false red of the
*rendering*, not of the rule. `rendered(call)` now writes each one as
`freeSlots({"day": "domingo por la mañana"}) → []` — the name, the arguments as one JSON line, the
answer verbatim as the model read it — and `Called` already carried all three off the log, so
nothing new is read. Rendering only adds characters to the string `carries` matches against, so
every call that was grounded by code stays grounded and asks nobody; a day that appears only in the
arguments now grounds itself, which is the case from the field going green without a model at all.

Only a left-over fact reaches a model, and it is the one override of `evaluate` in this package —
livekit's own two-step, where a check that can settle itself settles itself and the LLM is reached
only when it cannot (`evals/judge.py:327-367`). `45 euros` against `45 €` is the case it exists for.

`asking.py` is the one question this framework ever puts to a model: a forced call to
`submit_verdict`, temperature 0, the tool schema doing the validating — the shape of
`evals/judge.py:116-163`. It is written here rather than reused because livekit's `_LLMJudge` is
private **and** its instructions are fixed at construction, while ours carry the evidence of the
call being judged, which changes every time. A verdict that is not one of their three is read as
`maybe`, which scores a half and never a pass.

## `register.py` is not the gateway's banned-word scan

Ring 3's `register.py` scans the words a business declared it will not have its agent say. This one
asks a different question: did the agent address the caller as **tú** or as **usted**, and keep to
the one the business asked for. Both halves are closed word lists, and the usted one is deliberately
shorter: `le`, `les`, `su` and `sus` are third person as often as polite ("su cita con la doctora"),
so counting them would report a register the agent never chose. Whole words: banning tuteo has not
banned `tutor`.

## `leakage.py`, `matrix.py`, `report.py`

Leakage scans what the agent said and what its tools answered, and names which of the two: a foreign
row in a tool output is the app handing over the wrong record, one in a turn is a caller hearing it
— two processes to fix. A scan with nothing declared reports **broken**, not held: a check that
could not look must never read as proof, and the same rule makes `consent` refuse a case built
without the declaration.

`matrix.py` measures and never runs a turn: `Spoken(model, golden, case)` in, `Matrix` out. A cell's
metric is the judge's **own `name`** (`evals/evaluation.py:20-23`) and never a key the caller chose,
so two judges can never share a column by accident. The score is livekit's own arithmetic over a
verdict — pass 1.0, maybe 0.5, fail 0.0 (`evals/evaluation.py:41-52`) — read off an
`EvaluationResult` rather than spelled again here. `report.py` draws it as a single self-contained
HTML page: no stylesheet to fetch and no script to run, because a report is opened from a terminal
or mailed to somebody and either may have no network. One reason per cell now, where there were two
(`reason` and DeepEval's verbose log) — a judgment always carries its reasoning, whoever answered.

## The seam

`tests/test_isolation.py` holds two rules. The first is the debt this package still owes:
`answers.py`, `clinica.py` and `headless.py` reach into `pinecall.gateway` for two pure conversions
(`api/agents/declaration.py` and `session/declaring.py`). No module added since does, and the test
fails if a fourth appears — because the gateway imports this distribution under its `evals` extra,
and a judge module reaching back would be a cycle at import time. The second is new and one line:
no module of this distribution imports `deepeval`. `tests/test_isolation.py` says the other
half — `pinecall.evals` never travels back into ring 0.
