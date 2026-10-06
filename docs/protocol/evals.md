# Evals — goldens run through the app, a call checked again, the simulated caller

An agent is tested in rings. A **golden** is a scripted conversation: the state it opens in, the
caller's lines, the facts the app's backend injects, and what is expected. A **run** plays every
golden under every model named, as written calls through the app socket that holds the agent,
and **judges** each call into a matrix of models by goldens. A **persona** is a caller a model
plays, one improvised line at a time, written or out loud. Every call that hangs up is judged
once more by the panel of hang-up judges, and its verdicts are the last entry of its log.

Every door below takes a key with the `evals` scope and acts in the key's org and world.

## A suite — `POST /v1/evals/run`

```
$ pinecall test --model anthropic/claude-haiku-4-5
POST /v1/evals/run
{"agent": "recepcion",
 "goldens": [{"name": "reserva", "state": {"stage": "book"}, "input": ["quiero el jueves"],
              "memory": ["prefiere la mañana"], "today": "2026-09-08",
              "events": [{"after_turn": 1, "name": "slot_freed", "data": {"at": "10:15"}}],
              "expect": {"tools": ["book"], "not": ["gratis"], "register": "usted"}}],
 "models": [{"provider": "anthropic", "model": "claude-haiku-4-5"}],
 "app": null, "voice": false}
```

The run drives the app that holds the agent in the key's scope (`app` names one, as
`WS /v1/chat?app=` does); nobody holding it is `404`. Each golden is one written call on the same
session a real caller gets, tuned where the app holds the agent, on the org's keys:

- it opens with no greeting: a golden starts in the middle of a conversation;
- its `state` is set as the app's `session.configure` would, before the first line;
- each `events` step is the app's `call.event`, after the caller line it names (`0` is before the
  first), so an event the agent never declared is refused by name (`400`) and the call ends;
- `memory` answers `recall` for this call alone; `search` is the real index;
- `today` is the day the model is told it is; absent, the real one;
- between steps the run waits for the log to go quiet: the app reacting is the only signal.

Each entry of `models` is a column; none is the agent's own, named `declared`. The answer is the
run, finished:

```json
{"id": "run_3f0c…", "agent": "recepcion", "started_at": 1790000000.1, "finished_at": 1790000031.4,
 "status": "done",
 "calls": [{"golden": "reserva", "model": "anthropic/claude-haiku-4-5", "call": "call_…"}],
 "matrix": {"models": ["anthropic/claude-haiku-4-5"], "goldens": ["reserva"],
            "metrics": ["consent", "heard", "tools", "silence", "register"], "judge_calls": 0,
            "runs": [{"model": "…", "golden": "reserva", "summary": {…},
                      "scores": [{"metric": "consent", "score": 1.0, "passed": true,
                                  "reason": "…", "criteria": "…", "judge_calls": 0}]}],
            "failures": []},
 "error": null}
```

A cell carries its call's own `call.summary`, never a number of its own; a cell where a judge did
not hold carries `asked`, every request the model was sent, to reproduce it. The row is written
before each call opens (so its log can be tailed) and after each call is judged.

`status: "failed"` is the run stopping early, and `error` says why: the app let go of the agent
(`the app detached after 1 of 3 goldens`), the org ran out of a quota (and `credits.exhausted` is
on the agent's log), or the fifteen-minute deadline passed. The cells judged so far stay. A
failing golden is a score, never a status.

One run per agent at a time: a second is `409` naming the run in flight.

`voice: true` says each golden out loud through the world's fleet: the room is dispatched, the
caller's lines are spoken (under `interferer_db` of a background voice and `packet_loss`, when
set), the room is deleted, and the call is judged once its worker sealed it. The worker builds the
session from the declaration, so a spoken run refuses `models` and a golden that pins `today`.

`GET /v1/evals/runs?agent=&since=&limit=` lists the runs of the key's org in its world, newest
first; `GET /v1/evals/runs/{id}` is one of them, and another org's is `404`.

