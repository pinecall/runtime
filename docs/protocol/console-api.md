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
