# Ring 3 — one real call, re-evaluated

One chapter of [evals.md](evals.md), which indexes the rest. Nothing here was reworded:
it was moved. The consent rule this chapter calls is now `pinecall/types/consent.py` —
[evals-consent.md](evals-consent.md) says why it moved and what the move found.

Why `pinecall eval <id>` is one HTTP call to the runtime, what the four checks read, and
why one of them answers `deferred` instead of `failed`.

## The verb lives on the TypeScript side, and only there

The design's §16 puts `pinecall eval <call-id>` in the tenant's CLI, and `src/cli/groups.ts`
already declared it there as "ms-4". So this card built it there: `packages/pinecall/src/cli/eval.ts`
POSTs to `/v1/evals/replay/{call}` and prints the verdicts.

The Python CLI got no `eval` verb, and had no placeholder for one. That is the split the two
CLIs already live by: `pinecall` is for whoever builds the agent, `pinecall-runtime` is for
whoever operates the box. Judging a call needs the log, the store and — from ms-6 — a judge and
its provider keys, all of which are inside the runtime; the caller only needs to name the call
and hand over the case. A second implementation in Python would be a second place for the
verdict vocabulary to drift, for one command that a person on a laptop is the one who types.

§16 also spells `[--free]`, which means "no judge". No judge exists in this tree until ms-6, so
every run is free and the flag would be a no-op today. It lands with the judge.

## The door: `POST /v1/evals/replay/{call}`

On `KeyDep` — `api/_deps.py:a_key`, which is `auth/bearer.py:bearer_of` plus the key store,
the one bearer parser every other door of the runtime uses. Not the reader token: a browser token
minted for one call must never be able to run a scan over its transcript, and evals are the
tenant's own tool.

POST and not GET because the caller declares a **case** with the request: the words the business
will not have its agent say, and the latencies it holds the call to. The design says the same
thing about ring 1 — "el CLI construye el caso porque es el que sabe qué estado tiene la clase".
Both fields are optional; `pinecall eval --policy policy.json` is how a person sends them today,
and ms-6's generated `evals/policies` writes the same JSON.

It answers `{call, agent, fleet, passed, verdicts[]}`, where a verdict is three strings — check,
status, detail — and `passed` is false only when something actually **failed**.

## Four statuses, because two would lie

`passed` · `failed` · `deferred` · `skipped`. The last two exist so that a check which could not
judge anything never reads as a check that held:

- **deferred** — the runtime does not do this yet, on purpose, with a date. Only consent uses it.
- **skipped** — nothing this call carried could be judged: no words declared, no declaration for
  the agent, no turn that measured anything.

## Consent, and the gate that is not there

The rule is the one the design states: every irreversible `tool.call` preceded by a
`confirm.granted` for the same tool call and the same audience. It is no longer written here —
it moved to `pinecall/types/consent.py` so that this door, `ConsentJudge` and ring 4 all
ask one function, and [evals-consent.md](evals-consent.md) is its chapter. `consent.py` at this
door is now two things and nothing else: the registry's answer written onto the trace, and the
map from the rule's four words to these four statuses.

The deferral decided here survived the move and is the reason `deferred` exists at all. The gate
itself was removed on 2026-09-06 (`docs/decisions/confirm.md`): nothing in this runtime mints a
`confirm.granted`, so a real call today has irreversible tool calls and no `confirm.*` at all.
Printing FAIL for that would be the eval lying about the agent — the first lesson of the design doc
is to read the failure before fixing it, and this one is the platform's, not the call's. So:
**irreversible calls, and no `confirm.*` anywhere in the log → `deferred`, naming the date.** A log
that does carry `confirm.*` is judged in full, because a gate that exists and let a tool through
early is a real bug. Three fixtures, and they are read by both rings.

Which tools are irreversible is not in the log: `tool.call` carries a name and arguments, never a
side effect. It comes from the agent's live declaration in the registry (`ToolSpec.side_effect`,
`api/calls/sink.py:declared_by`, the same lookup the state door uses). If no app holds the agent
right now, the check answers `skipped` and says so — silence about a side effect must never read
as a call that consented to everything.

