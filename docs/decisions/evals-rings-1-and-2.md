# Rings 1 and 2 — livekit already carries them (tk-481ef4, 2026-09-07)

One chapter of [evals.md](evals.md), which indexes the rest. Nothing here was reworded:
it was moved.

Read in the installed package, `runtime/.venv/.../livekit/agents`, at 1.8.0. Paths below are
relative to `livekit/` inside it, the same convention `livekit-1.8.md` uses; that file's
invariant #21 is this chapter's headline.

## The verdict, in one line

**livekit 1.8 ships the turn-level runner, the event assertions, the binary judge, the tool
mock and eight conversation judges. It does not ship the simulator, the matrix, the goldens or
anything that reads OUR log.** So rings 1 and 2 are written on `session.run`, ring 4's judge
call has a shape already in the library, and what we write begins where a suite becomes a
*product*: many goldens, two models, a matrix, a promotion loop.

(This chapter was written when the matrix ran on DeepEval. On 2026-09-08 the judges moved onto
`livekit.agents.evals` too and DeepEval left the tree — [evals-judges.md](evals-judges.md). The
line above is unchanged in substance: the library carries the judging, we carry the product.)

## What the library carries

- **The runner.** `AgentSession.run(user_input=…, input_modality=…, output_type=…)`
  (`agents/voice/agent_session.py:809`) builds a `RunResult` and calls `generate_reply`; it
  refuses a nested run (`:815`). No room and no job context is a *supported* path, said out
  loud in `start()`: "these aren't relevant during eval mode" (`:1034`).
- **The recording.** `RunResult` (`run_result.py:98`) keeps four event types in the order they
  happened — `ChatMessageEvent`, `FunctionCallEvent`, `FunctionCallOutputEvent`,
  `AgentHandoffEvent` (`:69,75,81,87`) — reachable as `.events` (`:127`).
- **The assertions.** `.expect` (`:142`) is a cursor over that list (`RunAssert`, `:317`):
  `next_event(type=…)` (`:402`), `skip_next_event_if` (`:474`), `no_more_events()` (`:558`),
  `contains_function_call / contains_message / contains_function_call_output /
  contains_agent_handoff` (`:576,598,618,640`), and `expect[i]` / `expect[a:b]` (`:327`). One
  event is then judged by shape: `is_function_call(name=, arguments=)` (`:672`, arguments
  matched key by key against the parsed JSON, `:700-704`), `is_message(role=)` (`:743`),
  `is_function_call_output(output=, is_error=)` (`:711`), `is_agent_handoff(new_agent_type=)`
  (`:770`). A failure prints the whole event list with the cursor marked (`:380`).
- **The judge.** `ChatMessageAssert.judge(llm, intent=…)` (`:953`): one
  `check_intent(success, reason)` function tool (`:989`), `tool_choice="required"` (`:1033`),
  temperature 0 for every model but gpt-5 (`:1022-1025`), an OTel `judge_evaluation` span
  carrying the intent, the message and the token usage (`:1064`). It is ONE binary question
  with the evidence attached — the shape this milestone's rules already ask for.
- **The tool mock.** `mock_tools(AgentType, {name: fn})` (`:1132`), resolved per call by
  `type(session.current_agent)` and the tool NAME (`generation.py:951-956`) and swapped in at
  execution (`tool_executor.py:353-356`), with the arguments trimmed to the mock's own
  signature (`run_result.py:1174`). It reaches a raw-schema tool as well as a method.
- **Conversation judges.** `agents/evals/` (`__init__.py:20`) — `JudgeGroup`
  (`evaluation.py:78`) runs a list of `Evaluator`s over a `ChatContext` concurrently, scores
  pass 1 / maybe 0.5 / fail 0 (`:41-52`) and tags the session when it runs inside a job
  (`:194-198`); the eight built-ins are task_completion, handoff, accuracy, tool_use, safety,
  relevancy, coherence, conciseness (`judge.py:370-509`). `Judge` (`judge.py:166`) is the base
  class for a check that calls no model at all. Judges are pinned to the low inference class so
  they never compete with live traffic (`judge.py:16-30`, `evaluation.py:126`).
- **Verbosity.** `LIVEKIT_EVALS_VERBOSE=1` prints every recorded event and every judgment
  (`run_result.py:36,146,1076`).

## What it does NOT carry

- **The simulator.** The persona, the scenario run and the verdict of a whole conversation are
  a LiveKit **Cloud** service: the agent process only ever receives a `SimulationDispatch` and
  may veto the result with `ctx.fail()` (`agents/simulation.py:13-17,43,139`). The official
  starter says the same out loud — `scenarios.yaml` is run by `lk agent simulate` on Cloud,
  and its in-process eval is a commented-out example beside it
  (`agent-starter-python/tests/test_agent.py:1-5`, `AGENTS.md:45-47`). A self-hosted box has no
  simulator, so `pinecall simulate --persona` is ours to write, and so is `--voice`.
