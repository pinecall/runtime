# The shapes

The two frames on the wire and the shapes that events, commands and the state share.

### `Entry`

One line of a call's log, or of an agent's log when call is null. seq is written before control returns, so two readers never disagree about order.

| field | type | required | meaning |
|---|---|---|---|
| `seq` | `integer` | yes | The entry's place in its log, starting at 1, never reused. The cursor a reader resumes from. |
| `ts` | `number` | yes | When the entry was written, unix seconds with fractions. |
| `call` | `string | null` | yes | The call this entry belongs to. Null for entries about the agent itself: registered, configured, pong, an error outside any call. |
| `agent` | `string` | yes | The agent's slug, as it registered: clinica-norte. |
| `type` | `string` | yes | Which event this is, dotted: call.started, turn.user, metrics.llm. The closed list is the events/ folder. |
| `ephemeral` | `boolean` | yes | True for entries a store may drop and a slow reader may miss without harm: interim transcripts, VAD health, replay markers. False for everything the truth is made of. |
| `data` | `object` | yes | The event's payload, whose shape is the event's own schema under events/<type>.json. |

### `Command`

One instruction from an app to the gateway over its WebSocket. The gateway answers with the events the command produces, or with an error naming the command's id.

| field | type | required | meaning |
|---|---|---|---|
| `type` | `string` | yes | Which command this is, dotted: state.set, call.dial, ping. The closed list is the commands/ folder. |
| `agent` | `string` | yes | The agent this command speaks for. A socket may serve several agents. |
| `call` | `string | null` | yes | The call the command is about, or null for commands about the agent: register, configure, dial, ping. |
| `id` | `string` | no | The app's own id for the command, echoed back on an error so the app knows which one failed. |
| `data` | `object` | yes | The command's payload, whose shape is the command's own schema under commands/<type>.json. |

### `Channel`

The endpoint the public came through: a phone call over SIP, the browser widget over WebRTC, or WhatsApp text.

One of: `phone`, `web`, `whatsapp`.

### `Medium`

How a call is had: spoken in a room, or written in a text session. A `web` call is either.

One of: `voice`, `text`.

### `Direction`

Inbound: the public reached the agent. Outbound: the agent reached out (a dial).

One of: `inbound`, `outbound`.

### `Env`

Which of the two environments a key opens, and so which environment an agent is held in and a call ran in. A key is issued into one; an agent registered on it and every call it takes carry that one; an endpoint claimed in one is refused to a key of the other. `sandbox` is where things are written and `production` is what the public reaches — and whether a sandbox agent is one PERSON's copy or the team's shared one is not this field: it is whether the key that registered it names a person. Every key issued before the field existed is production.

One of: `production`, `sandbox`.

### `DevVerb`

What a console may ask of the process standing in the agent's directory, relayed by the gateway: a written call to the class mounted there (chat), a simulated caller put on the class it holds, its goldens and a suite of them, its knowledge folder pushed or its golden asked, its memory goldens, the panel it draws beside a conversation (view), a call promoted to a candidate file, the drift of the last two windows, and the reproductions a broken run left on that disk. Everything else a console needs is an endpoint of the gateway.

One of: `chat.roster`, `chat.start`, `chat.say`, `chat.end`, `view.render`, `simulate.start`, `goldens.roster`, `goldens.run`, `knowledge.roster`, `knowledge.push`, `knowledge.eval`, `memory.roster`, `memory.eval`, `memory.extraction`, `promote.roster`, `promote.write`, `drift.read`, `reproductions.roster`, `reproductions.read`.

### `EndReason`

Why the call is over. Who hung up, what failed before anybody could, drained: the platform took the worker down (a deploy, a stop) with the call still on it, or app_detached: the app holding the agent closed its socket mid-call, so nothing was rendering the prompt or answering a tool — both are nobody's fault and neither is an error.

One of: `caller_hung_up`, `agent_hung_up`, `supervisor_ended`, `transferred`, `no_answer`, `busy`, `dial_failed`, `timeout`, `drained`, `app_detached`, `error`.

