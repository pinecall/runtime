# An agent's pipeline — `/v1/agents/{slug}/pipeline`

Beside [operator-api.md](operator-api.md) because they are the same public contract — everything
needed to self-host is here — but they are **not** the operator API and they take a different key:
the org's own API key, as `Authorization: Bearer <key>`, on the one bearer parser every other door
uses. The ops key does not open them. They belong to the AGENT, so a tenant's own dashboard opens
them as readily as ours would — any UI is one client of them; the *pipeline* decision page in the maintainer's notebook argues
the shape.

### `GET /v1/agents/{slug}/pipeline`

What one agent hears, decides and speaks with right now, and what those three have cost it. `404`
when no app is holding the agent, in the same words the worker's config door answers with — a
pipeline is a live thing and there is no empty one to show.

```json
{ "agent": "clinica-norte",
  "hears":   { "vendor": "soniox", "model": null, "voice_id": null, "language": "es" },
  "decides": { "vendor": "anthropic", "model": "claude-haiku-4-5", "voice_id": null, "language": null },
  "speaks":  { "vendor": "elevenlabs", "model": null, "voice_id": "a-declared-voice", "language": "es" },
  "greeting": { "say": "Clínica Norte, buenas.", "reply": null, "allow_interruptions": null },
  "voices": [ "…" ],
  "providers": [ … ],
  "defaults": { "llm": "anthropic", "stt": "soniox", "tts": "elevenlabs" },
  "models": { "llm/anthropic": [ "claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5" ], "…": [ "…" ] },
  "calls": 12,
  "medians": [ { "name": "transcription_delay", "seconds": 0.13, "turns": 41 },
               { "name": "e2e_latency", "seconds": 0.94, "turns": 39 } ],
  "unavailable_reasons": { "speaks": "elevenlabs has no API key in this process" } }
```

Each stage names the vendor a session **would** be built with, which is what the agent's settings
say or this build's default for that modality — never a guess. `greeting` is the opening in the
wire's `GreetingConfig` shape, set or null. `voices` are the names the voice may be set to, and
`providers` every vendor each stage could be moved onto, the same rows `GET /v1/providers` answers.
`defaults` is the vendor each stage runs on when the agent declares none, and `models` the models
this build vouches for under `<modality>/<vendor>`, each vendor's default first — the same two the
catalogue carries, built by the same function, so the screen that turns a stage offers a list a
person picks from instead of a box they type a model name into. A vendor with no entry in `models`
runs its own default and takes no model name. `medians` is `log/latencies.py` over every
turn of the agent's last calls together: livekit's own field names, in the order a turn happens,
the median and not the mean, and a measure nobody measured has no row rather than a zero. `calls`
is how many logs it read. `unavailable_reasons` names a stage whose vendor has no API key in this
process, so a screen can say so before the line goes dead instead of after.

### Changing any of it

The vendors, the models, the voice, the opening: the agent's **settings** — per world, per corner,
a version a row — at `PUT /v1/agents/{slug}/settings` ([settings-api.md](settings-api.md)), applied
on the agent's **next session** through `GET /v1/agents/{slug}/config`, the one door an agent's
config has ever reached a worker by, so nothing is restarted and nothing is deployed. This door
reads; it turns nothing.

### The hold melody: `GET` · `PUT /v1/agents/{slug}/pipeline/hold-audio`

What a caller hears while a tool runs — the melody the runtime ships with, silence, or a file the
org uploaded. Doors of its own and never a field of the settings' whole-set PUT, because a file is
not a knob; one choice per agent per **org**, whichever world asks. The same key as the rest of
this page, holding `pipeline`, and the same `404` in the same words when no app is holding the
agent. The `GET` says which of the three it is:

```json
{ "played": "custom", "name": "espera.mp3", "seconds": 41.2, "sha256": "9f2c…" }
```

`played` is the whole answer. `default` is the runtime's own melody, and says its name and length
(`"A New Life"`, 24 seconds). `off` is silence and says nothing else: `name`, `seconds` and
`sha256` are all null. `custom` is an uploaded clip, with the file name it arrived under (null
when it arrived with none), its length, and the sha256 of the **converted** bytes — which is what
a worker recognises a clip it has already fetched by.

`GET …/pipeline/hold-audio/audio` is the file itself, `audio/ogg`, to listen to before a caller
does: the uploaded clip, or the runtime's own when nobody uploaded one. `404` while the agent
plays none, naming the door that turns it back on.

`PUT /v1/agents/{slug}/pipeline/hold-audio` uploads one, and **the body IS the file** — a wav, an
mp3, an ogg, an m4a, whatever this box decodes — not a form: one request, no multipart, and the
name it had rides in `?name=` (200 characters at most). It is converted here, once, to Ogg Opus
48 kHz mono, so every worker plays the same bytes and none of them decodes a stranger's mp3
mid-call; it answers the `custom` shape above and plays from the next call on. `413` over 20 MB.
`400`, in a sentence, for a file that is no audio this box can read, for one longer than five
minutes — a melody loops, so the length buys nothing — and for one shorter than a second.

`PUT /v1/agents/{slug}/pipeline/hold-audio/played` takes the two choices that need no file,
`{"played": "default"}` or `{"played": "off"}`, and answers the same shape. An uploaded clip is
forgotten either way: `default` drops the row, `off` keeps one that names no file, and going back
to a clip of your own means uploading it again.

### The worker's own: `GET /v1/agents/{slug}/hold-audio` · `…/audio`

The same two reads without `/pipeline`, for the process building a call. They take a key holding
`app` or `calls`, and they resolve in a **corner** rather than in the key's own: the fleet's key
names the call's `?org=&env=&holder=`, as the dispatch said them, and a tenant's key names
nothing and is answered in its own. `404` in the config door's words when nobody holds the agent
in that corner, or when the agent held there is another org's.

The answers are the ones above, byte for byte: the `HoldAudioAnswer` and, at `…/audio`, the
`audio/ogg` the call plays — `404` when the agent plays none, which the worker reads as silence
rather than as a failure. A worker asks once per call and fetches the bytes only for a `sha256` it
has not got, keeping each clip on disk under that hash; a changed clip is a new hash. Neither door
writes: a worker never chooses the melody — the pair above is the only place it is chosen.