A run may also name the org's **cases** (below): `cases: ["jueves-tarde"]` plays those by name,
and `dataset: true` plays every case of the agent that is not held out, which is what a nightly
run asks for; a case held out is played only when a run names it, which is what a release asks
for. `version: 4` builds every call of the run on that version of the agent's settings in the
scope of the app that holds it, instead of the one standing (a candidate, a canary's: see
[settings-api.md](settings-api.md)); a version the scope never had is `404`. Beside `models`, it
is how a run compares a candidate with what runs now. A run of cases is refused in production
(`403`): a case is a real caller's words, and it is played only in the sandbox, as written calls
through the app a developer holds there, never through production's app, whose tools act for real,
and never out loud or on a phone.

## The dataset — `POST /v1/evals/cases`, `GET /v1/evals/cases?agent=`, `DELETE /v1/evals/cases/{id}`

Real calls are the dataset. `pinecall runs promote` keeps a finished call as a **case**: its
caller's lines, the state it opened in (the first `state.changed` before the caller spoke), the
facts the app injected (`event.received` from the app, after the line they followed) and the day
it ran, as a golden the org names, with what it expects.

```
POST /v1/evals/cases
{"call": "call_…", "name": "jueves-tarde", "expect": {"says_any": ["jueves"]}, "held_out": false}

{"id": "case_3f0c…", "agent": "recepcion", "name": "jueves-tarde",
 "golden": {"name": "jueves-tarde", "state": {"stage": "book"}, "input": ["Quiero cita el jueves"],
            "today": "2026-09-29", "expect": {"says_any": ["jueves"]}, "promoted_from": "call_…"},
 "source_call": "call_…", "source_env": "production", "held_out": false,
 "author": "m_ana", "created_at": 1790000000.1}
```

The key must read the call, as a replay's does: a production call is promoted with a key of
production. The case is the org's, in both worlds, and played in the sandbox. A call still going is
`409`, one whose caller said nothing is `409`, and a name the agent has already is `409`.
`GET` lists the org's cases by agent and name; `DELETE` forgets one, and another org's, or one
nobody kept, is `404`.

A case is tenant data like the call it came from, and never outlives it: erasing the call, the
contact who made it, or the org erases the case in the same transaction (`tenancy/erasure.py`),
and so does the nightly retention run, which erases through the same path. The calls a run
plays a case in are calls of the sandbox like any other, under the org's retention there.

## The judge judged — `POST /v1/evals/calibration`, `GET /v1/evals/calibration?agent=`

A person who knows says what a judge should have answered on a finished call:
`POST /v1/evals/calibration {call, judge, held, note?}` keeps it (a second label of the same
call and judge replaces the first), in the call's world, by the key's person. `GET` answers each
judge against those labels, beside the verdict the seal counted for it on the same call:
`{judges: [{judge, labelled, compared, agreed, rate, trusted}], labels_to_judge, agrees_at_least}`.
A judge is judged once 10 labels have its verdict beside them, and `trusted` is false while it
agrees with fewer than 80 % of them: it is reported, never dropped silently, and until 10 it is
null. Erasing the call takes its labels.

## The judges

A judge is settled by code, or by one question to the judge model when code leaves the answer
open. A model that is unsure scores a half and never passes.