`caller_hung_up` is also a caller whose connection dropped and who did not come back within 20 s, LiveKit's own wait for a room's last person: a supervisor watching keeps the room up, so the call is ended then rather than left waiting. `timeout` is the call's ceiling (`max_duration_s`, ten minutes unless the agent says otherwise) and, for a chat in a room, which has no ceiling, ten minutes without a message from the person: a page left open would otherwise hold its seat for as long as it stays open.

### `EndedBy`

Whose action ended the call. platform covers timeouts, errors and a drained worker.

One of: `caller`, `agent`, `supervisor`, `platform`.

### `ScoreVerdict`

What one judge answered about a finished call. Held: the rule held. Broken: it did not, and the reason names the evidence. Deferred: the judge was asked and could not settle it. Skipped: nobody asked it — no model was reachable inside the call's judging budget.

One of: `held`, `broken`, `deferred`, `skipped`.

### `TransferMode`

Cold: the caller is sent on with a REFER on their SIP leg and the call ends here. Warm: the number is dialled into the call's own room, the agent stays on the line until the other side answers and then falls silent; the call ends when either of them hangs up.

One of: `cold`, `warm`.

### `PromptRegion`

Which region of the prompt a block lives in: static, before the history, cached by the provider; or dynamic, after the history, replaced every turn. The append-only history in between is never written by the app.

One of: `static`, `dynamic`.

### `DocsMode`

How the knowledge base reaches the model: retrieved, the platform runs search itself when the caller's turn ends; or tool, the model calls search when it decides to. Either way the chunks arrive as a tool result.

One of: `retrieved`, `tool`.

### `PlatformTool`

The two tools the platform runs on the app's behalf: recall reads the contact's facts out of memory, search reads chunks out of the knowledge base. The app declares memory and docs and writes neither method; the platform runs the lookup, and the answer reaches the model as a tool result rather than as part of the prompt.

One of: `recall`, `search`.

### `PromptBlockSpec`

One named block of the prompt and the region it lives in. The default layout, when an agent declares none, is identity, knowledge and tools (static), then the history, then view (dynamic). A block's text is written per call with prompt.set.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The block's name, the one prompt.set writes it by: lowercase, digits and underscores. Matches `^[a-z][a-z0-9_]*$`. |
| `region` | `PromptRegion` | yes | Which region of the prompt a block lives in: static, before the history, cached by the provider; or dynamic, after the history, replaced every turn. |

### `UserState`

What the platform believes the person on the line is doing right now. The states are the session's own.

One of: `listening`, `speaking`, `away`.

### `AgentState`

What the agent is doing right now, in the session's own words: warming up, waiting, hearing the caller, generating, or playing audio.

One of: `initializing`, `idle`, `listening`, `thinking`, `speaking`.

### `ParticipantKind`

Who a participant is to the call: the person the agent serves (over SIP or the widget), the agent itself, a supervisor who took a seat in the room, a listener who only hears, or a second SIP leg that room.invite brought in.

One of: `caller`, `agent`, `supervisor`, `listener`, `sip`.

### `TrackKind`

What a track carries: a microphone's audio, a camera's video, or a screen share.

One of: `audio`, `video`, `screen`.

### `TrackSource`

Where a track comes from, as livekit's TrackSource names it, in lower case.

One of: `microphone`, `camera`, `screen_share`, `screen_share_audio`, `unknown`.

### `EventSource`

Where an outside fact came from: the tenant's backend over the app socket (app), or a participant's browser over the DataChannel (participant).

One of: `app`, `participant`.

### `Visibility`

Who may see a field of the app's state: everyone in the call (public), the tenant's own readers (tenant, the default for a field never declared), or nobody but the app: the log keeps it as `***` and seals the value aside (pii).

One of: `public`, `tenant`, `pii`.

### `Projection`

Which projection a sink applies before a state or an entry leaves the platform: public for a participant reading its own call, tenant for the tenant's readers. The contract is projections.md; a client never applies one.

One of: `public`, `tenant`.

### `Contact`

Who is on the line, as far as the platform knows. Everything is optional: a web visitor may be nobody yet.

