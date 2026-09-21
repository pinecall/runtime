# An agent's settings — `/v1/agents/{slug}/settings` and `/v1/lexicon`

What an agent runs on is the org's, not the class's: which vendors and models, how a call opens and
ends, how a turn is cut, what is remembered, which knowledge bases it reads — and the org's words,
how the voice says a brand and what the ears must know. These doors keep all of it **per world, per
corner, a version a row**, and a call's head row says which versions it ran on. They take the org's
own API key, as every tenant door does. The class declares the contract — the tools, the state,
the language, the doors — and nothing of this: the world is put on the declaration at the one place
every session is built (`providers/tuning.py`), and a knob the org never set is the runtime's
default.

**Two kinds of key open them.** A key that opens `pipeline` — a developer's, an admin's — may set
everything. A key that opens `words` — a supervisor's, a manager's — may set the opening's words,
the lexicon and what is remembered, and is refused the vendors, the models, the cut of a turn and
the bases **by name**, with the scope it lacks: `llm: the pipeline's, and this key does not open
pipeline: it opens …`. Their set carries those fields over untouched from what stands.

**Three corners.** In the sandbox a person's key has a corner of its own: what they set is theirs
until they set it with `team: true`, and a colleague's next call does not hear it. `team: true` writes the org's
own corner instead, which every corner falls back to — the rule 0021 gave knowledge, for the same
reason: nobody joins a team to an agent with no voice. A key that names nobody (CI's) and a key that
holds no agent (a supervisor's: no session ever resolves in their corner) write the org's own
corner always. Production has one corner, the org's own, and is **set directly** by a request that
runs there (`pinecall-env: production`): a person with production access, or a production server's
token. There is no promote: the goldens run in CI before a deploy, not at this door.

**Every knob falls through on its own.** A corner supplies the knobs it actually set, and each knob
it did not set is read from the corner below it — never the whole row of the first corner that has
one. Setting a voice of your own does not disconnect you from the team's model:

| set in | `voice` | `llm` | `stt` |
|---|---|---|---|
| yours | `nova` | | |
| the team's | | `anthropic/claude-sonnet-4-5` | `soniox` |
| **what the next call runs** | `nova` | `anthropic/claude-sonnet-4-5` | `soniox` |

A knob is **set** when it is in the row, which is exactly what leaving it out of a `PUT` decides. A
knob set to a falsy value is set and wins over the corner below — `turn {endpointing_ms: 0}`,
`hangup {when: ""}`, `memory {remember: [], forget: []}`, `bases []` — and only an ABSENT knob
falls through. An empty row therefore supplies nothing and is invisible to resolution: clearing
your corner is a version of its own that blanks nothing under it. Every knob says it the same way,
`bases` with the rest: `"bases": []` is **no base, and do not inherit one** — it is written and it
wins over the corner below — while leaving `bases` out reads the corner below's. The version a
call records and a diff answers with is the nearest corner that supplied a knob.

**Versions.** Every set is a new row; nothing is updated, nothing deleted. The body carries the
version it was read at, and a corner that moved on since answers `409` with where it is now — two
people saving from two screens never write over each other in silence. A rollback is a new version
equal to an old one, and the history says so.

### `GET /v1/agents/{slug}/settings` — `pipeline` or `words`

```json
{ "world": "sandbox",
  "yours":      { "holder": "m_ana", "version": 4, "author": "m_ana", "note": "carolina sounds flat", "set_at": 1758300000, "config": { "voice": "amelia" } },
  "team":       { "holder": "", "version": 11, "author": "m_bruno", "note": null, "set_at": 1758200000, "config": { "voice": "carolina", "llm": "anthropic/claude-haiku-4-5", "greeting": { "say": "Thanks for calling…" } } },
  "production": { "holder": "", "version": 11, "author": "m_ana", "note": "calmer greeting", "set_at": 1758210000, "config": { "…": "…" } } }
```

Each corner's **own** newest, or null when that corner set nothing — not the fallback, because
`if_version` is about the corner being written. `yours` is null on a request in production and on a key
that holds no corner. `config` is `TuningBody` (`rest.json`): `voice`, `tts`, `tts_model`, `stt`,
`llm` (the three model knobs take `vendor/model`, a vendor alone to keep its own default model, or a
model alone to keep whichever vendor is in use; the model names a screen offers are the `models`
map that `GET /v1/providers` and the pipeline report carry, keyed `<modality>/<vendor>` with each
vendor's default first — a vendor with no entry there runs its own default and takes no model
name), `greeting` (`{say}` or `{reply}`, one of the two),
`hangup {when}`, `turn {min_interruption_words, endpointing_ms}`, `memory {remember, forget}`,
`record` — whether the agent's calls keep their audio, the one boolean here, and the reason it is
a boolean and not a flag: `false` is a corner saying no and is heard over the corner below, while
leaving it out is a corner that never said and falls through to one that did (unset anywhere, a
call is recorded) —
`knowledge` — what the agent knows by heart, in Markdown: the business as the org describes it,
read whole into the static knowledge block of every call, cached ahead of everything, and set by
the floor (`words`) without a deploy — and `bases [{base, mode, k, min_score}]`, the RAG: every
base the agent reads in this world, each with how a turn reads it (`mode` `retrieved` or `tool`,
`k`, `min_score`); a turn's search fans out over all of them ([gateway-api.md](gateway-api.md),
Knowledge). `[]` is a corner that reads NO base and is not the same as leaving `bases` out, which
reads the corner below's. `pinecall docs attach <base>` writes that list; the text is the console's
textarea, or `pinecall agent knowledge edit`.

### `PUT /v1/agents/{slug}/settings` — `pipeline` or `words`

```json
{ "config": { "voice": "amelia", "llm": "anthropic/claude-haiku-4-5" }, "if_version": 4, "note": "cleaner on the phone", "team": false }
```

The **whole** set: a knob left out is not set in THIS corner, and the corner below it supplies the
knob — the runtime's default when none of them sets it either. A knob that
is present but **blank is refused** with `400` — an empty voice once reached the vendor and a whole
line of calls went out silent. A vendor this build has no file for, an ElevenLabs model it will not
run, a voice nobody curated, an opening with both verbs: `400`, each in its own sentence, and
nothing is written. `409` when the corner is not at `if_version`. In production the set lands in the org's own corner,
whatever `team` says; `403` on a `words` key that moved a pipeline field, naming the field. Answers
the `GET` shape.

### `GET …/settings/history?team=&limit=` · `GET …/settings/diff?against=team|production` · `POST …/settings/rollback {version, team}`

One corner's versions, newest first, each with who set it and why. What this key's corner reads
(knob by knob down its corners) against another corner's newest, with the fields that differ
by name. One version copied forward as the next one, `note: "rollback to v{n}"`; `404` for a
version the corner never had. Rollback takes `pipeline`, and works in production: what it copies
was set there once.

### `GET /v1/calls/{call}/settings` — `calls`

The exact tuning and lexicon the call was built on, by the two version numbers its head row recorded
when it opened (`config_version`, `lexicon_version`, 0037): `{config_version, lexicon_version,
config: TuningRow | null, lexicon: LexiconRow | null}`. A reviewer of Tuesday's call sees Tuesday's
voice, model and words, whatever changed since. Null is a corner that had set nothing.

## The lexicon — `/v1/lexicon`

The org's words, shared by every agent of it and merged into each one's own `says` and `hears` —
the org's word wins where both say the same one. `GET` answers `{world, yours, team, production}`
of `LexiconRow`, each `{holder, version, author, note, set_at, lexicon: {said: [{word, spoken}],
heard: [string]}}`. `PUT {lexicon, if_version, note, team}` is the whole lexicon, with the same
`409`, written in the request's world as the settings are — production's org's-own corner directly —
and a blank word refused. `GET /v1/lexicon/history`. All three open to `pipeline` or `words`.
