# Providers, an org's own keys, and the voices

The org's key holding `providers` for the catalogue and the keys; `pipeline` for the voices.

## `GET /v1/providers`

Every vendor this build runs, and how **this org** may run each:

```json
{ "providers": [ { "name": "elevenlabs", "does": ["tts"], "availability": "offered", "ready": true, "broken": null, "voices_listed": true } ],
  "defaults": { "llm": "anthropic", "stt": "soniox", "tts": "elevenlabs" },
  "models": { "tts/elevenlabs": ["eleven_flash_v2_5"] } }
```

A vendor is a livekit plugin installed on the box; nothing in code lists them. `availability` is
`yours` (the org brought its key), `offered` (the box lends its own to this org), `bring your own`
(installed, and no key would run it for this org) or `broken` (the plugin does not import, `broken`
says why). `ready` is the first two. `voices_listed` says whether `GET /v1/voices` lists that
vendor's voices. `defaults` and `models` are the box's providers row.

## The org's own keys

| | |
|---|---|
| `PUT /v1/provider-keys/{vendor}` | `{"key": "…"}`, or `{"credentials": {…}}` with the object the vendor's plugin takes (`speech_key` and `speech_region`); every call of the org runs on it from the next one |
| `DELETE /v1/provider-keys/{vendor}` | that vendor back on the box's key; `404` when the org brought none |
| `GET /v1/provider-keys` | `{"vendors": ["elevenlabs"]}`, names only |

No door a person reads answers with a key. The one door that does is the worker's,
`GET /v1/agents/{slug}/provider-keys`, which hands a call's three stages with the credentials each
runs on, to the fleet's key or the org's own worker. Every row is sealed under `PINECALL_VAULT_KEY`,
which a gateway does not start without.

## When a vendor fails

The providers row may give each default stage an ordered list of `fallbacks`
([operator-api.md](operator-api.md)). A call whose agent runs that default stage then runs
livekit's `FallbackAdapter` over the default and its fallbacks: a vendor that errors or times out
is marked down, the stage's next request goes to the first one still up, and livekit keeps asking
the one that failed until it answers again. A stage with no fallbacks is the vendor's own object,
as before; an agent that names its own vendor runs that vendor alone.

Each fallback runs on its own key, found as the default's is: the org's own, else the box's where
its `lends` allow. One this org has no key for is left out, and so is, when the call is built, ears
that do not stream or a voice of another channel count; the call runs on the rest. A fallback voice
speaks the row's voice for its vendor and the call's language, never the agent's (a voice id is its
own vendor's). The worker's door, `GET /v1/agents/{slug}/provider-keys`, hands each stage with its
`fallbacks` beside it; a worker of an earlier release reads the stage and runs the default alone.

Each switch is a `vendor.switched` entry on the call's log (which vendor went down or came back, and
which serves the stage now), and every metrics block keeps the vendor that served it. Usage is
counted per vendor and model that answered, so a call is priced at the rates of the vendors that
actually ran, and a lent fallback's usage is the operator's like any lent stage's.

## The voices

`GET /v1/voices?tts=&language=` lists a vendor's own voices as its plugin lists them, on the key a
call would use, in the vendor's order, filtered to the language's primary tag when one is named:
`{tts, language, voices: [{id, name, language, description, gender, country, accent}]}`. A vendor
whose plugin lists none is `404`; one this build lacks is `400`.

`POST /v1/voices/sample {tts, voice, model?, language?, text?}` says a line with that voice over
the same path a call speaks on and answers the WAV itself, `audio/wav`, with `Server-Timing:
first-audio;dur=…, total;dur=…`. The words are read exactly as the settings door reads them, so
what plays is what saves. No `text` is the providers row's line for the language, else one in
English. `400` past 400 characters; `429` past thirty samples a minute on one key; a vendor that
refuses or does not answer is `502` in its own words.
