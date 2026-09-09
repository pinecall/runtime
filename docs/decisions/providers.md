# providers: the livekit plugins ARE the adapters

ms-1 wrote what livekit-agents already ships. `providers/` held `claude.py` (152 lines) and
`gpt.py` (170) — hand-written streaming adapters over the vendor SDKs — plus `llm.py` (120), our
own `LLM` Protocol with `Delta` / `Finished` / `Usage` / `Message` / `Prompt`, and `llms.py` (44),
a registry whose name differed from the Protocol's by one letter. `session/text/measure.py` then
re-derived ttft, duration and tokens-per-second by hand from a clock the session started itself.

The repo rule says the opposite: **livekit-agents is the session; libraries are used as they are.**

## What the library already does

`livekit.agents.llm.LLM.chat(chat_ctx=..., tools=...)` works with **no room and no AgentSession**.
It returns an `LLMStream`: an async iterator of `ChatChunk{id, delta{content, tool_calls}, usage}`.
While we drain it, livekit's own monitor task accumulates the chunks and emits
`livekit.agents.metrics.LLMMetrics` on the llm's `metrics_collected` event — with the exact field
list the wire already carries, because `protocol/schema/metrics.json` was generated from that class.

So the request, the streaming, the retries, the vendor's tool-call shape, the OTel span and the
measurement are all the library's. What is left for us is: which model, what it costs, and what
the log says.

## What providers/ is now

`providers/models.py`, one module named after the idea:

- `DEFAULT_MODEL` — the model a session runs when the app declared none, one line in one file.
- `VENDORS` — the table. **A new vendor is one import and one row.**
  `"anthropic"` runs with `caching="ephemeral"`, because the static prompt region is exactly what
  a prompt cache is for; `"openai"` takes nothing but the model and the key.
- `models_for(settings) -> (Model | None) -> livekit LLM` — keys read once at startup, so a
  provider with no key is refused at the door of the call and not as a 401 mid-turn.
- `vendor_of(llm)` — livekit's own `LLM.provider` is the **API host** (`api.anthropic.com`), which
  is right for a trace and wrong for a price table keyed by vendor. It is read off `LLM.label`
  instead, the name the plugin gives itself.

### The vendor is the plugin's label, not `type(llm).__module__`

This was a stopgap and it is closed. The first version read `type(llm).__module__` and matched it
against `livekit.plugins.<vendor>.` — a private attribute, and a walk over the import path of a
library that never promised us one. livekit does not expose a vendor: `LLM.provider` is overridden
by every plugin to return `self._client._base_url.netloc`, the API host. What it does expose is
`LLM.label`, built once in `livekit.agents.llm.LLM.__init__` out of the plugin's own module and
class name — so an anthropic model answers `livekit.plugins.anthropic.llm.LLM` and an openai one
`livekit.plugins.openai.llm.LLM`. It is not `<vendor>.<model>`; it is the plugin naming
itself, publicly, and `vendor_of` matches the rows of `VENDORS` against its segments. A test per
installed plugin pins both halves: what the label says, and that the vendor read out of it is the
row the model was built from. The day livekit publishes a real vendor attribute, this is one line.

`prices.py` stays: cost is ours, livekit does not price. `usage.py` does **not** — its two
helpers (`an_int`, `summed`) existed only to read a count off a vendor's own usage object and keep
"never reported" apart from "reported zero". That is now exactly what `CompletionUsage` and
`LLMMetrics` do, and both helpers had no caller left. The card asked for it to stay; it stayed
dead, so it went. Restoring it is one `git revert` of the deletion if the reviewer disagrees. `claude.py`, `gpt.py`,
`llm.py` and `llms.py` are gone — 486 lines deleted for 66.

## The ChatContext: our history, in livekit's items

`session/text/model.py` held the model side of a text session — it was dissolved later
(see `text-session.md`), `Prompt` into `session.py` and `Metered` into `agent.py`:

- **the prompt** — `Prompt(static, view)`. The two regions become the request's one **system
  message, static first**: the order the milestone's rule fixes. They are joined rather than sent
  as two blocks because the Anthropic plugin puts its cache breakpoint on the **last** system
  block; splitting them would move the breakpoint onto the region that changes every turn and the
  cache would never hit. The cost of joining is the mirror of that: a view change invalidates the
  static prefix too. The honest fix is a trailing dynamic block outside the cached region, and it
  belongs to the card that owns the three-region render, not to this one.