| field | type | required | meaning |
|---|---|---|---|
| `id` | `string` | no | The platform's own id for this contact, stable across channels and calls. |
| `phone` | `string` | no | The caller's number in E.164 form (+34 600 000 000 written as +34600000000). |
| `name` | `string` | no | The name the contact is known by, when memory or the app supplied one. |
| `email` | `string` | no | An e-mail address, when the web widget or the app supplied one. |
| `external_id` | `string` | no | The id the tenant's own system uses for this person, when the app told the platform. |

### `Route`

One endpoint to an agent: a channel and, for phone and WhatsApp, the number that answers. A number is a route, never an agent.

| field | type | required | meaning |
|---|---|---|---|
| `channel` | `Channel` | yes | The endpoint the public came through: a phone call over SIP, the browser widget over WebRTC, or WhatsApp text. |
| `number` | `string | null` | yes | The number in E.164 form for phone and WhatsApp; null for the web widget, which needs none. |
| `label` | `string` | no | A human name for the endpoint, for the console: 'main line', 'after hours'. |

### `Supervisor`

The human who sent a supervise verb, as the token that let them in names them.

| field | type | required | meaning |
|---|---|---|---|
| `id` | `string` | yes | The supervisor's id, from their token. |
| `name` | `string` | no | Their display name, when the token carried one. |

### `ToolSpec`

What the app declares about one tool: the contract the model sees and the rules the platform enforces before running it.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The name the model calls, unique within the agent: find_patient, book_slot. |
| `description` | `string` | yes | What the tool does, as the model reads it. In the app this is the method's docstring. |
| `parameters` | `object` | yes | The arguments, as a JSON Schema object the model must satisfy. |
| `side_effect` | `"read" | "write" | "irreversible"` | no | read looks at the environment; write changes it and can be undone; irreversible changes it for good, so the platform asks the caller first. Absent means read. |
| `confirm` | `string` | no | A receipt the agent reads out once the tool has run, before the model replies to the result, with {{name}} placeholders filled from the arguments and {{result.name}} from what came back: 'Reservado: {{result.when}} con {{result.professional}}.'. It does not hold the tool, so it is written as a receipt and never as a question. Absent means no read-back. |
| `pii` | `string[]` | no | Argument names that carry personal data: the log keeps them as `***` and seals the value aside, for the app alone (docs/security/private-values.md). |
| `timeout_s` | `number` | no | How long the platform waits for the app's result before reporting an error to the model, in seconds: 30 by default, above 0 and 300 at most. |

### `ToolResult`

What came back from running a tool in the app's process. Either an output or an error, never both.

| field | type | required | meaning |
|---|---|---|---|
| `call_id` | `string` | yes | The id the tool.call carried, so the result finds its call. |
| `name` | `string` | yes | The tool's name, repeated so a log line stands on its own. |
| `output` | `any` | no | Whatever the method returned, as JSON. Absent when the tool failed. |
| `error` | `string` | no | What went wrong, in words the model may read. Absent when the tool succeeded. |
| `summary` | `string` | no | A one-line version of the output for the console and for memory, when the app wrote one. |
| `duration_s` | `number` | no | How long the app's method took, in seconds. |

### `MemoryFact`

One thing remembered about a contact: a sentence, where it came from, and how well it matched when recalled.

| field | type | required | meaning |
|---|---|---|---|
| `id` | `string` | no | The fact's id in the memory store. |
| `text` | `string` | yes | The fact itself, as one sentence: 'prefers mornings', 'allergic to penicillin'. |
| `category` | `string` | no | The tenant's own word for what kind of fact this is, from its memory.remember list: 'alergias', 'preference'. |
| `score` | `number` | no | How well it matched the recall query, 0 to 1. Absent when remembering, which has no query. |
| `source` | `string` | no | The call this fact was extracted from, when known. |

### `MemoryOp`

One operation against the contact's memory: a recall during the turn, a remember at hangup, or a forget on request.

| field | type | required | meaning |
|---|---|---|---|
| `op` | `"recall" | "remember" | "forget"` | yes | recall reads facts for the turn; remember writes facts extracted at hangup; forget erases on the contact's request. |
| `contact` | `string` | no | The contact id the memory belongs to. |
| `query` | `string` | no | What was asked of memory, for a recall. |
| `facts` | `MemoryFact[]` | yes | The facts read, written or erased. |
| `took_ms` | `number` | yes | How long the operation took, in milliseconds. A recall in the turn must stay under 150. |

