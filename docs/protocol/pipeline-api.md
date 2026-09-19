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
  "calls": 12,
  "medians": [ { "name": "transcription_delay", "seconds": 0.13, "turns": 41 },
               { "name": "e2e_latency", "seconds": 0.94, "turns": 39 } ],
  "unavailable_reasons": { "speaks": "elevenlabs has no API key in this process" } }
```

Each stage names the vendor a session **would** be built with, which is what the agent's settings
say or this build's default for that modality — never a guess. `greeting` is the opening in the
wire's `GreetingConfig` shape, set or null. `voices` are the names the voice may be set to, and
`providers` every vendor each stage could be moved onto, the same rows `GET /v1/providers` answers. `medians` is `log/latencies.py` over every
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