- **Anything that reads our log.** Ring 3 already exists here (`api/evals/`) and reads
  entries, not `RunResult`s. Ring 4 scores a call that is over: the judge SHAPE is livekit's,
  the case is built from the log.
- **The product.** No golden format, no matrix over two models, no deterministic graph, no cost
  or latency table, no promotion of a failing call to a golden, no drift alert.

## Ring by ring, who does it

| ring | the question | who carries it | so we write |
|---|---|---|---|
| 1 — a turn does what the class says | one utterance in, one reply out | `session.run` + `.expect` + `.judge` | the goldens and the headless session; no runner, no assertion library, no judge prompt |
| 2 — a tool is called with the right arguments | which tool, which arguments | `.expect.contains_function_call(name=, arguments=)` | the answers the app would have given |
| 3 — one real call, re-checked | consent, register, errors, latency | nothing: it reads OUR log | already written, `api/evals/` |
| 4 — every finished call, scored | did this call do its job | `agents/evals` judges + `RunResult.judge`'s one-binary-question shape | the case built from the log, the price, the row |
| the matrix, promotion, drift | many goldens over two models, over time | `agents/evals`: `Judge`, `JudgeGroup`, `Verdict` | `PolicyJudge` and the six policies on it, the matrix, the report |

## What `evals/` adds, and why none of it is a second copy

- `clinica.py` — the two goldens a text ring starts from. The declaration is captured from the
  class itself into `evals/goldens/clinica-norte/declaration.json` and turned into an
  `AgentConfig` by **the gateway's own conversion** (`api/agents/declaration.py`), so the
  wire→domain mapping exists once. The prompt's two regions are read from the tenant's own
  captures, `examples/clinica-norte/test/prompts/state-N.txt`, which `prompt-regions.test.ts`
  already pins byte for byte — there is no second copy of that text in this repo.
- `headless.py` — the worker's own path and nothing else: `kit_for(settings)` →
  `session/voice/session.py:a_session(config, kit, "whatsapp")` → `VoiceAgent`. The written door is
  what makes it text-only: `session/voice/session.py:27` builds no STT, no TTS and no VAD for it.
- `answers.py` — the app's side of a tool. `mock_tools` mocks a method on an `Agent` subclass;
  none of our tools is one — they are raw-schema callables whose body is a socket to the
  tenant's process (`session/declaring.py:35`). The `RunTool` callable is already the seam, so
  a ring stands in there rather than layering a second mock on top of it.
- `judges.py` — the judge is Haiku, built through `providers/models.py` like every other model
  in this tree. `RunResult.judge` takes any `llm.LLM`, so nothing adapts anything.

**Recapturing the declaration** — only when the class changes what it declares. From a checkout
with the workspace installed and `packages/pinecall` built:

```
cd examples/clinica-norte                      # tsx reads the tenant tsconfig from here:
                                               # without it the @tool decorators transform as
                                               # standard decorators and the class fails to load
node --import ../../packages/pinecall/node_modules/tsx/dist/loader.mjs \
     ../../evals/scripts/capture-declaration.mjs > ../../evals/goldens/clinica-norte/declaration.json
```

`pinecall test` (the milestone's own verb) takes it live off the socket instead, and the golden
becomes what a keyless run reads.

## What the three tests found on the first run

Both are the ring doing its job, and neither was softened away:

1. **The first turn is not stable.** The class says "Una sola pregunta por turno" and the view
   at stage `identify` says "pide nombre y teléfono". Haiku honours one or the other from run to
   run — name alone, or name and phone in the same breath. The test asserts what BOTH replies
   keep (it begins identifying the caller, invents no hour) and the contradiction is written
   here, for the tenant to resolve in the class.
2. **The view does not move.** A ring renders one state and holds it; on a real call a tool
   changes the state and the app re-renders. So a headless ring is one turn deep by
   construction, and a multi-turn walkthrough is `pinecall test` with the app in the loop.

## Two things this suite pays for and would rather not

- The runtime's `providers/pipeline.py:46` builds all three vendors before the channel decides
  which are used, so a written session constructs an STT and a TTS it never speaks to. The ring
  gives those two dead sentinel keys (`evals/tests/conftest.py`) rather than pretend it needs
  them. A box with only an LLM key would today refuse to serve a WhatsApp-only agent.
- livekit's anthropic plugin builds its own `httpx.AsyncClient`
  (`plugins/anthropic/llm.py:127-131`) and the base `LLM.aclose()` never closes it, so a test
  process leaks one connection per model. `ResourceWarning` is allowed in this distribution's
  pytest config, and nowhere else.
