# The console's doors

The doors a console reads a whole org through — a filtered list of calls with their verdicts, a
day at a glance, an inbox per contact, memory across callers, the widget's settings, whether the
org's calls are judged — beside [gateway-api.md](gateway-api.md), which is every other door. Every
answer's shape is the protocol's own (`protocol/schema/rest.json`), and every field added to a
shape that already existed is optional, so an older console reads a newer gateway.

All of them answer in the **reader's corner** — the key's org, its world, and in the sandbox whose
copy (`pinecall-corner` opens a colleague's, [multi-tenancy.md](../multi-tenancy.md)) — and count
off the **call index**: one row per call the store folds as it appends each entry (`log/facts.py`,
`call_facts`), never a fold of every log. A call from before migration 0025 is in no count until
`pinecall-runtime migrate up --post` has folded it (0026).

## 1. Sessions: filtered, counted, paged, judged

`GET /v1/sessions` and `GET /v1/agents/{slug}/sessions` (`calls`) take:

| parameter | |
|---|---|
| `q` | matched case-insensitively against the call id's start, the digits of either number (`600 12` finds `+34 600 123 456`), the caller's name and the outcome. `%` and `_` are characters, not wildcards |
| `agent` | `/v1/sessions` only: one agent's calls. The agent's door is one agent's already |
| `channel` | `phone` · `web` · `whatsapp`; anything else is `422` |
| `before` | the `next` of the page before: the list continues below that call |
| `limit` | 1–200, 20 when unsaid |

`SessionList` is `{calls, total, next}`: `total` counts every call that matches, every page
together, and `next` is the last call of this page, or `null` on the last page. Newest first by
when the call's log opened, ties by call id.

Each `SessionLine` carries two more fields. `score` is `{held, judged, passed, reason}` — how many
judges answered `held` of how many answered `held` or `broken` (a `skipped` or `deferred` judge
settled nothing and is not counted), `passed` false when one broke, and `reason` the first broken
judge's own sentence — or `null` when nobody settled anything about the call. `flags` is what a
person reviewing calls looks at first:

| flag | raised when |
|---|---|
| `escalated` | a person took part: `supervisor.took_over`, `.said`, `.ended`, `.transferred`, `call.transferred`, or the call ended `transferred`/`supervisor_ended`. A whisper is advice and raises nothing |
| `low_score` | `score.passed` is false |
| `promise` | the **`promises`** judge answered `broken`: the agent committed the business to something — a call back, a visit, a price, sending something, a follow-up — that no tool call records |

`promises` is a model judge shaped like `grounded` (`evals/judges/promises.py`): code finds the
sentences that could commit, in Spanish and in English, and a call with none holds for free; when
there are some, the judge model is asked once with every tool call of the call as evidence, under
the same per-call ceiling as every judge. With no model to ask, it is `skipped`, and no flag rises.

## 2. Insights: a day at a glance

`GET /v1/insights?day=YYYY-MM-DD` (`calls`) — today when `day` is left out. **The day is cut in
UTC**: an org carries no timezone, and the answer says `"timezone": "UTC"`. Every count is of calls
whose log **opened** in the day, in the reader's corner; `budget` is the org's, both worlds.

```json
{ "day": "2026-09-17", "timezone": "UTC",
  "conversations": { "today": 42, "yesterday": 37 },
  "resolved_rate": 0.93, "median_e2e_s": 1.21, "spend_eur": 3.84,
  "channels": { "phone": 30, "web": 9, "whatsapp": 3 },
  "sessions_total": 5120, "live": 2,
  "agents": [ { "slug": "bidfire-dispatch", "today": 40, "score": 0.97 } ],
  "budget": { "limit_eur": 300, "spent_eur_month": 88.2 } }
```

| field | how it is counted |
|---|---|
| `resolved_rate` | of the day's calls that **ended**, the share no person took part in — no `escalated` flag (§1). `null` when none ended |
| `median_e2e_s` | the median `e2e_latency` over every agent turn of the day's calls. `null` when no turn measured one |
| `spend_eur` | `call.summary`'s `cost.eur`, summed over the day's calls |
| `sessions_total` · `live` | every call the corner ever held; the ones whose log is not sealed yet |
| `agents[].score` | the share of judges that held (`held / judged`, 0..1), averaged over the agent's judged calls of the day; `null` when none was judged |
| `budget` | `limit_eur` is the operator's `budget_eur` ([operator-api.md](operator-api.md)), `null` for none; `spent_eur_month` is what the org's calls that opened in the day's calendar month cost, every world and corner. A budget is shown and never enforced |

Three indexed reads of the call index and one of the quotas, whatever the day.

## 3. Judging: on, off, and the ceiling

`GET /v1/org/judging` (`calls`) answers `{on, ceiling_eur}`; `PUT /v1/org/judging {on}` (`usage`:
what an org spends is its manager's and admin's to decide) turns it for every call that hangs up
from then on. It is the **org's**, both worlds: the judges are the platform's measurement and their
cost is the org's bill. `ceiling_eur` is the box's `PINECALL_JUDGE_CEILING_EUR` — what judging one
call may spend on a model — which a tenant reads and does not set.

Off, a hang-up asks no judge at all and seals the call with a `call.score` carrying no verdict and
`not_judged: "this org's calls are not judged at hang-up: …"`. A written call reads the setting in
the gateway; a spoken one is judged in the worker, which asks `GET /v1/calls/{call}/judging` (`app`,
the worker's own door, the call's org) before its judges run — a gateway it cannot reach is a call
judged as before, still under the ceiling. Such a call can be judged later: §6.

## 6. Judging a call later

`POST /v1/evals/judge/{call}` (`evals`) runs the hang-up's judges over a finished call — one its org
had judging off for, one whose judge broke, or one a person wants a second opinion on — writes the
`call.score` onto the call's own log and answers it. That log is sealed, and a verdict is the one
entry a sealed log takes (`Store.rescored`): the list, the day and `usage` read it like the first.

| answer | when |
|---|---|
| `200` `CallScore` | judged, under the box's ceiling exactly as at hang-up |
| `404` | no such call in the key's org, world and corner — another org's call is told the same |
| `409` | the call has not ended; or its last `call.score` already carries a verdict and `?again=true` was not said |

## 4. Threads: the inbox by contact

A **contact** is who a call is filed under: the id the app resolved (`caller.id`), else the number
or visitor id the call came from. Every door below is one agent's, in the reader's corner.

| door | scope | |
|---|---|---|
| `GET /v1/agents/{slug}/threads?after=&limit=` | `calls` | `{threads: [{contact, name, channel_last, last: {text, at, kind}, unread, calls}], next}`, the thread that moved last first. `kind` is `in`, `out`, or `call` for a spoken call, whose `text` is its outcome. `after` is the `next` of the page before; `limit` 1–200, 30 unsaid |
| `GET /v1/agents/{slug}/threads/{contact}` | `calls` | `{contact, name, messages: [{kind, text, at, call, channel, duration_s?, answered?}]}`, oldest first, merged from the contact's 20 newest calls: a written call is its turns, a spoken one is one pill with its length and whether it came up. `404` when the contact has no call with the agent here |
| `POST /v1/agents/{slug}/threads/{contact}/read` | `calls` | `204`: this reader has read the thread up to now |
| `POST /v1/agents/{slug}/threads/{contact}/messages {text}` | `talk` | `202 {contact, call}`: said as the agent on the contact's open WhatsApp conversation |

`unread` is per **person** (the key's member, or the key itself for a machine key): what arrived
after they last marked the thread read — each message the contact wrote, and each spoken call.
`name` is the caller's name when a call recorded one; nothing writes one today but an app's
`caller`, so it is usually `null`.

A message is said through the path a supervisor's `say` takes: `supervisor.said` and `turn.agent`
land on the conversation's own log, the thread sends `turn.agent` to the contact, and the call is
flagged `escalated` — a person spoke in it. It is refused with `409` and the reason when the
contact's newest call is not WhatsApp, when WhatsApp's customer-service window closed (24 h after
the contact's last message: only a template may be sent then, and this door sends none), and when
the conversation already idled out and sealed (after two hours of silence): a sealed log takes no
turn, and the contact's next message opens the conversation a message is said on.

**Dialling out is not a door.** `POST /v1/calls {agent, to}` was asked for and is not built: that
path is already the worker's own door (a call opened, its log created), no runtime handler exists
for the protocol's `call.dial`, and a call placed from this box needs an outbound SIP trunk per org
that nothing provisions — the only outbound leg today is `room.invite` inside a call that is
already up, through a trunk no org configures. A door that minted a room and dispatched a worker
toward a number with no trunk behind it would ring nothing and bill a room.

## 5. Memory across callers

`GET /v1/agents/{slug}/memory?after=&q=&limit=` (`memory`) is `{facts: [{id, contact, text, category,
written_at}], next}`: the **current** facts the agent's calls taught, across every contact, newest
first, in the key's world and corner. A fact is the agent's through the call that taught it (its
`source_call`, whose head row names the agent), so a fact a memory golden held came from no call and
is in no agent's list. `q` matches the text, the contact and the category, case-insensitively;
`after` is the `next` of the page before; `limit` is 1–200, 50 unsaid.

`DELETE /v1/memory/facts/{id}` (`memory`) ends **one** fact the way a later call would have: the
row stays with `invalidated_at` set to now, recall stops reading it from that moment, and the
contact's history (`GET /v1/contacts/{contact}/memory`) still shows it, superseded. It answers
`{"forgotten": 1}`; `404` when no current fact of the key's org, world and corner answers the id
(already forgotten included), `422` for an id that is not a UUID. Erasing a person whole is still
`DELETE /v1/contacts/{contact}/memory`.
