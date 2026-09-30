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

## Where the abstraction is, and where it stops

**livekit's `LLM`, `STT` and `TTS` classes are the abstraction; what differs between vendors is
data.** A vendor is its plugin's class built with the org's key; what it takes is its constructor's
parameters, read when it is built (the key, the voice and the language each land under whichever
name the plugin gave them); what it can do is the class's capabilities (streaming, interim
results, keyterms, an aligned transcript); whether its ears end the turn themselves
(`ends_the_turn`), which of its classes runs (`builds`) and its options are the providers row's.
Nothing in code names a vendor, so a fifth vendor is `pip install "livekit-agents[<vendor>]"` and a
row, and the suite proves it before a call does: every installed vendor is built offline with a key
alone, and either reports the model and the provider the usage reads and the capabilities the
session reads, or is refused in our words, never in its own exception.

A knob the org sets that the vendor takes under no name — a turn's `endpointing_ms`,
`eot_threshold` or `eager_eot_threshold` on ears that have no such parameter, a `voice` on a voice
that picks none — is **refused where it is set**, `400 soniox's stt takes no eager_eot_threshold`
on `PUT /v1/agents/{slug}/settings` with a `pipeline` key, instead of being dropped on every call.
A set a `words` key writes carries those knobs over untouched and is not refused for them, and a
setting stored before this refusal still runs as it did. The abstraction stops where a vendor's
behaviour is not a parameter: a stream's timing, how it cuts a sentence, what its confidence
means. Those are measured per vendor and per day (`GET /v1/insights`, `stages`), not abstracted.

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
