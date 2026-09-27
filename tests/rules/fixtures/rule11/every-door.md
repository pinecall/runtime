| | | |
|---|---|---|
| `WS` | `/v1/apps` | the app socket |
| `PUT` · `GET` · `DELETE` | `/v1/carrier` | the carrier |
| `POST` | `/v1/numbers` · `?dry_run=true` | import a number |
| `GET` | `/v1/members` · `POST` | the org's people |
| `PUT` | `/v1/ops/orgs/{named}/quotas` · `/v1/ops/orgs/{named}/dialling` | quotas |
| `GET` | `/v1/provider-keys` · `PUT`·`DELETE /v1/provider-keys/{vendor}` | keys |
| `POST` | `/v1/calls/{call}/listen` · `/supervise` | seats |
| `GET` | `/v1/agents/{slug}/threads?after=` · `/threads/{contact}` | threads |
| `POST` | `/v1/calls` · `/v1/calls/{call}/events` · `/sealed` · `GET /commands` | the worker's |
| `GET` | `/{path}` | the console |