### `DocSource`

One chunk of the knowledge base that retrieval put in front of the model for this turn.

| field | type | required | meaning |
|---|---|---|---|
| `id` | `string` | yes | The chunk's id in the knowledge store. |
| `base` | `string` | no | The base the chunk came from, since an agent may read several: 'tarifas', 'manual-2026'. Absent on a log written before a turn could read more than one. |
| `path` | `string` | yes | The document the chunk came from, as the tenant pushed it: 'faq/horarios.md'. |
| `heading` | `string` | no | The heading the chunk sits under, since chunks are cut by heading. |
| `score` | `number` | yes | The fused rank score (vector and BM25 through RRF), higher is better. |
| `excerpt` | `string` | no | The chunk's text as the model read it, the body under its heading: the log carries the evidence an answer had to come from, so a judge reading it afterwards can find it. |

### `CostRow`

One priced line: a model, what was counted, how much, and what it came to.

| field | type | required | meaning |
|---|---|---|---|
| `provider` | `string` | yes | The provider, as the usage row names it: anthropic, elevenlabs, soniox. |
| `model` | `string` | yes | The model, as the usage row names it. |
| `unit` | `"input_tokens" | "cached_input_tokens" | "cache_creation_tokens" | "output_tokens" | "characters" | "audio_seconds" | "requests" | "session_seconds" | "minutes"` | yes | What the provider bills by for this line: `minutes` is a phone leg's, each minute begun billed whole. |
| `quantity` | `number` | yes | How many of that unit the call used. |
| `unit_price_usd` | `number` | yes | The provider's list price per unit, in US dollars, from the price table. |
| `usd` | `number` | yes | What this line came to, in US dollars: quantity at unit_price_usd. |

### `UnpricedRow`

A usage row the price table does not know. It is listed, never priced at zero.

| field | type | required | meaning |
|---|---|---|---|
| `provider` | `string` | yes | The provider of the row nobody could price. |
| `model` | `string` | yes | The model of the row nobody could price. |

### `Cost`

What the call cost in provider fees, informational, in US dollars, the currency providers price in. The runtime never prices commercially; this is the provider's bill as best we know it.

| field | type | required | meaning |
|---|---|---|---|
| `usd` | `number` | yes | The sum of every priced row, in US dollars. |
| `rows` | `CostRow[]` | yes | Every usage row that had a price, one line per unit billed. |
| `unpriced` | `UnpricedRow[]` | yes | The usage rows the price table did not know, so the total is known to be incomplete. |
| `rate` | `object` | no | Only on a summary written before money in dollars, which said `eur` where it says `usd` now: the euro's rate it carried. Read, never written; its `eur` is read as the same number of dollars, never converted, as migration 0007 kept the facts. |

### `VoiceConfig`

Which voice speaks for the agent: the name it was asked for, or the id the provider knows it by.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | no | The voice as the app wrote it: one of the platform's curated names, or a provider's own id. Resolved to a provider and an id when the app declares itself, so an unknown name is refused there and never at the first utterance. |
| `provider` | `string` | no | The TTS provider: elevenlabs, cartesia, rime, hume, or any other vendor LiveKit reaches. Absent when the curated name decides it. Naming it changes what voice_id means: the runtime passes the id through as that vendor's own, because only ElevenLabs' shape is known here. |
| `model` | `string` | no | The provider's model, when the default is not wanted. |
| `voice_id` | `string` | no | The provider's id for the voice, in that provider's own shape — a 20-character ElevenLabs id, a Cartesia uuid, a Rime speaker name. |

### `ModelConfig`

Which model does a job (the LLM, or the STT), and the one or two knobs worth turning.

| field | type | required | meaning |
|---|---|---|---|
| `provider` | `string` | yes | The provider, by its own name or any of the words it answers to: anthropic (claude), openai (gpt), cartesia, deepgram, soniox, groq, google (gemini) — every vendor LiveKit ships a plugin for — or livekit, which is LiveKit Inference and carries the vendor inside the model name (openai/gpt-5-mini). The runtime answers the whole list at GET /v1/providers. |
| `model` | `string` | yes | The provider's model name. |
| `temperature` | `number` | no | Sampling temperature, for an LLM. Absent means the provider's default. |