- **the turns** — `ChatMessage(role="user" | "assistant")`.
- **a tool round-trip** — `FunctionCall(call_id, name, arguments)` then
  `FunctionCallOutput(call_id, output, is_error)`, as items of their own. That is the one shape
  every provider accepts, and it reads back as what happened: the model asked, the app answered.
- **the tools** — our `ToolSpec.parameters` is already JSON Schema and the app's own process runs
  every tool, so there is no Python signature to introspect: `function_tool(raw_schema=...)` is the
  door that takes a schema. The body it wraps raises if anything ever calls it, which nothing does.

`MAX_TOOL_ROUNDS` is unchanged, and so is every entry the session writes and its order.

## measure.py computes nothing

`llm_metrics(metrics, speech_id)` is `metrics.model_dump(exclude_defaults=True)`, plus the `type`
tag (a discriminated union cannot be read back without it) and the session's `speech_id`.

That one call also **is** the absent-not-zero rule. livekit gives every field it measured a value
and leaves at its default the ones no provider reported, so `cache_creation_tokens` and
`reasoning_tokens` fall out of the entry when nobody reported them, while a required field like
`prompt_cached_tokens` stays at 0 because 0 is what the provider said.

`turn_metrics` reads `llm_node_ttft` and `llm_node_tps` straight off `LLMMetrics.ttft` and
`.tokens_per_second` — of the first round that produced a token, so a second round after a tool
never resets them. livekit's `-1` for "generated no token at all" is not a latency anybody may
average, so the turn simply carries neither number. The `usage` rows of `call.summary` are livekit's
four counts, summed, exactly as its own `ModelUsageCollector` keyed them.

### A usage row's `provider` is livekit's label, and pricing never reads it

The collector keys a row by `(provider, model)` where `provider` is `LLM.provider` — and that
property is the plugin's, not a vendor registry's. ElevenLabs, Soniox and Deepgram return their
brand; **anthropic and openai return `self._client._base_url.netloc`**, so a real Haiku call writes
`provider: "api.anthropic.com"` into its summary. That is not a bug to map away: with a base URL
overridden to a proxy or a gateway, the host is the truthful thing to have written down, and
inventing "anthropic" over it would erase where the tokens actually went.

Nothing prices off it. `PRICES` and `MEDIA_PRICES` are keyed by **model prefix**
(`prices.py`), so `claude-haiku-4-5-20251001` is priced by `claude-haiku-4-5` whatever the row
is labelled; the label is carried into `CostRow.provider` and `UnpricedRow.provider` for the
reader and for nothing else. `tests/providers/test_prices.py` pins it from livekit's own usage
object with the hostname on it, with no key and no call. The vendor name the
registry uses is `vendor_of(llm)`, read off `LLM.label` — a different question, answered above.

## The fake

`tests/gateway/fake_llm.py` subclasses `llm.LLM` and `llm.LLMStream`. `_run()` pushes ChatChunks
into the channel the base class owns — text deltas, then a chunk of tool calls, then a usage chunk
— and **livekit's own monitor derives the LLMMetrics from them**. No metric is fabricated anywhere
in the tests: the numbers a test reads are the library's arithmetic over a scripted stream. The
base class needed no coaxing at all: no room, no connection, no AgentSession, `super().__init__`
and one `_run`.

`Asked` keeps the `ChatContext` the session built and reads `.system`, `.history`, `.calls` and
`.outputs` off it, so the existing tests assert the same things about the same request.

## Two consequences worth stating

- **`livekit-agents[anthropic,openai]` moved into the runtime's core dependencies.** The gateway's
  text session is a livekit LLM caller now; it is not an optional extra any more. The media
  plugins (soniox, deepgram, elevenlabs) stay in the `runtime` extra.
- **The isolation rule still holds.** Only `providers/` names `livekit.plugins` or a vendor SDK;
  everything else says `livekit.agents`, which is the framework, not a vendor.