| metric | when | settled by |
|---|---|---|
| `consent` | always, first | code: every irreversible tool call ran after its own `confirm.granted`, for the same audience; a call with no `confirm.*` at all holds, saying so |
| `heard` | a golden with lines | code: every line reached the agent |
| `tools` · `not_tools` | `expect.tools`, `expect.not_tools` | code, the second naming the seq of the call that ran |
| `silence` · `says` | `expect.not`, `expect.says` | code, case-blind; the turn is named |
| `says_any` | `expect.says_any` | code, case-blind: any one of the phrases is enough, for an expectation with several right answers (`de 9 a 14`, `de nueve a dos`); the reason names the one said, or every one when none was |
| `grounded` | `expect.grounded`, and at hang-up | every price, hour, date and name stated is in the evidence of its scope (prices in the text shown, the rest in tool answers and states); what code cannot match goes to the model |
| `register` | `expect.register` | code: no word of the other register (`tú`, `usted`) |
| `replies` | `expect.replies` | code: the agent's turn after each fact names what it carried, or does not |
| `promises` | at hang-up | a phrase that commits the business goes to the model with every tool call |
| `identified` | at hang-up, an outbound call | code: the agent's first turn names the org, or says the org's opening sentence (`disclosure`, [gateway-api.md](gateway-api.md) §7) |
| `disclosed` | at hang-up | code: an agent turn says it is automated (an AI, a virtual or automated assistant) before the caller's second turn; failing that, the caller never asked whether a person was speaking, or the agent's next turn after they asked says so |
| `honoured_stop` | at hang-up | code: a caller who said *stop calling*, *remove me*, *no me llamen* and the like had their number put on the do-not-call list on that call (`call.opt_out`); nobody asking holds |
| `persona` | at hang-up, when the caller wrote a rule | the model reads the caller's `accepts_when`/`declines_when` |
| the agent's own | at hang-up, every call or only simulations | the model reads the question the org wrote for the agent |

## A finished call

`POST /v1/evals/replay/{call}` `{banned?, budget?}` runs six checks by code alone: consent,
the banned words, the errors the session did not recover from, each latency's **worst turn**
against the budget (seconds, under livekit's names; the default is 2 s end to end, 1 s to the
model's first token, 0.6 s to the voice's first byte), how much of the talking the agent did,
and how it took being interrupted.
A call nobody wrote and another org's are the same `404`.

Two of the budget's keys are the runtime's own measures. `dead_air` is the silence between the
caller stopping and the agent starting, one value per reply that followed the caller; a reply that
started before the caller stopped talked over them and is not counted. `talk_share` is the agent's
part of the time anybody spoke on the call, 0 to 1, and is judged by the `talk` check, which is
`skipped` when the budget names none. `interruption_delay` is how long a barge-in took to be
obeyed: from the caller starting to speak over the agent to the agent leaving `speaking`, one
value per reply written as interrupted, read off `user.state` and `agent.state`. None of the three
has a default: a budget that leaves them out does not judge them.

