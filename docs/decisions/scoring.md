# Ring 4 — `call.score`: every finished call judged, in the tenant's own log

One chapter of [evals.md](evals.md), which indexes the rest. Rings 1–3 are asked of something a
person chose to ask about: a turn, a tool call, one replayed golden. Ring 4 is asked of every call
that ever happens, once, at hang-up, with nobody watching.

## Why the verdict is an entry and not a session tag

livekit's own shape for this is `on_session_end` plus a `JudgeGroup`, and it is the shape we take:
their `evaluate` runs every judge concurrently, swallows a judge that throws and tags the session
with the result (`evals/evaluation.py:139-186`). The tag is the half we cannot use — it surfaces in
LiveKit Cloud, and this runtime has to work on a customer's own box with no LiveKit Cloud in it.

So the result goes where everything else about a call goes: the call's log, as one entry,
`call.score`. The console reads it, drift reads it over time, promotion reads it per organisation,
and all three read it through the same three doors every other entry is read through. Nothing new
was invented to carry it.

## `call.score` is the terminal entry, and `call.summary` no longer is

The log seals on the terminal entry (`log/logs.py:56-58`), the schema allows exactly one
(`protocol/generate/schema.py:133-137`), and the verdict is written **after** the summary. So the
marker moved: `call.summary` states the cost, `call.score` states the verdict, and the log seals on
the second one.

For the voice door this is not a matter of taste. The worker writes over HTTP, so an entry sent
after a sealed `call.summary` would be refused by the gateway and counted, silently, in
`Writing.refused`. Two readers followed the constant and needed nothing: `sessions tail` stops on
the terminal entry, and the SSE body ends there. One reader did not — `sessions recording` was
reading the path off "the last entry", which is a different fact from "the summary", and it now
names `call.summary` itself.

**A call whose judges could not run still writes the entry**, because the entry is what seals the
log. `pinecall-evals` is the runtime's `evals` dependency GROUP, not a dependency of it
(`runtime/pyproject.toml`), so a box that only answers calls does not have it.

## An absent `passed` is not a `passed: false`

`passed` means **no judge answered broken**, which is the answer to a question somebody asked. A
call nobody asked about has no answer, so the field is **absent** — never `true`, and never `false`
either. A reader that trusts `passed` alone must never be handed a green call that nobody looked
at, and it must never be handed a red one either: absent is a third thing, and it is the honest
one. The reason is in the entry beside it, in `not_judged`: the judges are not installed on this
box, or the judging itself failed and this is the sentence it failed with.

This is the one field on `call.score` whose absence carries meaning, and it is why the whole entry
exists on a box with no judges at all: the log is the truth, and "nobody judged this call, because
X" is a fact about the call. Writing it only to the process's logger would put it in the one file
nobody ships. The console draws this screen, drift reads it over time, and billing runs against this
log; all three read the same two fields.

A judge that raises answers nothing and is **dropped**, the same drop livekit's own group does
(`evals/evaluation.py:155-165`). No verdict is invented for it — a fabricated `maybe` would read as
a judge that looked and was unsure. When every judge is dropped the entry has no `passed` and
`not_judged` says so.

## `panel` is who was RUN; `judges` is who answered

A dropped judge leaves no row, so `judges` alone cannot tell a call that was judged by one judge
from a call that was judged by two and lost one — and `passed`, read off the survivors, would call
the second one green. So the entry carries `panel` beside `judges`: the name of **every judge run
over this call**, in the order `evals/score.py:_the_judges_of` declares them, written on every
path including the one where nothing judged the call at all — there it is `[]`, and `not_judged`
says why. A judge that raised is in `panel` and not in `judges`, and that difference is the whole
point of the field. It is OPTIONAL on the wire only because entries written before it exists carry
none; absent is "an older runtime wrote this", never "nothing was declared", which is `[]`.

## The four words, and the three they come from

livekit's `Verdict` is `pass · fail · maybe` (`evals/judge.py:12`). Ours is four, and this table is
the whole mapping — written once, in `judging/verdicts.py`, and pinned by a test that reads
livekit's own `Literal`:

| livekit | ours | what it means |
|---|---|---|
| `pass` | `held` | the rule held |
| `fail` | `broken` | it did not, and the reason names the evidence |
| `maybe` | `deferred` | the judge was asked and could not settle it |
| — | `skipped` | **ours alone**: nobody asked it |

`skipped` is never something a judge answers. It is this runtime saying a question went unput,
which is a different fact from every answer a judge can give, and the reason on that row says why.
Ring 3's own four statuses (`passed · failed · deferred · skipped`, `api/evals/verdict.py`) are
a different vocabulary for a different reader — a code check, not a judgment — and the two are
deliberately not merged: a verdict on the wire is the tenant's, a check's status is the operator's.

## Where the one function lives, and why it is not in either process

