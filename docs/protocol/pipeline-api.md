# An agent's pipeline — `/v1/agents/{slug}/pipeline`

Beside [operator-api.md](operator-api.md) because they are the same public contract — everything
needed to self-host is here — but they are **not** the operator API and they take a different key:
the org's own API key, as `Authorization: Bearer <key>`, on the one bearer parser every other door
uses. The ops key does not open them. They belong to the AGENT, so a tenant's own dashboard opens
them as readily as ours would — any UI is one client of them; the *pipeline* decision page in the maintainer's notebook argues
the shape.

### `GET /v1/agents/{slug}/pipeline`

What one agent hears, decides and speaks with right now, what those three have cost it, and what an
operator has already turned. `404` when no app is holding the agent, in the same words the worker's
config door answers with — a pipeline is a live thing and there is no empty one to show.

```json
{ "agent": "clinica-norte",
  "hears":   { "vendor": "soniox", "model": null, "voice_id": null, "language": "es" },
  "decides": { "vendor": "anthropic", "model": "claude-haiku-4-5", "voice_id": null, "language": null },
  "speaks":  { "vendor": "elevenlabs", "model": null, "voice_id": "a-declared-voice", "language": "es" },
  "greeting": { "say": "Clínica Norte, buenas.", "reply": null, "allow_interruptions": null },
  "overrides": { "voice": null, "tts": null, "tts_model": null, "stt": null, "llm": null, "greeting": null },
  "voices": [ "…" ],
  "providers": [ … ],
  "calls": 12,
  "medians": [ { "name": "transcription_delay", "seconds": 0.13, "turns": 41 },
               { "name": "e2e_latency", "seconds": 0.94, "turns": 39 } ],
  "unavailable_reasons": { "speaks": "elevenlabs has no API key in this process" } }
```

Each stage names the vendor a session **would** be built with, which is what the agent declared or
this build's default for that modality — never a guess. `greeting` is the opening in the wire's
`GreetingConfig` shape, turned or declared, or null. `voices` are the names the voice knob may be
turned to, and `providers` every vendor each stage could be turned onto, the same rows
`GET /v1/providers` answers. `medians` is `log/latencies.py` over every
turn of the agent's last calls together: livekit's own field names, in the order a turn happens,
the median and not the mean, and a measure nobody measured has no row rather than a zero. `calls`
is how many logs it read. `unavailable_reasons` names a stage whose vendor has no API key in this
process, so a screen can say so before the line goes dead instead of after.

### `PUT /v1/agents/{slug}/pipeline/overrides`

Turn one or more of the six knobs. They are applied on the agent's **next session**, through
`GET /v1/agents/{slug}/config` — the one door an agent's config has ever reached a worker by — so
nothing is restarted and nothing is deployed.

```json
{ "voice": "a-voice-an-operator-chose", "llm": "anthropic/claude-sonnet-4-5" }
```

`voice` is the voice that speaks · `tts` the vendor that speaks it · `tts_model` the model it speaks
with, which wins over a model named in `tts` · `tts`, `stt` and `llm` take `vendor/model`, a vendor
alone to keep its own default model, or a model alone to keep whichever vendor is already in use ·
`greeting` the words said first on the next call.

The body is the **whole** set: a knob left out stops being overridden and goes back to what the app
declared. A knob that is present but **blank is refused** with `400` and the sentence that says
why — an empty voice once reached the vendor and a whole line of calls went out silent. A `tts`
model this build will not run is refused too, in the words that name the one it runs instead, and
so is a vendor this build has no file for.

The answer is the same report `GET` gives, so a screen redraws from what the gateway now holds.

## The hold melody

While a tool runs, the caller hears a melody instead of silence — on the phone and on the web
alike, because it is a second audio track in the room, which the widget attaches and the SIP bridge
mixes into the phone leg. It starts once a tool has been running for 0.6 s, so a tool that answers
at once plays nothing, loops under the wait at 60 % volume with a short fade in and out, and stops
before the tool's confirm read-back is said. Tools running side by side play it once. A written
(`chat`) call has no audio and plays nothing. Every agent plays **"A New Life"**, the runtime's own,
until it is told otherwise here; these doors are their own and not a knob of `…/overrides`, so a
console that PUTs the six knobs never moves the melody.

| door | what |
|---|---|
| `GET /v1/agents/{slug}/pipeline/hold-audio` | `{played, name, seconds, sha256}`, where `played` is `default`, `off` or `custom` |
| `GET …/pipeline/hold-audio/audio` | the file that plays, `audio/ogg` (Opus, 48 kHz mono); `404` when `off` |
| `PUT …/pipeline/hold-audio` | the body **is** the file — wav, mp3, ogg, m4a, whatever PyAV decodes — and `?name=` names it. Converted once, here, to Opus 48 kHz mono and kept in Postgres (`hold_audio`, 0036); answers the `GET` shape |
| `PUT …/pipeline/hold-audio/played` | `{"played": "default"}` gives the runtime's melody back, `{"played": "off"}` plays nothing; an uploaded clip is forgotten either way |

An upload over 20 MB is `413`; a file that is no audio, shorter than a second or longer than five
minutes is `400` with the sentence that says which. The choice applies from the **next call**: the
worker asks `GET /v1/agents/{slug}/hold-audio` (the fleet's key, the call's corner) while it reads
the config, and fetches a clip once per box by its `sha256`, keeping it on disk after that. A
gateway that cannot answer leaves the call with the default melody, never with a broken one.
