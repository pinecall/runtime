# The directory verbs — `POST /v1/agents/{slug}/dev/{family}/{verb}`

Some of what a console does needs the **agent's directory**, not the gateway: mount the class for a
written call, read `test/personas` or `test/goldens`, push `knowledge/docs`, run `memory/golden.json`
and `test/memory`, write a golden candidate beside the goldens, read the reproduction a broken run
left on that disk. None of those files are on the box. So the console asks the gateway, and the
gateway asks the process that IS standing there — the `pinecall run` holding the agent — over the
app socket it already has open:

```
console ── POST /v1/agents/clinica-norte/dev/evals/goldens.run ──▶ gateway
gateway ── dev.request {id, verb, data} ──▶ the app socket holding clinica-norte
gateway ◀── dev.answer {id, result | refused} ── the app
console ◀── 200 result, or the refusal's status and sentence, verbatim
```

The request is **never stored**: it is a fact about two processes talking, not about the world, so
it goes down the one socket the door chose as an ephemeral entry with `seq: 0`. The answer is the
verb's own shape, which belongs to the CLI that answers it; the protocol closes only the **verb**
(`DevVerb` in `defs.json`) and the envelope (`dev.request`, `dev.answer`).

| family | scope | verbs |
|---|---|---|
| `chat` | `talk` | `chat.roster` · `chat.start` · `chat.say` · `chat.end` |
| `knowledge` | `knowledge` | `knowledge.roster` · `knowledge.push` · `knowledge.eval` |
| `memory` | `memory` | `memory.roster` · `memory.eval` · `memory.extraction` |
| `evals` | `evals` | `simulate.roster` · `simulate.start` · `goldens.roster` · `goldens.run` · `promote.roster` · `promote.write` · `drift.read` · `reproductions.roster` · `reproductions.read` |

The family in the path is the door's scope; the verb is the wire's word, whole. A verb asked under
the wrong family is `404 no dev verb chat.start in knowledge: the verbs are […]`. The body is the
verb's own, passed through as `data`.

**Which process answers.** The one the chat door would give a caller: the newest socket holding the
agent in the key's world that takes unclaimed calls — a `pinecall run`, never a console — unless
`?app=<socket id>` names one, which is how a developer's own terminal is the one that mounts the
class. Nobody holding the agent is `404 no app is holding agent …`; only consoles holding it is
`409 … start `pinecall run``; an app named that is not there is `409 app … is not holding agent …`.

**When it goes wrong.** The app answers a refusal with a status and a sentence — *this console runs
in clinica-norte's directory*, *no golden called X* — and the console gets exactly that. An app that
disconnects before answering is `502`; one that says nothing for two minutes is
`504 the app holding agent … did not answer goldens.run within 120s`.