### `TurnConfig`

How the session decides that the caller has finished, and when the caller may interrupt.

| field | type | required | meaning |
|---|---|---|---|
| `min_interruption_words` | `integer` | no | How many words the caller must say before the agent stops talking. Guards against 'mm-hm'. |
| `endpointing_ms` | `integer` | no | How long a silence, in milliseconds, before the turn detector is asked whether the caller is done. |
| `eot_threshold` | `number` | no | How sure a recogniser that decides the end of the turn itself must be before it ends one, 0.5 to 0.9. Low is an agent that answers half a sentence; Deepgram measures a fifth of the turns ending early at its own default of 0.7. |
| `eager_eot_threshold` | `number` | no | The lower confidence at which such a recogniser says the turn MIGHT be over, 0.3 to 0.9 and never above eot_threshold, so the model may start on an answer that is thrown away if the caller carries on. Absent means it never guesses. |
| `min_interruption_ms` | `integer` | no | How long, in milliseconds, the caller must speak over the agent before it stops talking, judged by the local voice activity detector alongside min_interruption_words. Absent is livekit's own half second. |

### `Pronunciation`

How the voice says one word it would otherwise get wrong: a proper name, a brand, a street.

| field | type | required | meaning |
|---|---|---|---|
| `word` | `string` | yes | The word as the model writes it: Vidal. |
| `spoken` | `string` | yes | What the voice is given instead: bidál. Applied to the reply before it is spoken, never to the log. |

### `StateFieldSpec`

What the app declares about one field of its state: who may see it. A field never declared is tenant.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The field's name in the app's state, as state.set sends it: patient, slots. |
| `visibility` | `Visibility` | yes | Who may see a field of the app's state: everyone in the call (public), the tenant's own readers (tenant, the default for a field never declared), or nobody but the app: the log keeps it as `***` and seals the value aside (pii). |

### `ViewSpec`

The panel the agent draws beside a conversation: it is declared here so a console knows the agent has one before it asks for it, and draws its own about the contact when it has not. What the panel CONTAINS never comes this way — it is asked for a conversation at a time, through the view.render dev verb.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | What the panel is called over its first section: Customer, Order, Patient. |

### `EventSpec`

One outside event the agent accepts, and from whom. An event nobody declared is refused before it touches the log.

| field | type | required | meaning |
|---|---|---|---|
| `name` | `string` | yes | The event's name, dotted, as call.event or pinecall.event will send it: slot.released, form.submitted. |
| `from` | `EventSource[]` | yes | Who may send it: the tenant's backend, a participant's browser, or both. |

### `KnowledgeFile`

One file of a base, as a push sends it: its path as the tenant keeps it, and its text.

| field | type | required | meaning |
|---|---|---|---|
| `path` | `string` | yes | The file's path as the tenant keeps it, relative to the agent: 'knowledge/clinica.md', 'faq/horarios.md'. |
| `text` | `string` | yes | The file's whole text, as the app read it from disk. |

### `DocsConfig`

The knowledge base the agent answers from, and how its chunks reach the model. It is named by the base it was pushed under, with PUT /v1/knowledge/{base}.

| field | type | required | meaning |
|---|---|---|---|
| `base` | `string` | yes | The base's name, the one it was pushed to with PUT /v1/knowledge/{base}: 'clinica-norte'. |
| `mode` | `DocsMode` | no | How the knowledge base reaches the model: retrieved, the platform runs search itself when the caller's turn ends; or tool, the model calls search when it decides to. |
| `k` | `integer` | no | How many chunks a turn's retrieval puts in front of the model at most. |
| `min_score` | `number` | no | The fused rank score a chunk must reach to be put in front of the model. Absent, nothing is dropped from the top k. |

### `GreetingConfig`

