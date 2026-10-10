# Evals — goldens run through the app, a call checked again, the simulated caller

An agent is tested in rings. A **golden** is a scripted conversation: the state it opens in, the
caller's lines, the facts the app's backend injects, and what is expected. A **run** plays every
golden under every model named, as written calls through the app socket that holds the agent,
and **judges** each call into a matrix of models by goldens. A **persona** is a caller a model
plays, one improvised line at a time, written or out loud. Every call that hangs up is judged
once more by the panel of hang-up judges, and its verdicts are the last entry of its log.

Every endpoint below takes a key with the `evals` scope and acts in the key's org and environment.

## A suite — `POST /v1/evals/run`

```
$ pinecall test --model anthropic/claude-haiku-5-5
POST /v1/evals/run
{"agent": "recepcion",
 "goldens": [{"name": "reserva", "state": {"stage": "book"}, "input": ["quiero el jueves"],
              "memory": ["prefiere la mañana"], "today": "2026-09-08",
              "events": [{"after_turn": 1, "name": "slot_freed", "data": {"at": "10:15"}}],
              "expect": {"tools": ["book"], "not": ["gratis"], "register": "usted"}}],
 "models": [{"provider": "anthropic", "model": "claude-haiku-5-5"}],
 "app": null, "voice": false}
```

The run drives the app that holds the agent in the key's scope (`app` names one, as
`WS /v1/chat?app=` does); nobody holding it is `404`. Each golden is one written call on the same
session a real caller gets, tuned where the app holds the agent, on the org's keys:

- it opens with no greeting: a golden starts in the middle of a conversation;
- its `state` rides the call's `call.started` (its `state` field), and the app opens the call in
  it before the first render, as `WS /v1/chat?state=` does; the run declares no state of its own;
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
 "calls": [{"golden": "reserva", "model": "anthropic/claude-haiku-5-5", "call": "call_…"}],
 "matrix": {"models": ["anthropic/claude-haiku-5-5"], "goldens": ["reserva"],
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

`voice: true` says each golden out loud through the environment's fleet: the room is dispatched, the
caller's lines are spoken (under `interferer_db` of a background voice and `packet_loss`, when
set), the room is deleted, and the call is judged once its worker sealed it. The worker builds the
session from the declaration, so a spoken run refuses `models` and a golden that pins `today`.
The golden's `state` rides the dispatch, and the worker writes it on the call's `call.started`.

`GET /v1/evals/runs?agent=&since=&limit=` lists the runs of the key's org in its environment, newest
first; `GET /v1/evals/runs/{id}` is one of them, and another org's is `404`.

A run may also name the org's **cases** (below): `cases: ["jueves-tarde"]` plays those by name,
whatever their status, so a case still waiting for a person can be reproduced, and `dataset: true`
plays every case of the agent a person approved, that is not held out and not kept in the
repository, which is what a nightly run asks for; a case held out is played only when a run names
it, which is what a release asks for. `version: 4` builds every call of the run on that version of
the agent's settings in the scope of the app that holds it, instead of the one standing (a
candidate, a canary's: see [settings-api.md](settings-api.md)); a version the scope never had is
`404`. Beside `models`, it is how a run compares a candidate with what runs now. A run of cases is
refused in production (`403`): a case is a real caller's words, and it is played only in the
sandbox, as written calls through the app a developer holds there, never through production's
app, whose tools act for real, and never out loud or on a phone.

## The dataset — `POST` · `GET /v1/evals/cases`, `PATCH` · `DELETE /v1/evals/cases/{id}`, `GET /v1/calls/{call}/golden`