`interruptions` reads every reply of the agent's written as interrupted: the caller's words that
cut it off must be followed by a reply of the agent's, and that reply must not start over the one
cut off (its first four words the same as the cut reply's). A reply the agent went on with before
the caller said anything (a cough, a false start: livekit resumes it) and a caller who hung up
after cutting in are not judged; a call where nothing was cut off, or nothing cut off wanted an
answer, is `skipped`. The detail names the seqs of the cut reply, the caller's words and the reply.

```json
{"call": "call_…", "agent": "recepcion", "passed": true,
 "verdicts": [{"check": "consent", "status": "deferred", "detail": "…"}, …]}
```

`POST /v1/evals/judge/{call}` runs the hang-up panel on a finished call now and writes its
`call.score` on the sealed log: a call whose org judged nothing then, or whose judge failed. A
call judged already is `409` unless `?again=true`; a call still going is `409`. The agent's own
judges are the ones written when the door runs, not when the call ended.

## At hang-up

The seal judges every call when three things hold: the org judges its calls (`PUT /v1/org/judging`),
the box's providers row names a `judge` model, and that judge's `ceiling_usd` is above zero. The
panel is consent, grounded, promises, the compliance judges (`identified` on an outbound call,
`disclosed`, `honoured_stop`: settled by code, so every judged call carries them), persona when
the caller wrote a rule, then the org's own
judges and the agent's own (below), by name. The judge runs on the box's key, and the ceiling is
what one call may spend on it: a model judge asked once the calls before it reached the ceiling is
`skipped`, saying so, while the code judges still answer. Without a model the code judges still answer and the ones that needed a model are
`skipped`, saying why; a judge whose model failed is skipped too, and the call seals all the same.
`judge_calls` counts the model's requests and `judge_cost_usd` prices them at the row's rates. An
org that judges nothing gets `not_judged` saying so; a call an eval run opened is judged by the run.
Each settled verdict is counted into the day's drift, by the version of the agent's settings the
call ran and the hash of the judge's question, which `GET /v1/insights/drift` reads to say which
judge's pass rate moved ([console-api.md](console-api.md)).

## An agent's own judges — `GET /v1/agents/{slug}/judges`, `PUT` · `DELETE /v1/agents/{slug}/judges/{name}`

A judge of the agent's own is a question about its job that the org writes, one list per agent
for both worlds. At hang-up the judge model reads it with the whole call, both sides' turns and
the tool calls between them, and answers `held` or `broken`; its verdict is one more entry of
`call.score`'s `judges`, under the judge's name, and the name is in `panel`.

```
$ pinecall judges add offers-next-slot --asks 'The agent offered the next free slot.'
PUT /v1/agents/recepcion/judges/offers-next-slot
{"question": "The agent offered the next free slot.", "runs_on": "every-call"}

{"judges": [{"name": "offers-next-slot", "question": "The agent offered the next free slot.",
             "runs_on": "every-call", "author": "m_ana", "set_at": 1790000000.1}]}
```

The name is lower-case words joined by hyphens. `runs_on` is `every-call` (the default) or
`simulations`: a call a persona played, named on `call.started` or placed as the spoken caller of
`/v1/evals/voice`, and no other. Writing a name again replaces it; each door answers the agent's
list after it, and `DELETE` of a name nobody wrote is `404`. The judges run only when the seal
judges at all (the three conditions above), and each is one more request to the judge model.

## The simulated caller

Every line of the simulated caller is paid for by the box (its model and its voice), so each door
has a ceiling: a suite runs 200 goldens at most, a call 40 caller turns at most (`turns`,
`turns_left`); past them it is `422`.

`POST /v1/evals/caller` `{persona, heard, turns_left}` answers `{say, hangup}`: the persona's next
line on the call so far, on the persona's `llm` or the box's default, on the org's own key or the
one the box lends it. The caller never sees its own rule. A vendor this box lacks is `400`, one
nobody keyed `503`, a model that answered no line `502`.

`POST /v1/evals/voice` `{call, agent, persona, turns, interferer_db?, packet_loss?}` places a
spoken call: the agent is dispatched into the room named `call` with the persona and its rule on
the dispatch, the caller joins and speaks in a voice the agent does not use (the persona's own,
else one of the operator's voices for the language), and the room is deleted at the end whatever
happened. A persona is the agent's: a name nobody wrote for that agent is `404`; one sent
without a name is played as sent. A room nobody can hold is `503`. The answer is `{call, turns,
line, caller_cost_usd, stopped_at_ceiling}`: what the caller's lines and voice cost by the box's
rates, and whether it hung up because the call had spent the providers row's
`caller.ceiling_usd` ([operator-api.md](operator-api.md)); the line that crosses it is the last
one said. A model or a voice the rates do not price costs nothing here, as on a call.

## Personas — `GET /v1/agents/{slug}/personas`, `PUT` · `DELETE /v1/agents/{slug}/personas/{name}`, `GET /v1/agents/{slug}/personas/{name}/runs`

A persona belongs to one agent, in both worlds: two agents of the org may each have an `apurado`
of their own. `GET /v1/agents/recepcion/personas` lists that agent's callers.

```
PUT /v1/agents/recepcion/personas/apurado
{"goal": "cambiar la cita al martes", "style": "frases cortas", "facts": {"nombre": "Ana"},
 "llm": "anthropic/claude-haiku-4-5", "accepts_when": "le dan hora el martes", "was": null}
```

The name is lower-case words joined by hyphens; `was` renames, within the agent. A vendor this box
lacks is refused when written, not in the middle of a run. `GET
/v1/agents/{slug}/personas/{name}/runs` pages the calls the persona made to that agent in the
key's world, newest first, each with its turns, how it ended, its cost in
US dollars and the judges' score.
