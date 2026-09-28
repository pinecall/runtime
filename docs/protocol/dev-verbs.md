# The directory verbs — `POST /v1/agents/{slug}/dev/{family}/{verb}`

Some of what a console does needs the **agent's directory**, not the gateway: mount the class for
a written call, read the goldens, push the docs, run the memory goldens, write a candidate, read a
reproduction, render the panel an agent draws beside a conversation where the tenant's own systems
are reachable. So the console asks the gateway, and the gateway asks the process standing there,
the `pinecall start` holding the agent, over the app socket it already has open:

```
console ── POST /v1/agents/clinica-norte/dev/evals/goldens.run ──▶ gateway
gateway ── dev.request {id, verb, data} ──▶ the app socket holding clinica-norte
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

A verb asked under the wrong family is `404`. The process that answers is the newest socket holding
the agent in the key's world that takes unclaimed calls, unless `?app=` names one. Nobody holding
the agent is `404`; an app that says nothing for two minutes is `504`.
