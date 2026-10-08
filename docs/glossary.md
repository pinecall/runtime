# Glossary — the words of the domain

| word | meaning |
|---|---|
| **environment** | production or the sandbox. A property of a row (`env`) and of a key's prefix (`pc_live_`, `pc_test_`), never of a deployment: one gateway, one database and one name (`PINECALL_DOMAIN`) serve both, so the key or the `pinecall-env` header says the environment, never the address. The CLI and some messages call it the *world* |
| **deployment** | one installation of the runtime: the gateways, the workers, LiveKit, SIP, Redis and Postgres, on one Kubernetes cluster (`infra/`). Pinecall Cloud is one; a self-hosted runtime is another |
| **operator** | the person who runs a deployment: admits carriers, sets what a new org may use, holds the vendor keys the deployment lends |
| **fleet** | the workers that answer one environment's calls, registered under one LiveKit agent name (`PINECALL_FLEET`). A sandbox call is only ever dispatched to the sandbox's fleet |
| **scope** | the triple `(org, env, holder)` a request acts in: whose data, which environment, and which app socket holds the agent. Every endpoint resolves it from the key and the `pinecall-env` header. A key's *scopes* (`calls`, `numbers`, `team`…) are something else: what that key may open |
| **endpoint** | one method and path of the gateway's HTTP API; [every-door.md](protocol/every-door.md) lists them all |
| **entry** | one line of a call's log: `seq`, `ts`, `type`, `data`. Everything the runtime knows about a call is a fold over its entries |
| **ephemeral entry** | an entry sent to live readers and never stored (`agent.transcript`, `participant.speaking`) |
| **seal** | closing a call's log at hang-up: the summary, price and verdicts are written and no entry may follow |
| **lend** | the deployment running a stage of a call on its own vendor key for an org that brought none; the `lends` quota says which vendors. What was lent is the operator's bill |
| **BYOK** | bring your own key: the org's own vendor credential, sealed in the vault; runs any installed vendor with no gate of ours |
| **admission** | what a new org is allowed in each environment (quotas, lends, one trial per person): the `admission` row of `box_settings`, edited from the console |
| **fence** | the carrier networks a number may be called from; one inbound SIP trunk per fence |
| **carrier catalog** | the carriers a deployment knows (`channels/telephony/carriers.csv`), each with its published SIP signalling networks; the operator admits some, and only those are offered to orgs and opened in the fence |
| **hooked number** | a number the org points at the deployment itself, from its own carrier, admitted by its networks; as opposed to one imported from an account the deployment can configure |
| **app socket** | the WebSocket a tenant's agent process holds open on `/v1/apps`; tool calls cross it |
| **signal** | what the gateway processes tell each other as it happens: an entry just written, who holds an agent. Lossy on purpose and never a record, since every reader resumes from the store; on Redis when `PINECALL_REDIS_URL` is set, inside the one process when it is not (`process/signal.py`) |
| **desk** | a supervisor's place in a live call: listening in, whispering to the agent, taking the line, ending the call (`gateway/api/desk.py`) |
| **line** | a developer's terminal holding an agent: a call to a production number from one of their own phones lands there (`gateway/api/line.py`) |
| **relay** | the console asking a running app to do something — chat, render the view, run an eval — forwarded over the app socket and answered (`gateway/api/relay.py`) |
| **visitor** | somebody in a browser: the room token that lets them into a call, and the four-digit code a caller keys to tie a page to a phone call (`gateway/api/visitors.py`) |
| **thread** | one WhatsApp conversation with one contact: the gateway keeps it open (`gateway/calls/threads.py`) and the org reads it (`gateway/api/threads.py`) |
| **keyring** | the vendor credentials a call may run on: the org's own, the deployment's, and what the deployment lends (`providers/credentials.py`) |
| **golden** | a scripted conversation an agent is tested on: the state it opens in, the caller's lines, the facts injected, what is expected (`docs/protocol/evals.md`) |
| **persona** | a synthetic caller of one agent that a model plays one line at a time |
| **judge** | one question about a finished call, settled by code or by the judge model: `held`, `broken`, `deferred` or `skipped`; the platform's own, or one an org writes for itself or for one agent |
| **run** | every golden of a suite under every model named, through the app that holds the agent, judged into a matrix |
| **hosted app** | an org's agent process that the deployment runs itself, from sources the org uploaded, on a server's token minted for it (`tenancy/hosting.py`, `docs/protocol/hosting.md`) |
| **release** | one upload of a hosted app's sources, numbered from 1 and never edited |
| **secret** | a value of the org's, per environment, that its hosted apps are started with as an environment variable; kept sealed and never read back by an endpoint (`tenancy/org_secrets.py`) |
| **runner** | the deployment's own process that builds and runs every org's hosted apps in one environment, on a key that opens `runner` and nothing of an org's (`gateway/api/runner.py`) |
| **host** | the name a hosted app's process runs under, one release under one set of the org's secrets; the app socket says it when it registers, which is how the gateway knows a release is serving |
| **time served** | how long a hosted app's process ran under one of its hosts, counted by the runner's beats per UTC day (`hosted_usage`): what hosting is billed on |
