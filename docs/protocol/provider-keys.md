# Providers, an org's own keys, and the voices

The org's key holding `providers` for the catalogue and the keys; `pipeline` for the voices.

## `GET /v1/providers`

Every vendor this build runs, and how **this org** may run each:

```json
{ "providers": [ { "name": "elevenlabs", "does": ["tts"], "availability": "offered", "ready": true, "broken": null, "voices_listed": true } ],
  "defaults": { "llm": "anthropic", "stt": "soniox", "tts": "elevenlabs" },
  "models": { "tts/elevenlabs": ["eleven_flash_v2_5"] } }
```

A vendor is a livekit plugin installed on the platform; nothing in code lists them. `availability` is
`yours` (the org brought its key), `offered` (the platform lends its own to this org), `bring your own`
(installed, and no key would run it for this org) or `broken` (the plugin does not import, `broken`
says why). `ready` is the first two. `voices_listed` says whether `GET /v1/voices` lists that
vendor's voices. `defaults` and `models` are the platform's providers row.

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
| `PUT /v1/provider-keys/{vendor}` | `{"key": "…"}`, or `{"credentials": {…}}` with the object the vendor's plugin takes (`speech_key` and `speech_region`): secrets alone, eight short scalars at most, each a field a constructor of the vendor takes, none an address, a file or a session (`base_url`, `endpoint`, `*_file`, `http_session`… are the operator's, in the providers row's tuning), `400` otherwise; every call of the org runs on it from the next one |
| `DELETE /v1/provider-keys/{vendor}` | that vendor back on the platform's key; `404` when the org brought none |
| `GET /v1/provider-keys` | `{"vendors": ["elevenlabs"]}`, names only |

No endpoint a person reads answers with a key. The one endpoint that does is the worker's,
`GET /v1/agents/{slug}/provider-keys`, which hands a call's three stages with the credentials each
runs on, to the fleet's key or the org's own worker. The platform's keys go to its own workers alone: an
org's worker (its app key) is handed the org's own keys and lent none of the platform's, a stage on a
vendor it brought no key for refused `403` in those words. The worker names the call it is about to open
(`?for_call=`), as it does to `GET /v1/agents/{slug}/config`, so a canary picks the call's version
([settings-api.md](settings-api.md)). Every row is sealed under `PINECALL_VAULT_KEY`,
which a gateway does not start without.

## When a vendor fails

The providers row may give each default stage an ordered list of `fallbacks`
([operator-api.md](operator-api.md)). A call whose agent runs that default stage then runs
livekit's `FallbackAdapter` over the default and its fallbacks: a vendor that errors or times out
is marked down, the stage's next request goes to the first one still up, and livekit keeps asking
the one that failed until it answers again. A stage with no fallbacks is the vendor's own object,
as before; an agent that names its own vendor runs that vendor alone.

Each fallback runs on its own key, found as the default's is: the org's own, else the platform's where
its `lends` allow. One this org has no key for is left out, and so is, when the call is built, ears
that do not stream or a voice of another channel count; the call runs on the rest. A fallback voice
speaks the row's voice for its vendor and the call's language, never the agent's (a voice id is its
own vendor's). The worker's endpoint, `GET /v1/agents/{slug}/provider-keys`, hands each stage with its
`fallbacks` beside it; a worker of an earlier release reads the stage and runs the default alone.

The row's order is the operator's, and it stands but for a vendor over its error line: the
gateway keeps, for the last two minutes, how many calls it handed each vendor first for a stage and
how many calls saw that vendor fail (an `error` whose message names its plugin, or a
`vendor.switched` away from it, counted once per call however often it failed). A vendor handed at
least five calls, half or more of which saw it fail, goes behind every vendor that is not, when the
next call's stages are resolved (`providers/credentials.py` `stage`, at the worker's endpoint); among
themselves the ones over the line keep the row's order. It stays last until its failures leave the
two minutes: then it is back in its place, and the next calls try it again. With no fallbacks, or
no failures, the order is exactly the row's; an agent that names its own vendor runs it whatever
its failures. Nothing is ordered by price. The window is in each gateway process's memory, counted
from what the worker's append endpoints take: a restarted gateway starts it empty, and with several
gateways each orders by what it saw itself. `/metrics` says it as `pinecall_vendor_failing{vendor}`: 1 for a vendor over
the line, 0 for every other one installed, so the family is there before anything fails. A written call, which the gateway runs itself and
whose failures reach no append endpoint, runs the row's order.

Each switch is a `vendor.switched` entry on the call's log (which vendor went down or came back, and
which serves the stage now), and every metrics block keeps the vendor that served it. Usage is
counted per vendor and model that answered, so a call is priced at the rates of the vendors that
actually ran, and a lent fallback's usage is the operator's like any lent stage's.

## The voices

`GET /v1/voices?tts=&language=` lists a vendor's voices, filtered to the language's primary tag
when one is named: `{tts, language, voices: [{id, name, language, description, gender, country,
accent}]}`. The voices the providers row lists for that vendor and language come first, in the
row's order (`listed`, keyed `vendor/language`, [operator-api.md](operator-api.md)); where it lists
none, the vendor's own as its plugin lists them, on the key a call would use, in the vendor's
order. So a vendor whose plugin lists nothing — Cartesia's, among most — is listed by the row, as
data, and no vendor is written in code. One listed by neither is `404`, before any key is asked
for; one this build lacks is `400`. `voices_listed` in the catalogue is true for either.

`POST /v1/voices/sample {tts, voice, model?, language?, text?}` says a line with that voice over
the same path a call speaks on and answers the WAV itself, `audio/wav`, with `Server-Timing:
first-audio;dur=…, total;dur=…`. The words are read exactly as the settings endpoint reads them, so
what plays is what saves. No `text` is the providers row's line for the language, else one in
English. `400` past 400 characters; `429` past thirty samples a minute on one key; a vendor that
refuses or does not answer is `502` in its own words.
