# The pipeline door, and the knobs on it

> The SCREEN this page was written for was the console's, and the console was deleted on
> 2026-09-08 (the agents repo's `docs/decisions/console.md`). The DOOR is the runtime's and is untouched, and every
> rule below about what it answers, what it refuses and how its numbers are computed still holds
> for whatever reads it next. Read "the screen" here as "a client of the door".

The pipeline door answers one question — *what is this agent actually running on, and what does
that cost a caller* — and then lets an operator change it without a deploy. Two doors, and every
number they carry read off the same rule the CLI prints from.

## The door belongs to the agent, and a UI is one client of it

For one milestone this door carried the console's own name, and that said the wrong thing: a
tenant changing their own voice from their own dashboard had to knock at a door named after OUR
console. What it answers is a property of the agent — what it hears, decides and speaks with —
so it is `/v1/agents/{slug}/pipeline`, beside `/calls`, `/sessions` and `/config`, and it spells
its parameter `{slug}` like the rest of that family. A UI is one client of it; the tenant's web
app is the next. It was renamed, not aliased: nothing outside this repo had ever called the old
path, and a fallback kept "just in case" is a second door to maintain forever.

## An override travels the path a declaration already travels

There is no second channel. `GET /v1/agents/{slug}/config` is the one hop an agent's config has
ever taken to a worker (`worker/client.py` validates the same `AgentConfig` back through the same
`TypeAdapter`), and an operator's knobs are laid onto that answer inside that door:

```python
return CONFIG.dump_python(overrides.config_for(slug, held.config), mode="json")
```

That is the whole mechanism. A worker asks before every job, so a knob turned now is on the next
session and on nothing that is already speaking — which is exactly what "no deploy" has to mean
when a call is in progress. Nothing is restarted and nothing is redeployed, the same promise the
routes table makes in `docs/decisions/routes.md`.

`config_for()` is that one applying function, and it is called wherever a config is read to build
a session — whichever door the session came through. A text call arrives on `WS /v1/chat` and never
asks the worker's door at all, so for one milestone it read `held.config` raw and an operator's
knob was on every voice call and on no text one (found driving the console against a
real gateway). The rule that replaces it has no exceptions to remember: the held config is what the
app declared, and nothing builds a session from it without asking `Overrides` first.

The overrides live in one `Overrides` object on `app.state`, opened by the gateway's lifespan
beside the registry and the routes. Per process, never at module level: one gateway serves many
agents at once and a dict shared between two of them is the bug the hygiene list was written for.
They are not durable — an app socket that reconnects re-declares, and a box that restarts is back
to what the app says. That is honest for ms-5: the screen is a knob an operator turns while
listening, not a configuration store. The day it must survive a restart it becomes a table with a
migration, and `Overrides` is the one class that changes.

## A blank value is refused, and a knob is given back by leaving it out

convo ms-14 cost a line of calls: an empty voice field reached the vendor, because `""` is a value
and `None` is not, and the agent went out silent. So `PUT /v1/agents/{slug}/pipeline/overrides`
takes the **whole** set of knobs, and:

- a field that is present but blank is a `400` with the sentence that says why — `BLANK`;
- a field **left out** of the body is not overridden at all, which is how an operator gives a knob
  back to the app. There is no blank that means "unset", because that is the same keystroke as the
  mistake.

A `PUT` and not a `PATCH` for that reason: the body is the state of the form, whole.

## A forbidden model is refused here and substituted there, on purpose

`providers/tts/elevenlabs.py` maps `eleven_turbo_v2_5` and `eleven_v3` to what this build speaks
with instead, warns the log, and runs the call. That is right for an **app's** declaration: an
agent that asked for a voice gets a voice, and the log says which. It is wrong for a **person** who
has just typed a model into a form and is about to believe it, so the door refuses instead, in the
words that name the substitute.

The list is never restated. `overrides.py` asks `a_model()` — the provider file's own function over
its own `INSTEAD` and `ALLOWED` — and turns its answer into a refusal. A vendor this build has no
file for is refused the same way, over `Vendors.names`, so a new vendor file changes both sentences
with no edit here.

`stt` and `llm` take `vendor/model`, or a bare model that keeps whichever vendor is already in use.
One separator, `VENDOR_SEPARATOR`, said once.

## The voice knob is a list of names, and it goes through the declaration's own door

The first real voice call closed with `1008 voice_id_does_not_exist` seven times because a name
reached ElevenLabs where an id was expected (`docs/decisions/providers.md`). `voice_declared()`
closed that for an app declaring itself; for one milestone this screen left the other half of the
same door open — a free text box whose string became `Voice.voice_id` unchecked, which is the same
1008 with an operator instead of an app behind it.

So the knob takes the two forms a declaration takes, a curated name or the tenant's own vendor id,
and `overrides.py` resolves it through **the same** `voice_declared()` at `PUT` time. That function
now has exactly two callers and there is no third resolver and no second list: an unknown name is a
`400` carrying the table's own sentence, `no voice named 'carolinaa'; this build knows: …`, and the
declared voice is left standing.

The screen offers those names rather than asking for one. `GET /v1/agents/{slug}/pipeline`
answers `voices` — `voice_names()` off the one table — and the SPEAKS knob is a `<select>` over it,
so a client keeps no voice list of its own and a new curated voice appears on screen with no
edit here. A voice already turned that this build does not curate (a tenant's own id) stays on the
list as its own option: opening the form must never silently change what the agent speaks with.

## The medians are the CLI's medians

`log/latencies.py` is the one median rule in the runtime: five measures in the order a turn
happens, the median and not the mean (one interrupted turn moves an average, and the question is
what a normal turn felt like), and a measure nobody measured has no row rather than a zero. The
pipeline door imports it. So the numbers on this screen, the table `pinecall-runtime sessions show`
ends with, and the medians on the Sessions screen are the same rule read three ways — when two of
them disagree, one is a bug.

They are taken over every turn of the agent's last `LAST_CALLS` calls **together**, not as an
average of per-call medians: a call of two turns must not weigh as much as a call of forty.

## The waterfall names no metric of its own

`MEASURES` ends with the total (`e2e_latency`) and the four before it are its parts, so the
waterfall draws `MEASURES.slice(0, -1)` as bands, offset by everything before each one, and the
total as the rule over them. Whatever `e2e_latency` has left over after the four — `playback_latency`
and anything a turn did not break out — is drawn as **the rest of e2e_latency** and not given a
metric's name: a client draws only the names `@pinecall/protocol` carries, and never invents one
the protocol does not have. The day `playback_latency` joins the
runtime's `MEASURES`, it becomes a band of its own and this paragraph goes.

## What only a human with a phone can tell

The criterion is *changing the voice from the UI is heard on the next call*. Half of that is
provable here and is: a test PUTs a voice and asserts the next `GET /v1/agents/{slug}/config` — the
door the worker itself reads — carries it, and a second test asserts a blank voice is refused with
the sentence and leaves the declared voice standing. What no test in this repo can say is that the
**sound** changed: that ElevenLabs answers for that voice id, that the new voice is the one an
operator meant, and that it arrives without a seam mid-call. That is a person, a phone, and two
calls either side of pressing apply.
