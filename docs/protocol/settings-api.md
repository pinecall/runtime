# An agent's settings — `/v1/agents/{slug}/settings` and `/v1/agents/{slug}/lexicon`

What an agent runs on is the org's, not the class's: which vendors and models, how a call opens and
ends, how a turn is cut, what is remembered, which knowledge bases it reads, and its lexicon,
how the voice says a brand and what the ears must know. These endpoints keep all of it **per environment, per
scope, a version a row**, and a call's head row says which versions it ran on. The class declares
the contract, the tools, the state, the language, the endpoints, and nothing of this: the environment is put
on the declaration at the one place every call is built, and a knob the org never set is the
runtime's default.

**Two kinds of key open them.** A key that opens `pipeline` (a developer's, an admin's) may set
everything. A key that opens `words` alone (a supervisor's, a manager's) may set the opening's
words, what is remembered and the agent's lexicon, and is refused the vendors, the models, the cut of
a turn, the model's deadline, the bases, the recording and the duration **by name**: `403 llm, turn: the pipeline's, and
this key opens words alone`. Its set carries those fields over untouched from what stands.

**Three scopes.** In the sandbox a person's key has a scope of its own: what they set is theirs
until they set it with `team: true`, and a colleague's next call does not hear it. `team: true`
writes the org's own scope, which every scope falls back to. A key that names nobody (CI's) writes
the org's own scope always. Production has one scope, the org's own, set directly by a request that
runs there (`pinecall-env: production`): a person with production access, or a production server's
key.

**Every knob falls through on its own.** A scope supplies the knobs it set, and each knob it did not
set is read from the scope below it, never the whole row of the first scope that has one. Setting a
voice of your own does not disconnect you from the team's model:

| set in | `voice` | `llm` | `stt` |
|---|---|---|---|
| yours | `nova` | | |
| the team's | | `anthropic/claude-sonnet-5` | `soniox` |
| **what the next call runs** | `nova` | `anthropic/claude-sonnet-5` | `soniox` |

A knob is **set** when it is in the row, which is what leaving it out of a `PUT` decides. A knob set
to a falsy value is set and wins over the scope below (`turn {endpointing_ms: 0}`, `bases []`); only
an absent knob falls through. The version a call records is the nearest scope that supplied a knob.

**Versions.** Every set is a new row; nothing is updated, nothing deleted. The body carries the
version it was read at, and a scope that moved on since answers `409 this corner is at v{n} now`.
A rollback is a new version equal to an old one, and the history says so.

## `GET /v1/agents/{slug}/settings` — `pipeline` or `words`

```json
{ "world": "sandbox",
  "yours":      { "holder": "m_ana", "version": 4, "author": "m_ana", "note": "flat", "set_at": 1758300000, "config": { "voice": "amelia" } },
  "team":       { "holder": "", "version": 11, "author": "m_bruno", "note": null, "set_at": 1758200000, "config": { "llm": "anthropic/claude-haiku-5-5", "greeting": { "say": "Buenas…" } } },
  "production": null }
