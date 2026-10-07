# The directory verbs — `POST /v1/agents/{slug}/dev/{family}/{verb}`

Some of what a console does needs the **agent's directory**, not the gateway: mount the class for
a written call, read the goldens, push the docs, run the memory goldens, write a candidate, read a
reproduction, render the panel an agent draws beside a conversation where the tenant's own systems
are reachable. So the console asks the gateway, and the gateway asks the process standing there,
the `pinecall start` holding the agent, over the app socket it already has open:

```
console ── POST /v1/agents/clinica-norte/dev/evals/goldens.run ──▶ gateway
gateway ── dev.request {id, verb, data} ──▶ the socket that answers the console for clinica-norte
gateway ◀── dev.answer {id, result | refused} ── the app
console ◀── 200 result, or the refusal's status and sentence, verbatim
```

The request is never stored: it goes down the socket as an ephemeral entry with `seq: 0`. The
answer is the verb's own shape, which belongs to the CLI that answers it; the wire closes the verb
(`DevVerb`) and the envelope.

| family | scope | verbs |
|---|---|---|
| `chat` | `talk` | `chat.roster` · `chat.start` · `chat.say` · `chat.end` |
| `knowledge` | `knowledge` | `knowledge.roster` · `knowledge.push` · `knowledge.eval` |
| `memory` | `memory` | `memory.roster` · `memory.eval` · `memory.extraction` |
| `view` | `calls` | `view.render` |
| `evals` | `evals` | `simulate.start` · `goldens.roster` · `goldens.run` · `promote.roster` · `promote.write` · `drift.read` · `reproductions.roster` · `reproductions.read` |

A verb asked under the wrong family is `404`; an app that says nothing for two minutes is `504`.

## Which process answers

`pinecall start` holds two sockets for an agent: the process serving the class, which takes the
calls, and beside it a companion that registers with `answers_dev: true` and
`takes_unclaimed: false`, which takes no call and answers the console. The gateway sends each verb
to one of them:

| the ask | goes to |
|---|---|
| `?app=<id>` | that socket, whatever the family; `409` when it does not hold the agent |
| `view.render` | the socket serving the call: the class draws the panel. The newest that takes unclaimed calls, in the key's scope, else the org's own |
| every other verb | the newest socket that answers the console and is not draining, in the key's scope, else the org's own |

Nobody holding the agent is `404`. An agent held only by processes that serve calls — an SDK's own
process, with no `pinecall start` beside it — is `409` with `agent <slug> is held, but nothing
answers the console for it: run `pinecall start` in its directory`; a `view.render` with nothing
taking unclaimed calls is `409` with the sentence that names `pinecall start` too.