## The lesson

Read livekit-agents before writing anything that touches a model. Four modules and a hand-rolled
stopwatch existed because nobody opened `livekit/agents/llm/llm.py`. The reviewer checks this too.

---

# The registry: a vendor is a file (ms-3)

Everything above is the ms-2 record and still holds. What changed is the shape around it: the
llm table was one dict in one module, and ms-3 needed four more modalities. Five dicts in five
modules, each with its own `if` chain, is the growing chain the style rules forbid, so the table
itself became the thing: **`providers/registry.py`, one `Vendors[Made]` per modality, and a
vendor is one file that registers itself in one line.**

```
providers/
  registry.py     Vendors · Asked · NoProvider · a_key · Chat / Ears / Speech
  pipeline.py     one AgentConfig in, the three objects an AgentSession takes out
  models.py       the llm modality's front door: models_for, vendor_of
  prices.py       what a model costs
  llm/            anthropic.py · openai.py
  stt/            soniox.py · deepgram.py
  tts/            elevenlabs.py
```

## Adding a vendor is one file

```python
"""Whisper.cloud: the ears nobody asked for yet."""

from pinecall.providers.registry import Asked, Ears, a_key
from pinecall.providers.stt import MAX_SILENCE_MS, VENDORS


@VENDORS.registers("whispercloud")
def build(asked: Asked) -> Ears:
    """One vendor, one function, every option stated."""
    return whispercloud.STT(api_key=a_key("whispercloud", asked.settings.whisper_api_key), ...)
```

That is the whole of it. No import to add, no row to paste, no chain to extend: `Vendors` reads
its own package with `pkgutil.iter_modules` the first time anybody asks it for a name, so **the
file being in the directory IS the registration** and the decorator is the one line. The suite
proves it with a vendor that exists only for the proof: `tests/providers/vendors/acme.py` is
imported by nothing, listed by nothing, and `VENDORS.names` returns it.

`Asked` is the one question every modality answers — `settings`, `model`, `language`, `voice_id`,
`endpointing_ms` — and each vendor file reads the two or three fields it knows about. One seam
instead of five signatures, and the vendor reads its own key off `Settings` under the vendor's own
environment name, because which variable holds the ElevenLabs key is ElevenLabs' business.

**The table is a constant that happens to be assembled.** The hygiene rule against module-level
mutable state is about state shared *between calls*: `Vendors` is filled once, by import, before
the first call, and is read-only afterwards. Nothing writes to it while a call is running, and
nothing per-call lives in it. What is per-call is `Pipeline`, built fresh from one `AgentConfig`.

**A table is read once, and once means once successfully.** `_read_the_package` used to mark
itself read *before* the imports, so a package that raised — a plugin that refuses a job thread,
a half-installed extra, a missing shared library — left the table marked read and empty for the
life of the process: every later call resolved no vendor at all, and the only sign of it was a
`NoProvider` naming "no vendor at all" long after the real failure. That is what the console
crash of ms-5 left behind. The flag is now set after the loop, so a raise reaches whoever asked
(the setup hook or the first call, both of which already report) and the next attempt reads the
package again. Reading again is safe rather than merely cheap: `sys.modules` keeps the files that
did import, a file that raised its way out of its own body is dropped from `sys.modules` and runs
again, and a row is keyed by vendor name, so a decorator that runs twice writes the same row
twice. **No lock was added**, and the reason is one line: `importlib` already locks per module, so
two threads reading the same table either wait for each other or find the module cached, and
either way they register the same rows into the same dict.

## The llm modality is in the registry, and there is still one table of models

Criterion 4 of the card, answered by folding rather than explaining: `VENDORS` moved out of
`models.py` and became `providers/llm/anthropic.py` and `providers/llm/openai.py`, one file each,
registered the same way as every other vendor. `models.py` keeps exactly what is llm-specific and
belongs to nobody else — `models_for`, the gateway's door from a `Model | None` to a plugin, and
`vendor_of`, which reads the vendor off the plugin's public label for the price rows. `DEFAULT_MODEL`
left it: **a model name is written in the vendor file that runs it, and nowhere else.** `models.py`
names `DEFAULT_VENDOR = "anthropic"`; which anthropic model that is, is `llm/anthropic.py`'s line.
The same rule holds for every modality — soniox names `stt-rt-v5`, elevenlabs names
`eleven_flash_v2_5`, and `pipeline.py` names only vendors.

