# What the console reads beside the agent's doors

The console is one client of the gateway; every screen is a door of [gateway-api.md](gateway-api.md)
or of the pages beside it. This page is the doors that exist for a screen and no worker:

| door | scope | what |
|---|---|---|
| `GET /v1/usage?after=&limit=` | `usage` | the org's metered rows after the cursor, `{rows, totals, next}`; one row per `call.summary` and `call.score` folded, the cursor the store's position so a billing consumer resumes losslessly |
| `GET /v1/insights?day=` | `calls` | one day of the key's world and scope, UTC: `{day, timezone, conversations: {today, yesterday}, resolved_rate, median_e2e_s, spend_usd, channels: {phone, web, whatsapp}, sessions_total, live, agents: [{slug, today, score}], budget: {limit_usd, spent_usd_month}}`; the budget is the org's across both worlds |
| `GET /v1/limits` | any key | each quota of the key's world as `{limit, used}` (`minutes`, `messages`, `llm_tokens`, `concurrent_calls`, `agents`, `seats`, `numbers`), `lends`, `billing_url` and `world`; `used` reads what admission reads, so the page and a refusal agree |
| `GET` · `PUT /v1/org/judging` | `calls` · `usage` | whether the org's calls are judged at hang-up, and the box's ceiling per call in dollars |
| `GET` · `PUT /v1/agents/{slug}/widget` | `talk` · `pipeline` | how the widget presents the agent per world: `{title, tagline, greeting, accent, autostart, theme}`; a null field is the widget's own default, an accent is a CSS colour |
| `GET /v1/agents/{slug}/pipeline` and the hold melody | `pipeline` | [pipeline-api.md](pipeline-api.md) |
| `GET` · `PUT /v1/agents/{slug}/settings`, `/v1/agents/{slug}/lexicon` | `pipeline` · `words` | [settings-api.md](settings-api.md) |
| `GET /v1/providers`, `/v1/provider-keys`, `/v1/voices`, `/v1/voices/sample` | `providers` · `pipeline` | [provider-keys.md](provider-keys.md) |
| `POST /v1/agents/{slug}/dev/{family}/{verb}` | the family's | [dev-verbs.md](dev-verbs.md) |
| `/v1/agents/{slug}/personas`, `/v1/evals/*` | `evals` | [evals.md](evals.md) |
| `/v1/agents/{slug}/judges` | `evals` | the agent's own judges: [evals.md](evals.md) |
| `/v1/knowledge`, `/v1/contacts/{contact}/memory`, `/v1/agents/{slug}/memory` | `knowledge` · `memory` | [../retrieval/spec.md](../retrieval/spec.md) |
| the threads, the line, the numbers, the accounts | | [whatsapp.md](whatsapp.md) · [numbers.md](numbers.md) · [people.md](people.md) |

Money is US dollars everywhere: a call's cost, the judge's, a budget.
