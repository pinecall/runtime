# An agent's settings — `/v1/agents/{slug}/settings` and `/v1/agents/{slug}/lexicon`

What an agent runs on is the org's, not the class's: which vendors and models, how a call opens and
ends, how a turn is cut, what is remembered, which knowledge bases it reads, and its lexicon,
how the voice says a brand and what the ears must know. These doors keep all of it **per world, per
scope, a version a row**, and a call's head row says which versions it ran on. The class declares
the contract, the tools, the state, the language, the doors, and nothing of this: the world is put
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
  "team":       { "holder": "", "version": 11, "author": "m_bruno", "note": null, "set_at": 1758200000, "config": { "llm": "anthropic/claude-haiku-4-5", "greeting": { "say": "Buenas…" } } },
  "production": null }
```

Each scope's **own** newest, or null when that scope set nothing; never the fallthrough, because
`if_version` is about the scope being written. `config` carries only the knobs the row set: `voice`,
`tts`, `tts_model`, `stt`, `llm` (the three model knobs take `vendor/model`, a vendor alone to keep
its own default model, or a model alone on whichever vendor is in use), `greeting` (`{say}` or
`{reply}`), `hangup {when}`, `turn {min_interruption_words, endpointing_ms, eot_threshold,
eager_eot_threshold, min_interruption_ms}` (the last is how long the caller must speak over the
agent before it stops; unset, livekit's own half second), `memory {remember, forget}`, `record`, `max_duration_s` (voice calls; `0` is
no limit), `llm_timeout_s` (how long a turn waits for the model's first word, livekit's retries
included; past it the turn ends unanswered and the call's log says `error {code: llm_timeout}`;
unset, there is no deadline of ours), `knowledge` (Markdown read whole into the static block of every call) and `bases
[{base, mode, k, min_score}]`.

## `PUT /v1/agents/{slug}/settings` — `pipeline` or `words`

```json
{ "config": { "voice": "amelia", "llm": "anthropic/claude-haiku-4-5" }, "if_version": 4, "note": "cleaner", "team": false }
```

The **whole** set for this scope. It is checked as a call would be built from it: the declaration
when an app holds the agent, a bare one otherwise, its lexicon in the scope, and the vendors on the
org's keys, so a vendor this build has no plugin for, a blank knob or an opening with both verbs is
`400` in its own sentence, and a vendor the box does not lend this org is refused here and not on
the next call. `409` when the scope is not at `if_version`. Answers the `GET` shape.

## `GET …/settings/history?team=&limit=` · `GET …/settings/diff?against=team|production` · `POST …/settings/rollback {version, team}`

One scope's versions, newest first, each with who set it and why. This key's scope's newest
against the team's or production's newest, with the fields that differ by name. One version copied
forward as the next one, `note: "rollback to v{n}"`; `404` for a version the scope never had.
Rollback takes `pipeline`.

## `GET /v1/calls/{call}/settings` — `calls`

The exact tuning and lexicon the call was built on, by the two version numbers its head row kept:
`{config_version, lexicon_version, config, lexicon}`, each row null where the scope had set nothing.

## The lexicon — `/v1/agents/{slug}/lexicon`

The agent's words: what the voice says in place of a word (`says`) and the words the ears must
know (`hears`). The class sets neither; each agent has a lexicon of its own, versioned per world
and scope as its settings are. `GET` answers `{world, yours, team, production}` of `{holder,
version, author, note, set_at, lexicon: {said: [{word, spoken}], heard: [string]}}`.
`PUT {lexicon, if_version, note, team}` is the whole lexicon, with the same `409`, and a blank
word refused. `GET /v1/agents/{slug}/lexicon/history?team=&limit=`. All three open to `pipeline`
or `words`.
