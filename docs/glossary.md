# Glossary — the words of the domain

| word | meaning |
|---|---|
| **world** | production or sandbox. A property of a row (`env`) and of a key's prefix (`pc_live_`, `pc_test_`), never of a deployment: one gateway and one database serve both, each at a name of its own (`PINECALL_DOMAIN`, `PINECALL_SANDBOX_DOMAIN`), so the address says the world |
| **fleet** | the set of workers that answer one world's calls, registered under one LiveKit agent name (`PINECALL_FLEET`). The isolation between worlds is that a test call is only ever dispatched to the sandbox fleet |
| **scope** | the triple `(org, env, holder)` a request acts in: whose data, which world, and which app socket holds the agent. Every door resolves it from the key and the `pinecall-env` header |
| **door** | one method and path of the gateway's HTTP API. Every door of v1 exists in v2 under the same path and shape |
| **entry** | one line of a call's log: `seq`, `ts`, `type`, `data`. Everything the runtime knows about a call is a fold over its entries |
| **ephemeral entry** | an entry sent to live readers and never stored (`agent.transcript`, `participant.speaking`) |
| **seal** | closing a call's log at hang-up: the summary, price and verdicts are written and no entry may follow |
| **lend** | the box running a stage of a call on its own vendor key for an org that brought none; the `lends` quota says which vendors. What was lent is the operator's bill |
| **BYOK** | bring your own key: the org's own vendor credential, sealed in the vault; runs any installed vendor with no gate of ours |
| **admission** | what a newborn org is allowed in each world (quotas, lends, one trial per person): the `admission` row of `box_settings`, edited from the console |
| **fence** | the set of carrier networks a number may be called from; one inbound SIP trunk per fence |
| **hooked number** | a number the org points at the box itself, from its own carrier, admitted by its networks; as opposed to one imported from an account the box can configure |
| **app socket** | the WebSocket a tenant's agent process holds open on `/v1/agents/{slug}/app`; tool calls cross it |
| **the box** | one machine running the gateway, the workers, LiveKit, SIP, Redis and Postgres, from `infra/box/` |
| **desk** | the supervisor's seat in a live call: listening in, whispering to the agent, taking the line, ending the call (`gateway/api/desk.py`) |
| **line** | a developer's terminal holding an agent: the ring at a production number lands there, and the phones they call from route to their copy (`gateway/api/line.py`) |
| **relay** | the console asking a running app to do something, chat, render the view, run an eval, forwarded over the app socket and answered (`gateway/api/relay.py`) |
| **visitor** | somebody in a browser: the room token that lets them into a call, and the four-digit code a caller keys to tie a page to a phone call (`gateway/api/visitors.py`) |
| **thread** | one WhatsApp conversation with one contact: the gateway keeps it open (`gateway/_threads.py`) and the org reads it (`gateway/api/threads.py`) |
| **keyring** | the vendor credentials a call may run on: the org's own, the box's, and what the box lends (`providers/credentials.py`) |
| **golden** | a scripted conversation an agent is tested on: the state it opens in, the caller's lines, the facts injected, what is expected (`docs/protocol/evals.md`) |
| **persona** | a synthetic caller of one agent that a model plays one line at a time |
| **judge** | one question about a finished call, settled by code or by the judge model: `held`, `broken`, `deferred` or `skipped`; the runtime's panel, or one an org writes for an agent |
| **run** | every golden of a suite under every model named, through the app that holds the agent, judged into a matrix |
| **ring** | how far a test goes: goldens (1), a persona on a line (2), a finished call checked by code (3), every call judged at hang-up (4) |