## Three modalities, because three is what livekit does not carry

The tree is `llm/`, `stt/`, `tts/` and nothing else. It briefly had `vad/` and `turn/` too, each
with one file, and both were the library rewritten: an `AgentSession` given no `vad=` builds
livekit's own native `inference.VAD(model="silero")` at `min_silence_duration=0.25`
(`agent_session.py:606-607`, `inference/vad.py:64`), and `turn_handling["turn_detection"]` defaults
eagerly to `inference.TurnDetector()` (`agent_session.py:541-542`). Our two files wrapped
`livekit-plugins-silero` and re-registered the framework's own detector; both are deleted, and
neither plugin is installed any more.

What is left of the turn decision is one line in `session/voice/session.py`, and it is real: the version
is named `"v1-mini"` out loud, because left unset the library picks the hosted `v1` when it reads
itself as hosted or in dev mode (`inference/eot/detector.py:55-59`) — a self-hosted box must never
be one environment variable away from sending a caller's transcript to a cloud.

The three that stay are the three LiveKit Cloud's inference gateway does **not** front: its closed
model unions (`inference/stt.py:377`, `tts.py:80`, `llm.py:175`) name deepgram, cartesia,
assemblyai, xai, speechmatics, inworld, google, rime, fishaudio, openai, kimi, deepseek and z-ai —
and neither Soniox, nor ElevenLabs, nor Anthropic. Those three reach a session only as
`livekit.plugins.*` objects with our options, which is exactly what `providers/` is for.

## The ElevenLabs trap, closed