How the agent opens a call, before the caller has said anything. Exactly one of the two, because there are only two ways to open one: `say` are the words themselves and `reply` is what the model is told before it finds its own. They are agent.say and agent.reply declared instead of called, so a class that opens every call the same way needs no onCall hook to do it, and an operator can turn the opening at the pipeline endpoint without a deploy. Absent: nobody speaks until the caller does.

| field | type | required | meaning |
|---|---|---|---|
| `say` | `string` | no | The opening words, read out as written: 'Clínica Norte, buenos días.' No model runs. |
| `reply` | `string` | no | What the model reads before it speaks its own opening — 'saluda, di que eres la recepción y pregunta en qué puedes ayudar' — and the caller never hears. Not what it says. |
| `allow_interruptions` | `boolean` | no | Whether the caller may cut the opening short. Default true; a legal notice sets false. |

### `HangupConfig`

Whether the model may end the call itself. Declaring this is what puts livekit's own end_call tool in front of the model; a class that says nothing here cannot hang up, and the call ends when the caller does or when a supervisor says so. The tool is hidden while the agent is greeting, because a model that can hang up on its first turn eventually does.

| field | type | required | meaning |
|---|---|---|---|
| `when` | `string` | no | When the agent should end the call, in the tenant's own words and their own language. It is appended to the tool's description, which already says to end it when the caller is clearly done and never when the intent is unclear. |

### `MemoryConfig`

What memory keeps about a contact across calls, and what it must never keep. Both lists are in the tenant's own words.

| field | type | required | meaning |
|---|---|---|---|
| `remember` | `string[]` | no | The kinds of fact worth keeping, as the tenant names them: 'alergias', 'preference', 'pets'. They become the categories remember extracts and recall reads back. |
| `forget` | `string[]` | no | The kinds of fact that are never written, whatever the call said: 'card_numbers', 'diagnosis'. Dropped before they touch the store. |

### `AgentConfig`

What an app declares about its agent: the prompt's layout, the language, the tools, whether it searches its bases itself, and who may see and send what. Every field is optional so a configure can change one thing. The environment — voice, models, greeting, hangup, turn, says, hears, knowledge, docs, memory — is the environment's, set in the agent's settings, and no longer read off this declaration.

| field | type | required | meaning |
|---|---|---|---|
| `prompt` | `PromptBlockSpec[]` | no | The blocks of the prompt in the one order they are sent: every static block, then the history, then every dynamic block. Absent, the layout is the default: identity · knowledge · tools, the history, view. |
| `language` | `string` | no | The language the agent speaks and expects, as a BCP 47 tag: es-ES, es-UY. |
| `greeting` | `GreetingConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `voice` | `VoiceConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `llm` | `ModelConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `stt` | `ModelConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `turn` | `TurnConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `says` | `Pronunciation[]` | no | Ignored: the environment's, set in the agent's lexicon. Kept one release so an app on an older package still registers; removed in the next. |
| `hears` | `string[]` | no | Ignored: the environment's, set in the agent's lexicon. Kept one release so an app on an older package still registers; removed in the next. |
| `knowledge` | `KnowledgeFile` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `docs` | `DocsConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `memory` | `MemoryConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `hangup` | `HangupConfig` | no | Ignored: the environment's, set in the agent's settings. Kept one release so an app on an older package still registers; removed in the next. |
| `record` | `boolean` | no | Whether this agent's calls keep their audio. The environment's, set in the agent's settings and never in the class: the platform records the whole room, so what is kept is what everybody on the call heard. |
| `tools` | `ToolSpec[]` | no | Every tool the agent may ever see. Which ones are visible now is tools.set. |
| `uses_knowledge` | `boolean` | no | Whether the class searches the knowledge base itself (this.knowledge.search). An environment with no base attached to the agent refuses the registration, so a tool that would find nothing is refused at boot and not on a call. |
| `state_fields` | `StateFieldSpec[]` | no | Who may see each field of the app's state. A field not listed is tenant: seen by the tenant's readers, never by the public. |
| `view` | `ViewSpec` | no | The panel this agent draws beside a conversation, or absent when it draws none. |
| `events` | `EventSpec[]` | no | The outside events this agent accepts and from whom. Anything else is refused before it touches the log. |