Real calls are the dataset, and the loop that keeps it is: a call a judge broke on is kept at
hang-up as a **pending** case; a person reads it, reproduces it (`cases: [name]` on a run),
fixes the agent, and approves it into the nightly or dismisses it. A case is a golden made of the
call: its caller's lines, the state it opened in (the first `state.changed` before the caller
spoke), the facts the app injected (`event.received` from the app, after the line they followed),
the facts `recall` gave it (as the golden's `memory`), and the day it ran.

**Born at hang-up.** After the panel (below), a call whose score did not pass is kept as a case
`pending`, by `the hang-up panel`, named after the first judge that broke, the caller's first
four words and the call's last six characters (`promises-me-llaman-manana-por-29d7c7`). Its
`expect` is what the broken verdicts say must not happen again, and never what was right:

| broke | the case's `expect` |
|---|---|
| `consent` | `not_tools`: the tool that ran unasked, by the seqs the verdict cites |
| `grounded` | `grounded: true` |
| any other judge of the library, the org's own, the agent's own | `judges: [name]`, asked again of the replay |
| `expected-outcome` | nothing: its expectations are a simulated caller's, and a case plays written lines |

A call a simulated caller made is never kept (it is meant to break things), nor one whose caller
said nothing, nor a call already kept. At most **50** cases wait per agent: past it a broken call
is judged as ever and not kept, until a person decides some. A write that breaks is logged and the
call seals all the same.

**Kept by hand.**

```
POST /v1/evals/cases
{"call": "call_…", "name": "jueves-tarde", "expect": {"says_any": ["jueves"]}, "held_out": false}

{"id": "case_3f0c…", "agent": "recepcion", "name": "jueves-tarde",
 "golden": {"name": "jueves-tarde", "state": {"stage": "book"}, "input": ["Quiero cita el jueves"],
            "today": "2026-09-29", "expect": {"says_any": ["jueves"]}, "promoted_from": "call_…"},
 "source_call": "call_…", "source_env": "production", "held_out": false,
 "author": "m_ana", "created_at": 1790000000.1, "status": "approved", "broke": [],
 "source_version": 4, "kept_in_repo": false}
```

A case kept by hand is `approved`. An `expect` left out is the one the call's broken verdicts
give, as at hang-up. The key must read the call, as a replay's does: a production call is kept
with a key of production. The case is the org's, in both environments, and played in the sandbox.
A call still going is `409`, one whose caller said nothing is `409`, and a name the agent has
already is `409`.

**Read as a golden, kept nowhere.** `GET /v1/calls/{call}/golden?name=&from_seq=` answers the
golden the call makes, with the same derived `expect`; `from_seq` opens it in the state the call
was in at that seq and plays only the lines after it: what `pinecall runs promote` writes to the project's
`test/candidates/` for a case that names the code (a stage, a tool, an event) and belongs in the
repository.

**The inbox.** `GET /v1/evals/cases?agent=&status=` lists the org's cases, the pending first,
newest first, and says how many wait: `{cases, pending, pending_at_most}`. `PATCH
/v1/evals/cases/{id}` writes what a person decided, each field set:

| field | does |
|---|---|
| `status` | `approved` (the nightly plays it), `dismissed`, or `pending` again; the person and the time are kept |
| `held_out` | played only when a run names it |
| `kept_in_repo` | written into the repository as a golden: the nightly leaves it to the file |
| `judge_was_wrong` + `note` | with `dismissed` only: the judge that broke should have held, kept as a calibration label of the call (below), in the call's own world, so the key must read the call; a judge the case did not break on is `400`, and so is a `note` without a judge |

`DELETE` forgets one, and another org's, or one nobody kept, is `404`.

A case is tenant data like the call it came from, and never outlives it: erasing the call, the
contact who made it, or the org erases the case in the same transaction (`tenancy/erasure.py`),
and so does the nightly retention run, which erases through the same path. The calls a run
plays a case in are calls of the sandbox like any other, under the org's retention there.

## The judge judged — `POST /v1/evals/calibration`, `GET /v1/evals/calibration?agent=`

A person who knows says what a judge should have answered on a finished call:
`POST /v1/evals/calibration {call, judge, held, note?}` keeps it (a second label of the same
call and judge replaces the first), in the call's environment, by the key's person. `GET` answers each
judge against those labels, beside the verdict the seal counted for it on the same call:
`{judges: [{judge, labelled, compared, agreed, rate, trusted}], labels_to_judge, agrees_at_least}`.
A judge is judged once 10 labels have its verdict beside them, and `trusted` is false while it
agrees with fewer than 80 % of them: it is reported, never dropped silently, and until 10 it is
null. Erasing the call takes its labels.

## The judges

Every judge is one question a model answers about a finished call (below, *Judges*). A golden is
held to them and to checks settled by code, which read what the golden expects:

| check | when | settled by |
|---|---|---|
| `heard` | a golden with lines | code: every line reached the agent |
| `tools` · `not_tools` | `expect.tools`, `expect.not_tools` | code, the second naming the seq of the call that ran |
| `silence` · `says` | `expect.not`, `expect.says` | code, case-blind; the turn is named |
| `says_any` | `expect.says_any` | code, case-blind: any one of the phrases is enough, for an expectation with several right answers (`de 9 a 14`, `de nueve a dos`); the reason names the one said, or every one when none was |
| `register` | `expect.register` | code: no word of the other register (`tú`, `usted`) |
| `replies` | `expect.replies` | code: the agent's turn after each fact names what it carried, or does not |
| `consent` | always, first | the library's judge, asked of the golden's call |
| `grounded` | `expect.grounded` | the library's judge, asked of the golden's call |
| any judge, by name | `expect.judges` | the library's, the org's own or the agent's own judge asked of the golden's call, whether or not the org switched it on; a name nobody wrote breaks the golden |

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
judges are the ones written when the endpoint runs, not when the call ended.

## At hang-up

The seal judges every call when three things hold: the org judges its calls (`PUT /v1/org/judging`),
the platform's providers row names a `judge` model, and that judge's `ceiling_usd` is above zero.
The panel is the library's judges switched on for the agent, then the org's own, then the
agent's own, each asked once.

**The judge model** is the agent's `judge` (its class's, else its settings'), else the org's
(`PUT /v1/org/judging {on, model}`, the model tried on the org's keys before it is kept), else
the providers row's on the platform's key. A model the org names runs on its own key for the
vendor when it has one — a local model's server among them, through `options`
`{"base_url": …}` — and then its answers are never billed (`call.score.own_key`) and no
ceiling of the platform's applies; on a key the platform lends, or on the platform's own judge,
the org pays for its answers in **evals** (below), and the providers row's `ceiling_usd` is what
one call may spend: a judge reached past it is `skipped`, saying so, as is one whose model failed,
and the call seals all the same. The judge is asked for a forced tool call, so a local model must
call tools. `judge_calls` counts the model's requests (a trigger's yes-or-no among them),
`judge_cost_usd` prices them at the row's rates, and `evals` counts the judges that answered. An
org that judges nothing gets `not_judged` saying so; a call an eval run opened is judged by the
run. Each `held` and `broken` is counted into the day's drift, by the version of the agent's
settings the call ran and the hash of the judge's question, which `GET /v1/insights/drift` reads
to say which judge's pass rate moved ([console-api.md](console-api.md)); `na` and a classification
are not. A call that did not pass is kept as a pending case (the dataset, above).

## Judges — `GET` · `PUT` · `DELETE /v1/org/judges[/{name}]`, `/v1/agents/{slug}/judges[/{name}]`

A judge is a name, a question, how it answers, when it runs and what it reads. It answers
`verdict` (`held` or `broken`), `choice` (one of two or more `choices`, written `classified`
with the `choice`) or `score` (1 to 5, `classified` with the `score`); any of them may answer
`na`, the question did not apply. Only a verdict judge sets `passed`. It runs `always`, only on a
call a simulated caller played (`simulations`), or on a `trigger`: a short yes-or-no asked first,
and a no is `na` without the question. It always reads the whole call, both sides' turns and the
tool calls between them, each line under its log position, and cites the positions it rests on;
`reads` adds `prompt` (the agent's prompt as the call ran it), `evidence` (the text the call
carried, the tool answers, the states) and `facts` (the org, the direction, the irreversible tools
and the confirmations, an opt-out, the disclosure sentence, how it ended, what a simulated caller
expected).

**The library** is Pinecall's, the same for every org, each judge's question in the runtime's
`evals/library/judges/` under a version: `consent`, `grounded`, `promises`, `disclosed`,
`identified`, `honoured-stop`, `ended-well` and `expected-outcome` are on by default; `relevance`,
`repetition` and `sentiment` are off. Some settle as `na` by a fact before any model is asked:
`consent` and `promises` when no irreversible tool ran, `identified` on a call that came in,
`expected-outcome` when the simulated caller expected nothing; an `na` is never an eval.

```
$ pinecall judges add offers-next-slot --asks 'The agent offered the next free slot.'
PUT /v1/agents/recepcion/judges/offers-next-slot
{"question": "The agent offered the next free slot.", "when": "always", "reads": ["prompt"]}

{"judges": [{"name": "consent", "owner": "pinecall", "on": true, "question": "…", "answer": "verdict",
             "choices": [], "when": "always", "trigger": "", "reads": ["facts"],
             "summary": "…", "version": 1}, …,
            {"name": "offers-next-slot", "owner": "recepcion", "on": true, "question": "…",
             "answer": "verdict", "choices": [], "when": "always", "trigger": "",
             "reads": ["prompt"], "author": "m_ana", "set_at": 1790000000.1}]}
```

A list is the library first, then the org's own (`owner: "org"`), then, on an agent's, the
agent's own (`owner`: its slug); one list serves both environments. `PUT` of a library name takes
`{"on": true}` or `{"on": false}` and nothing else (a question is `409`): at `/v1/org/judges` it is
the org's default for every agent, at an agent's it is that agent's, and the agent's wins. `PUT`
of any other name writes one of the org's own or the agent's own whole (`on` is `409`: it runs
while it is written); the name is lower-case words joined by hyphens, and the org's and an
agent's may not share one. `DELETE` forgets one of the org's own or the agent's own: a name
nobody wrote is `404`, a library name `409`.

`POST /v1/agents/{slug}/judges/try {name, …, last?, calls?}` asks one judge of the agent's
`last` 1 to 50 finished calls, or of the `calls` named, and writes nothing: a library judge or a
written one by `name` alone, or a judge not yet saved, written whole in the body.
`{rows: [{call, judgment, not_judged?}], evals, cost_usd}`; a call still going is a row
`not_judged`.

**Evals.** One eval is one judge that answered one call's question: `held`, `broken` or
`classified`. `na`, `deferred` and `skipped` are never counted. `call.score` carries `evals`, and
the usage feed counts those the platform's key answered into the org's month; on the org's own key
(`own_key`) they are counted nowhere ([limits.md](../limits.md)).

## The simulated caller

Every line of the simulated caller is paid for by the platform (its model and its voice), so each endpoint
has a ceiling: a suite takes 200 goldens, 200 cases and 8 models at most, a golden 40 caller lines
(`input`), a call 40 caller turns (`turns`, `turns_left`); past them it is `422`. And a run plays
200 calls at most in all, its goldens and the cases it joins each played under every model:
`400` past them, with the count.

`POST /v1/evals/caller` `{persona, heard, turns_left}` answers `{say, hangup}`: the persona's next
line on the call so far, on the persona's `llm` or the platform's default, on the org's own key or the
one the platform lends it. The caller never sees its own rule. A vendor this deployment lacks is `400`, one
nobody keyed `503`, a model that answered no line `502`. Each line is a model call, so an org says
120 a minute at most (`429` past them), `heard` holds 80 turns, and a turn, the `goal` and the
`style` 4000 characters each (`422` past them).

`POST /v1/evals/voice` `{call, agent, persona, turns, interferer_db?, packet_loss?, state?}` places
a spoken call: the agent is dispatched into the room named `call` with the persona, its rule and
`state` (the object the agent opens the call in, on its `call.started`; absent, the class's own) on
the dispatch, the caller joins and speaks in a voice the agent does not use (the persona's own,
else one of the operator's voices for the language), and the room is deleted at the end whatever
happened. `call` is minted by the client as the platform mints one (`call_` and a word, `422` for any
other shape) so it tails the log before the call starts, and it must name a call nobody opened:
the head is claimed in the caller's scope before the room is offered, and an id that exists —
anybody's — is `409`. A persona is the agent's: a name nobody wrote for that agent is `404`; one
sent without a name is played as sent. A room nobody can hold is `503`. The answer is `{call, turns,
line, caller_cost_usd, stopped_at_ceiling}`: what the caller's lines and voice cost by the platform's
rates, and whether it hung up because the call had spent the providers row's
`caller.ceiling_usd` ([operator-api.md](operator-api.md)); the line that crosses it is the last
one said. A model or a voice the rates do not price costs nothing here, as on a call.

`POST /v1/simulations` `{agent, persona, voice?, turns?, interferer_db?, packet_loss?}` is the
simulation the console starts: one of the agent's personas, by name, put on the agent as anybody
calls it, so whatever holds it answers — a process deployed, on a server of the org's, or a
developer's `pinecall start`. Spoken (`voice`, the default) it is the call above; written, it is
the chat the widget has, each line the persona's model improvises said once the agent's whole
answer is in, until the caller hangs up (sixty seconds with no answer end the call `timeout`). The
persona's rules ride the call, which is sealed and judged like any other. What can be refused is
refused before the answer: a persona nobody wrote for the agent, or nobody holding it in the
key's world and scope, `404`; the org's limits as on a call. The answer is `{call, voice}` at once,
the call minted by the platform, and the conversation goes on in the gateway: it is watched as any
call is, and `GET /v1/simulations` lists it. Its tools run for real, as on any call.

## Personas — `GET /v1/agents/{slug}/personas`, `PUT` · `DELETE /v1/agents/{slug}/personas/{name}`, `GET /v1/agents/{slug}/personas/{name}/runs`

A persona belongs to one agent, in both environments: two agents of the org may each have an `apurado`
of their own. `GET /v1/agents/recepcion/personas` lists that agent's callers.

```
PUT /v1/agents/recepcion/personas/apurado
{"goal": "cambiar la cita al martes", "style": "frases cortas", "facts": {"nombre": "Ana"},
 "llm": "anthropic/claude-haiku-5-5", "accepts_when": "le dan hora el martes", "was": null}
```

The name is lower-case words joined by hyphens; `was` renames, within the agent. A vendor this deployment
lacks is refused when written, not in the middle of a run. `GET
/v1/agents/{slug}/personas/{name}/runs` pages the calls the persona made to that agent in the
key's environment, newest first, each with its turns, how it ended, its cost in
US dollars and the judges' score.
