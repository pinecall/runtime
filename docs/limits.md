# Limits: what an org may use, and whose keys it runs on

## Quotas, per world

An org has one set of limits in production and another in the sandbox: minutes, messages,
LLM tokens, calls at once, agents, remembered facts, knowledge chunks, bought numbers and seats.
A limit nobody set is no limit; zero is a limit that refuses everything, which is how a plan
leaves a feature out. The set is replaced whole, so a limit left out stops being one.

What an org used is counted from its calls' summaries in that world, every time a call or a
turn asks to start: nothing is kept in memory, so a restart counts what the database holds.
What the sandbox spent never closes production.

- **A call** is refused past `concurrent_calls`, `minutes`, `messages` or `llm_tokens`. One that
  is let in is told how many seconds are left of the minutes, and ends there.
- **A written turn** is held to `messages` and `llm_tokens` on every turn, with what the open
  conversation has spent so far added to the org's totals.
- **Agents, numbers, facts, seats and chunks** are refused at their limit; a push that would pass
  `knowledge_chunks` is refused whole.

A refusal is a `429` whose sentence names the quota, what was used and the limit
(`the org has used 30 of its 30 minutes in the sandbox`), and `credits.exhausted` lands in the
agent's log with the same numbers.

## What a new org is given

What an org gets when it is made is the box's `admission` setting, edited from the console: the
limits in each world for a person's first org, and, when the box gives one trial per person, the
limits for any later org of the same address. A box with no such setting gives a new org no
limits, which is what a self-hosted box runs.

```json
{
  "first": {
    "sandbox":    {"minutes": 30, "messages": 300, "llm_tokens": 2000000, "concurrent_calls": 1,
                   "lends": ["deepgram", "cartesia", "anthropic/claude-haiku-4-5"]},
    "production": {"minutes": 0, "messages": 0, "llm_tokens": 0, "lends": []}
  },
  "later": {
    "sandbox":    {"minutes": 0, "messages": 0, "llm_tokens": 0, "lends": []},
    "production": {"minutes": 0, "messages": 0, "llm_tokens": 0, "lends": []}
  }
}
```

## Whose vendor keys a call runs on

An org's own key for a vendor runs any model of that vendor, whatever the box offers:

```bash
pinecall providers add deepgram        # the org's own key, sealed; every Deepgram model is theirs to run
```

Where an org brought no key, its calls run on the box's, and only on what the org's `lends`
says in that world: **null** lends every key the box holds, an **empty list** lends none, and
otherwise each entry is a vendor (`deepgram`) or a vendor and a model prefix
(`anthropic/claude-haiku-4-5`). A vendor nobody holds a key for is refused before the call
opens.