## Register, errors, latency

- **register** (`register.py`) — the words a business will not have its agent say, scanned over the
  agent's turns. Whole words, so banning `tú` does not ban `tútem`; an entry with a space is
  matched as a phrase, since that is the only way it could ever match. The *forms* are the
  framework's, the *words* are the business's: with none declared, the verdict is `skipped`.
- **errors** (`errors.py`) — the `error` entries the session wrote. One the session did not recover
  from fails the call; a recoverable one is named in the detail and still passes, because a retried
  request is not a failed call and hiding it would make the retry invisible.
- **latency** (`latency.py`) — the medians `log/latencies.py` reads off the call's turns, under
  livekit's own field names (`docs/decisions/livekit-metrics.md`), against a budget: `e2e_latency`,
  `llm_node_ttft` and `tts_node_ttfb`, because those are the three `DEFAULT_BUDGET` names. Medians
  and never a single turn: one interrupted turn moves an average, and the question is what a
  normal turn felt like.

**The budget.** `AgentConfig` carries none today — neither the wire's nor the domain's — and
`types/` is outside this card's surface, so the number is not invented into the agent's
declaration here. `DEFAULT_BUDGET` in `latency.py` is what a call is held to when nobody said
otherwise (2.0s end to end, 1.0s to the first token, 0.6s to the first byte of voice), and the
request's `budget` overrides it per call. When an agent learns to declare its own, the check takes
it unchanged: it is already a `Mapping[str, float]` argument.

## The fixtures are eval-only, and they are not goldens

`tests/api/evals/logs/` holds the three logs. They are **not** in
`protocol/fixtures/`, because that directory is the pair of goldens both languages must reduce
identically, and nothing in TypeScript reduces these: they exist to pin the consent check's three
answers. `protocol/fixtures/call-log-golden.json` stays the golden, and it is what
`sessions recording` and the metrics tests read.

## `sessions recording`

The recording's path is composed in exactly one place (`worker/recordings.py`) and stated in
exactly one entry: `call.summary.recording`, the log's last entry, which `log/logs.py` already
names `TERMINAL`. So `pinecall-runtime sessions recording <id>` reads the log, finds that entry,
and prints the path **alone on the line** — a verb whose output is meant to be an argument:
`-o "$(pinecall-runtime sessions recording CA_7)"`. A call with no summary yet, a call the box did
not record, and an id nobody wrote under each get their own sentence and exit 1; a blank line and
a zero would send a script copying nothing.

`sessions show` already prints every turn's metrics under livekit's own names — `cli/sessions/render.py`,
decided in `docs/decisions/cli.md`, pinned by
`tests/cli/sessions/test_render.py::test_every_metric_field_of_every_turn_is_printed_under_its_livekit_name`.
This card added nothing there on purpose.

## One reader per question

Two reads of a call existed twice when this landed, and the review sent them back. Both now have
one home in `log/`, which the CLI and the gateway both import and neither reaches across a process
boundary for:

1. **The latencies a call carried** — `log/latencies.py`. One name list (`MEASURES`, livekit's own
   five, in the order a turn happens), one `samples()` that reads them off the two entries a turn
   lands as, one `medians()` over that. `cli/sessions/render.py` draws the table `sessions show`
   ends with; `api/evals/replay.py` puts the same samples on the rebuilt call and
   `api/evals/latency.py` judges them against the budget — the budget's own keys decide which
   of the five are judged, which is why the eval needs no shorter list of its own.
   `cli/sessions/latency.py` is gone: the sampling was never the CLI's, and the rendering belonged
   with the rest of what `show` draws.
2. **Every entry of one call** — `log/replay.py:whole(store, call, after=, limit=)`, over the one
   page-until-a-short-page loop. Four callers: the gap that carries a snapshot, the state door's
   `Snapshots`, `cli/sessions/source.py` and the evals door.

`tests/log/test_latencies.py` and `tests/log/test_replay.py` pin both with the identity test this
repo already uses for `log/wording.py`: the consumers hold *the very same function object*, so a
second copy cannot appear without a red test.
