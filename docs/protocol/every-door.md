# Every door, in one table

The index of [gateway-api.md](gateway-api.md): one line per door, method and path, and what it
is for. The prose, the shapes and the refusals are on that page and in the pages it names.

| | | |
|---|---|---|
| `WS` | `/v1/apps` | the app socket: hold an agent, answer its tools |
| `WS` | `/v1/chat?agent=` | one text caller |
| `WS` | `/v1/attach?call=&token=` | a seat's live log, and the verbs back |
| `GET` | `/v1/usage?after=&limit=` | the org's metered rows, totals and cursor — `usage` |
| `GET` | `/v1/numbers` | the org's doors in the key's world, each with its source — `numbers` |
| `PUT` · `GET` · `DELETE` | `/v1/carrier` | the org's carrier: a Twilio account or a SIP peer, sealed under the vault key, named never secret |
| `GET` | `/v1/numbers/available` | what the carrier account owns, and which of it is imported |
| `POST` | `/v1/numbers` · `?dry_run=true` | import one number: the carrier's trunk pointed here, the SFU's trunk admitting it, the route — or the plan alone |
| `DELETE` | `/v1/numbers/{number}` | let a number go: the route and the admission; the carrier untouched |
| `POST` | `/v1/login/env` | the same person's key for the other world |
| `GET` | `/v1/sessions?limit=` | the org's newest calls across every agent, the same rows as an agent's |
| `GET` | `/v1/events` | SSE, live only: the org's floor changing — an agent held, a call ringing, up, over |
| `GET` | `/v1/members` · `POST` | the org's people; invite one, the token once |
| `PATCH` | `/v1/members/{id}` | role, agents, standing; disabled revokes their keys |
| `POST` | `/v1/invitations/{token}` | accept with a password: active, and the first key |
| `POST` | `/v1/login` | a key for a person and a device: org, email, password — or a code |
| `POST` | `/v1/login/codes` | a one-use code a key holder mints for a browser |
| `GET` | `/v1/whoami` | the org, the key's id, its label, the world it opens (`env`), its `scopes`, and whose it is (`subject`, `name`) |
| `GET` | `/v1/agents` | the agents this gateway is holding for your org |
| `GET` | `/v1/agents/{slug}/config` | what it declared, overrides applied |
| `GET` | `/v1/agents/{slug}/pipeline` · `PUT …/pipeline/overrides` | what it runs on, and the five knobs |
| `GET` | `/v1/agents/{slug}/provider-keys` | the org's own vendor keys, **in the clear**: the worker's door, see §6 |
| `GET` | `/v1/agents/{slug}/sessions` | one line per finished call |
| `GET` | `/v1/agents/{slug}/calls` | every call of the agent, as a log |
| `GET` | `/v1/calls/{call}/events` | one call's log: a page, or SSE |
| `GET` | `/v1/calls/{call}/state` | the call reduced |
| `GET` | `/v1/calls/{call}/recording` | the audio, seekable |
| `POST` | `/v1/calls/{call}/listen` · `/supervise` | a seat |
| `POST` | `/v1/calls/{call}/verbs` | one supervisor verb |
| `POST` | `/v1/tokens` | a room token for a browser — `503` and `fleet.full` when every worker is full |
| `POST`·`GET` | `/v1/callbacks` | a number to call back when the fleet was full, and the list of them |
| `GET` | `/v1/routes` | the numbers and doors your org answers |
| `PUT`·`DELETE`·`GET` | `/v1/provider-keys[/{vendor}]` | the org's own vendor accounts |
| `PUT`·`GET`·`DELETE` | `/v1/knowledge[/{base}]` · `POST …/eval` | the base the agent answers from |
| `GET`·`DELETE` | `/v1/contacts/{contact}/memory` · `POST /v1/contacts/memory/eval` | what it keeps about a person |
| `POST` | `/v1/agents/{slug}/memory/extraction` | what a hang-up makes of a call |
| `POST` | `/v1/evals/run` · `GET /v1/evals/runs[/{id}]` · `POST /v1/evals/replay/{call}` | the suites and ring 3 |
| `POST` | `/v1/evals/caller` · `/v1/evals/voice` | the improvising caller, and a spoken eval |
| `POST` | `/v1/calls` · `/v1/calls/{call}/events` · `/sealed` · `/tools` · `/lookup` · `/remember` · `GET /commands` | the worker's own doors |
| `POST`·`GET` | `/v1/fleet/heartbeat` · `/v1/fleet/standing` | the fleet's: what a worker holds, and whether all are full. The default org's key only |
| `GET`·`POST` | `/v1/whatsapp/webhook` | Meta's |
| | `/v1/ops/*` | the operator's, with the ops key — [operator-api.md](operator-api.md) |

`GET /openapi.json` is the generated schema of all of it, and `pinecall-runtime doctor` on the box
says which of these doors can actually answer today.