`worker/` never imports `gateway/` (`tests/test_isolation.py`), the voice door is in the worker and
the text door is in the gateway, and `log/` and `types/` hold no framework — which rules out all
four. So the judging is `pinecall/judging/`, a package both processes import and neither owns, and
it reaches `pinecall.evals` through an import **inside** the one function, for the same reason and
in the same shape as `api/evals/scoring.py:20-24`: on a box that only answers calls the
distribution is not installed at all.

`a_score(entries, config, settings)` never raises. It cannot: the log seals on what it returns.

## The judges read the log back, not the session's history

This is the one place we deliberately do not follow livekit's example. `JudgeGroup.evaluate` takes
`report.chat_history`, and a live session has one — but a `ChatContext` item carries a `created_at`
and never the log's own numbering, and **a verdict is read by the seqs it cites**. A reader who
disagrees with `consent: broken` has to be able to open the log at the two lines it is about.

So both doors replay the call's own log into a `Case` and hand the judges `case.chat_ctx`:

- the **text session** holds its `CallLog` and reads it whole (`CallLog.whole()`, over the one
  page-until-short loop in `log/replay.py`);
- the **voice bridge** flushes what it has queued and reads the call back from the gateway over
  HTTP (`Gateway.since`), because the worker never learns a seq — the gateway numbers the log. The
  seam is intact: the two processes met over HTTP, which is the only thing they ever do.

### The seqs come out of the judgment's own sentence

`JudgmentResult` carries a verdict, a reasoning and the instructions, and nothing else
(`evals/judge.py:34-57`). There is no field for evidence and we do not add one — extending their
shape means overriding `evaluate`, not growing their dataclass. So `evidence.seqs` is read out of
the reason with one regex, in one place, because `pinecall/types/consent.py` writes the seqs into
that sentence on purpose and says so where it writes them. `evidence.said` is then the words the
log carries at the first of those entries that carries any — the caller's own yes, for a reader who
should not have to open the log to see it.

The golden booking call is the proof and the fixture at once: `protocol/fixtures/call-log-golden.json`
now ends with the `call.score` ring 4 writes for it, `consent: broken`, seqs `[79, 93]`,
`said: "Sí, confirmo."` — the same two numbers ring 3 prints — and a test asserts the fixture is
what the judges reach rather than what somebody typed.

## The ceiling: what judging one call may spend

`PINECALL_JUDGE_CEILING_EUR` (default `0.002`, which is the 0.2 cents per call this milestone is
accepted against). A judge that answers by code is a `PolicyJudge` and costs nothing; a judge that
may reach a model runs only while this call's judging budget is above what judging it has already
cost — which, before the first question, is nothing. So today the ceiling is a door: above zero
every judge may ask, at zero none may, and a real per-org running budget arrives with the quotas in
ms-11. At zero **no judge model is built at all**, which is why a runtime with no provider key
still scores every call by code.

A judge the budget turns away is not guessed at. It is asked to answer with no model
(`evaluate(llm=None)` — livekit's own two-step, `evals/judge.py:327-367`): what it can settle by
code it settles, and what it cannot comes back `skipped` with the ceiling and its own sentence.
`GroundedJudge` is the only one of the four that extends `Judge` rather than `PolicyJudge`, so at a
ceiling of zero it is exactly the one that skips and the other three still answer — decided by the
class hierarchy, never by a list of names.

**Ring 0 runs at a ceiling of zero**, beside the dead-sentinel provider keys
(`tests/conftest.py`): structural, not disciplinary — a unit test cannot open a socket by
hanging up a call.

## `judge_calls` is a count; `judge_cost_eur` is a price, and may be absent

`judge_calls` comes from `pinecall.evals.Counted`, which wraps the judge model and tallies the
questions that actually reached a provider. It is never a price: a judgment reports no cost at all.

`judge_cost_eur` is the judge model's real token usage, priced in `providers/prices.py` by the same
table every other LLM row in this runtime is priced by. The usage comes from livekit's own
`ModelUsageCollector`, fed by the model's own `metrics_collected` — nothing here counts a token.
When not one row could be priced the field is **absent from the entry, never zero**: an unknown
bill is not a bill of nothing. That is `prices.eur_of`, which returns `None` and is the only
addition this card made to the price table.

## Two of the four judges are still waiting on a declaration

A live call carries its own evidence for `consent` (the gate, from its log) and `grounded` (the
knowledge and its tool answers). `register` needs the register the business asked for and `leakage`
needs the strings the other tenants on this box own, and **neither is on `AgentConfig`**. They are
not invented here: a judge given a declaration nobody wrote would be judging a rule nobody wrote.
The card that puts them on the declaration adds one line each to `evals/score.py`.

## What this card also collapsed

livekit's usage rows became our wire's rows in two places, about to be three. They are now
`providers/usage.py:as_wire_rows`, read by the voice call's summary, the text call's and the
judge's bill. Three copies of a conversion is where two of them start disagreeing about a field.
