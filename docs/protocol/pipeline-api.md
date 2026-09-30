# An agent's pipeline — `/v1/agents/{slug}/pipeline`

The org's own key, holding `pipeline`. These doors belong to the agent: a tenant's own dashboard
opens them as readily as the console does.

## `GET /v1/agents/{slug}/pipeline`

What one agent would hear, decide and speak with on its next call, and how fast its last calls were.

```json
{ "agent": "clinica-norte",
  "hears":   { "vendor": "soniox", "model": null, "voice_id": null, "language": "es" },
  "decides": { "vendor": "anthropic", "model": "claude-haiku-4-5", "voice_id": null, "language": null },
  "speaks":  { "vendor": "elevenlabs", "model": null, "voice_id": "a-voice", "language": "es" },
  "greeting": { "say": "Clínica Norte, buenas.", "reply": null, "allow_interruptions": null },
  "providers": [ … ],
  "defaults": { "llm": "anthropic", "stt": "soniox", "tts": "elevenlabs" },
  "models": { "tts/elevenlabs": [ "eleven_flash_v2_5" ] },
  "calls": 12,
  "medians": [ { "name": "transcription_delay", "seconds": 0.13, "turns": 41 } ],
  "unavailable_reasons": { "speaks": "elevenlabs has no key: the org brought none and the box holds none" } }
```

Each stage is the vendor a call **would** be built with: the agent's settings, else the providers
row's default for that modality. Nothing is built to answer. `providers` are the rows
`GET /v1/providers` answers this org ([provider-keys.md](provider-keys.md)); `defaults` and `models`
are the providers row's. `medians` pools every turn of the agent's last twenty calls, livekit's own
field names, the median and not the mean; a measure nobody took has no row. Two rows are the
runtime's: `dead_air`, the silence between the caller stopping and the agent starting, and
`talk_share`, the agent's part of each call's talking, whose `seconds` is a fraction 0 to 1 and
whose `turns` counts calls ([evals.md](evals.md)). `unavailable_reasons`
names a stage with no key to run on, in the refusal's own words, so a screen says so before a line
goes dead. When no app holds the agent the stages come from a bare declaration and the settings.

Changing any of it is the agent's **settings** ([settings-api.md](settings-api.md)), applied on the
next call; this door reads.

## The hold melody — `GET` · `PUT /v1/agents/{slug}/pipeline/hold-audio`

What a caller hears while a tool runs or the model has not said its first word: the box's melody,
silence, or a clip the org uploaded. It starts after 2.5 s of the agent being quiet, so a tool or a
model that answers inside that plays nothing, and stops at the answer. One choice per agent per
world, whoever holds it.

```json
{ "played": "custom", "name": "espera.mp3", "seconds": 41.2, "sha256": "9f2c…" }
```

`default` is the box's own melody, `off` is silence, `custom` an uploaded clip with the name it
arrived under, its length, and the sha256 of the **converted** bytes, which is what a worker
recognises a clip it already fetched by.

`PUT …/hold-audio?name=` uploads one, and **the body is the file**: a wav, an mp3, an ogg, an m4a,
whatever this box decodes; no multipart. It is converted here, once, to Ogg Opus 48 kHz mono, so no
worker decodes a stranger's upload mid-call; `400` in a sentence for a file that is no audio, one
longer than five minutes or shorter than a second, `400` over 20 MB.
`GET …/hold-audio/audio` is the org's own clip, `audio/ogg`, to hear before a caller does; `404`
while the agent plays no clip of its own. `PUT …/hold-audio/played {played: "default" | "off"}`
needs no file; an uploaded clip is forgotten either way.

The worker's own reads are `GET /v1/agents/{slug}/hold-audio` and `…/audio`, on the fleet's key,
in the call's scope.