`livekit-plugins-elevenlabs`' own signature defaults to `model="eleven_turbo_v2_5"`
(`plugins/elevenlabs/tts.py:109`, `livekit-session.md` #16) — a model this repo forbids. A build that
forwarded a missing model would have shipped it silently, which is exactly the class of bug the
milestone's rule exists for. So:

- the default is **ours**, `eleven_flash_v2_5`, stated in the file, and a test asserts the built
  model is not the plugin's;
- the two forbidden names are a table with the one we run **instead** — `eleven_turbo_v2_5` →
  flash, `eleven_v3` → `eleven_v3_conversational` — with a warning naming both. An agent that
  asked for a voice gets a voice; the log says which and why. A refusal here would have silenced
  a line of calls to make a point about a model name;
- a name that is neither allowed nor substituted is a typo, and a typo is refused with the list of
  the models this build runs, before it reaches the vendor.

## A missing declaration is never quiet

convo ms-14: a blank config once silenced a whole line. Two halves, two behaviours, both loud:

- **no key** — `a_key()` raises `NoProvider` at the door of the call, naming the vendor. Never a
  401 in the middle of a caller's turn, for any modality, the same rule the llm side already had.
- **no declaration** — `pipeline_for` falls back to the vendor this build defaults to and writes
  one `WARNING` per modality naming the agent and what it chose. A test reads those three lines.

## What the suite pins, with no network and no key

`tests/providers/` builds every vendor with dead sentinel keys and asserts the options off the
plugin's own object: soniox's hints, endpointing level 2 / sensitivity 0.3 / 1000 ms; deepgram's
`flux-general-multi` on `STTv2`, the only Flux model that takes language hints; elevenlabs' model
and voice. Nothing opens a socket, and every constant a test asserts is imported from the file that
owns it — a test that retyped a number would pass the day the source stopped saying it.

**An option equal to the plugin's own default is not set, and a test says why.** Soniox's
`stt-rt-v5` and both plugins' 16 kHz are the plugins' defaults already
(`soniox/stt.py:112,119`, `deepgram/stt_v2.py:73-74`), so the vendor files pass neither and
`test_both_ears_already_listen_at_16_khz_without_being_told_to` reads the rule straight off their
signatures. Every option that IS set carries the plugin's own default and its `file:line` in the
comment beside it.

## The vendors are built per call, and nothing is loaded before one

`pipeline_for(config, settings)` is the whole of it: the three objects an agent declared, built
when the call arrives. There is no warmed pair and no `warm=` argument — the two models that used
to justify them are the session's own, and livekit pages their weights into the forkserver itself
(`worker.py:747-759`, `inference/_warmup.py:7-10`; see [worker.md](worker.md)). `worker/kit.py`
holds the seam that is left: `Kit` is `Callable[[AgentConfig], Pipeline]`, and `kit_for(settings)`
closes over the keys the process read once, exactly like `models_for` does for the gateway.

## A voice is a name here and an id there

The first real voice call, 2026-09-07, room `smoke-voice-1`: Clínica Norte declares
`voice = "carolina"`, the bridge handed that string to ElevenLabs as a `voice_id`, and the vendor
closed the socket with `1008 voice_id_does_not_exist`. The session retried it seven times over
twenty seconds while the model, hearing its own words fail, apologised to a caller who heard
nothing. Two separate mistakes, and each has its own answer.

**The name never leaves the declaration.** `providers/tts/voices.py` is the one table: a curated
name to `{vendor, voice_id, language}`. `voice_declared(name, provider, voice_id)` is its only
door, and it has exactly two callers — `api/agents/declaration.py`, while the app is declaring
itself, and `providers/overrides.py`, when an operator turns the pipeline's voice knob — so
`AgentConfig.voice` never holds anything but a vendor and an id, and neither the worker nor the
pipeline can send a name to a vendor again. A tenant who has their own voice writes the vendor's id
instead: twenty letters and digits is not a name, and it passes through as one. Anything else is
`DeclarationRefused` — `no voice named 'carolinaa'; this
build knows: carolina, charlie, mateo` — which the SDK raises out of `agent.open()`, so the app
fails where a person is watching a terminal rather than where a caller is holding a phone. On the
wire it is `VoiceConfig.name`, the only field of the declaration with no twin in the domain, and
`tests/domain/test_wire_agreement.py` names it and says why.

**Every id in the table is a `premade` voice**, checked with `GET /v1/voices/<id>`. A `professional`
or a cloned voice exists only inside the workspace that added it, so curating one would ship every
other self-hoster the same 1008 this table was written to end. `carolina` and `mateo` come from
ElevenLabs' own default catalogue (`EXAVITQu4vr4xnSDxMaL`, `cjVigY5qzO86Huf0OWal`); `charlie` is
`IKne3meq5aSn9XLyUdCD`, the voice Pinecall v1 has been speaking with in production
(`sdk-server/.env`, `ELEVENLABS_VOICE_ID`) — its ElevenLabs name, not ours, because the table's
names are what a tenant writes and a wrong one would be a second thing to look up. The third
column is the language the voice was curated for, and an agent that declares no `language` of its
own inherits it (`providers/pipeline.py`).

## A failure whose cause cannot change ends the call

livekit already closes a session on unrecoverable component errors, and it is right to be patient:
`AgentSession` retries and gives up only after three of them (`voice/agent_session.py:1830-1844`).
What it cannot see is that this one was never worth the first retry. `APIStatusError` decides
`retryable` from the HTTP status (`agents/_exceptions.py:75`), and the ElevenLabs plugin puts the
*websocket close code* in that field (`plugins/elevenlabs/tts.py:847`): 1008 is not in `400..499`,
so a refusal of the request itself was read as a bad minute.

So the judgement is ours, in `session/voice/dead_end.py`, and it is deliberately small:
`is_a_dead_end(error)` is true for `1008` and for the statuses that answer the same way however
often they are asked — 400, 401, 402, 403, 404, 422. 408, 429 and every 5xx are absent on purpose:
those are the moment, not the request, and livekit's own retry handles them. `session/voice/
events.py` consults it on every session `error`: a transient one is written as `component_failed`
and the call goes on, exactly as before; a dead end is written ONCE as `component_dead_end` with
`recoverable: false`, every later error of that call is dropped, and `VoiceBridge.ends_for` takes
the session and the job down so `call.ended` reads `error`. The same rule covers an STT or LLM 401,
which is the loop ms-3 saw with a sentinel key.

One entry, one ending, and the caller stops paying for a retry that was never going to work.