```

Each scope's **own** newest, or null when that scope set nothing; never the fallthrough, because
`if_version` is about the scope being written. `config` carries only the knobs the row set: `voice`,
`tts`, `tts_model`, `stt`, `llm` (the three model knobs take `vendor/model`, a vendor alone to keep
its own default model, or a model alone on whichever vendor is in use), `language` (a tag, `en` or
`pt-BR`, given to the ears and the voice; unset, each vendor's own default, or the language an app on
an SDK before 0.9.19 still declares), `greeting` (`{say}` or
`{reply}`), `hangup {when}`, `turn {min_interruption_words, endpointing_ms, eot_threshold,
eager_eot_threshold, min_interruption_ms}` (the last is how long the caller must speak over the
agent before it stops; unset, livekit's own half second), `memory {remember, forget}`, `record`, `max_duration_s` (voice calls; `0` is
no limit), `llm_timeout_s` (how long a turn waits for the model's first word, livekit's retries
included; past it the caller hears a short sentence asking them to say it again, in the agent's
language (Spanish, English, Portuguese; English otherwise), and the call's log says `error {code:
llm_timeout}`; unset, 12 s), `knowledge` (Markdown read whole into the static block of every call) and `bases
[{base, mode, k, min_score}]`.

## `PUT /v1/agents/{slug}/settings` — `pipeline` or `words`

```json
{ "config": { "voice": "amelia", "llm": "anthropic/claude-haiku-5-5" }, "if_version": 4, "note": "cleaner", "team": false }
```

The **whole** set for this scope. It is checked as a call would be built from it: the declaration
when an app holds the agent, a bare one otherwise, its lexicon in the scope, and the vendors on the
org's keys, so a vendor this build has no plugin for, a blank knob, an opening with both verbs or a turn or
voice knob the vendor takes under no name ([provider-keys.md](provider-keys.md)) is `400` in its
own sentence, and a vendor the platform does not lend this org is refused here and not on
the next call. A `pipeline` key's set is held to the bands the ears take the thresholds in —
`eot_threshold` 0.5 to 0.9, `eager_eot_threshold` 0.3 to 0.9, outside which they refuse the
connection — and `400 eot_threshold 0.1 is outside 0.5 to 0.9` otherwise; a version kept before
this check is still read. A stage the set changes — the voice, its vendor or model, the model, the
ears — is tried once before anything is kept, on the key a call would use: a line said, one word
answered, half a second of silence heard (ears that take only a whole utterance are tried by their
first call). The vendor's no is `400 acme refused the voice this sets: no such voice`, in its words;
a vendor that does not answer is `502 … nothing was kept, try again`. A save that changes no stage
asks no vendor anything. `409` when the scope is not at `if_version`. Answers the `GET` shape.

## `GET …/settings/history?team=&limit=` · `GET …/settings/diff?against=team|production` · `POST …/settings/rollback {version, team}`

One scope's versions, newest first, each with who set it and why. This key's scope's newest
against the team's or production's newest, with the fields that differ by name. One version copied
forward as the next one, `note: "rollback to v{n}"`; `404` for a version the scope never had.
Rollback takes `pipeline`.

## A version on a share of the calls — `GET` · `PUT` · `DELETE /v1/agents/{slug}/settings/canary`

A new version need not take every call at once. `PUT {version, share, note, team}` puts one of
the scope's own versions (yours, or the team's with `team: true`) on `share` calls in a hundred
(0 to 100): each call is picked once, by its id, where its settings are resolved (the worker asks
for them naming the call it is about to open, `?for_call=`, and the open records the same pick),
so a call asked for again, or reopened by a gateway that restarted, keeps its version. Every other
call runs what the scope would run without that version: the newest of its others. Setting it
takes `pipeline`, as the vendors do: a canary decides what a share of the calls runs. A share of 0
keeps the version off every call and the canary standing; `DELETE ?team=` clears it, and every call
runs the scope's newest version again (the canary's, when it was the newest: that is promoting it).
A version the scope never had is `404`. `GET ?team=` (`pipeline` or `words`) and every endpoint answer
`{world, holder, canary: {holder, version, share, author, note, set_at} | null}`; an older worker,
which names no call, runs every call off the canary. The two versions are compared by
`GET /v1/insights/drift?agent=&before=v3&after=v4` ([console-api.md](console-api.md)): the same
days, two versions, which judge and which stage moved.

```
PUT /v1/agents/recepcion/settings/canary
{"version": 4, "share": 10, "note": "shorter greeting", "team": true}

{"world": "sandbox", "holder": "",
 "canary": {"holder": "", "version": 4, "share": 10, "author": "m_ana",
            "note": "shorter greeting", "set_at": 1790000000.1}}
```

## `GET /v1/calls/{call}/settings` — `calls`

The exact tuning and lexicon the call was built on, by the two version numbers its head row kept:
`{config_version, lexicon_version, config, lexicon, canary}`, each row null where the scope had set
nothing; `canary` is true for a call a canary's share picked (its version was the canary's that
stood when it opened), false otherwise.

## The lexicon — `/v1/agents/{slug}/lexicon`

The agent's words: what the voice says in place of a word (`says`) and the words the ears must
know (`hears`). The class sets neither; each agent has a lexicon of its own, versioned per environment
and scope as its settings are. `GET` answers `{world, yours, team, production}` of `{holder,
version, author, note, set_at, lexicon: {said: [{word, spoken}], heard: [string]}}`.
`PUT {lexicon, if_version, note, team}` is the whole lexicon, with the same `409`, and a blank
word refused. `GET /v1/agents/{slug}/lexicon/history?team=&limit=`. All three open to `pipeline`
or `words`.
