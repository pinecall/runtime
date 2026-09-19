# An agent's settings — `/v1/agents/{slug}/settings` and `/v1/lexicon`

What an agent runs on is the org's, not the class's: which vendors and models, how a call opens and
ends, how a turn is cut, what is remembered, which knowledge bases it reads — and the org's words,
how the voice says a brand and what the ears must know. These doors keep all of it **per world, per
corner, a version a row**, and a call's head row says which versions it ran on. They take the org's
own API key, as every tenant door does. The class still declares what it declares; what the org set
is laid over it at the one place every session is built (`providers/tuning.py`), and a class field
the org never set stands as the class wrote it.

**Two kinds of key open them.** A key that opens `pipeline` — a developer's, an admin's — may set
everything. A key that opens `words` — a supervisor's, a manager's — may set the opening's words,
the lexicon and what is remembered, and is refused the vendors, the models, the cut of a turn and
the bases **by name**, with the scope it lacks: `llm: the pipeline's, and this key does not open
pipeline: it opens …`. Their set carries those fields over untouched from what stands.

**Three corners.** In the sandbox a person's key has a corner of its own: what they set is theirs
until they promote it, and a colleague's next call does not hear it. `team: true` writes the org's
own corner instead, which every corner falls back to — the rule 0021 gave knowledge, for the same
reason: nobody joins a team to an agent with no voice. A key that names nobody (CI's) and a key that
holds no agent (a supervisor's: no session ever resolves in their corner) write the org's own
corner always. Production has one corner, the org's own, and **is written by promote alone**.

**Versions.** Every set is a new row; nothing is updated, nothing deleted. The body carries the
version it was read at, and a corner that moved on since answers `409` with where it is now — two
people saving from two screens never write over each other in silence. A rollback is a new version
equal to an old one, and the history says so.

### `GET /v1/agents/{slug}/settings` — `pipeline` or `words`

```json
{ "world": "sandbox",
  "yours":      { "holder": "m_ana", "version": 4, "author": "m_ana", "note": "carolina sounds flat", "set_at": 1758300000, "config": { "voice": "amelia" } },
  "team":       { "holder": "", "version": 11, "author": "m_bruno", "note": null, "set_at": 1758200000, "config": { "voice": "carolina", "llm": "anthropic/claude-haiku-4-5", "greeting": { "say": "Thanks for calling…" } } },
  "production": { "holder": "", "version": 11, "author": "m_ana", "note": "promoted from sandbox v11", "set_at": 1758210000, "config": { "…": "…" } } }
```

Each corner's **own** newest, or null when that corner set nothing — not the fallback, because
`if_version` is about the corner being written. `yours` is null on a production key and on a key
that holds no corner. `config` is `TuningBody` (`rest.json`): `voice`, `tts`, `tts_model`, `stt`,
`llm` (the three model knobs take `vendor/model`, a vendor alone to keep its own default model, or a
model alone to keep whichever vendor is in use), `greeting` (`{say}` or `{reply}`, one of the two),
`hangup {when}`, `turn {min_interruption_words, endpointing_ms}`, `memory {remember, forget}`,
`knowledge [{base, mode, k, min_score}]`.

### `PUT /v1/agents/{slug}/settings` — `pipeline` or `words`

```json
{ "config": { "voice": "amelia", "llm": "anthropic/claude-haiku-4-5" }, "if_version": 4, "note": "cleaner on the phone", "team": false }
```

The **whole** set: a knob left out is not set, and what the app declared stands for it. A knob that
is present but **blank is refused** with `400` — an empty voice once reached the vendor and a whole
line of calls went out silent. A vendor this build has no file for, an ElevenLabs model it will not
run, a voice nobody curated, an opening with both verbs: `400`, each in its own sentence, and
nothing is written. `409` when the corner is not at `if_version`. `403` on a production key, naming
the promote door; `403` on a `words` key that moved a pipeline field, naming the field. Answers the
`GET` shape.

### `GET …/settings/history?team=&limit=` · `GET …/settings/diff?against=team|production` · `POST …/settings/rollback {version, team}`

One corner's versions, newest first, each with who set it and why. What this key's corner reads
(its own newest, else the org's own) against another corner's newest, with the fields that differ
by name. One version copied forward as the next one, `note: "rollback to v{n}"`; `404` for a
version the corner never had. Rollback takes `pipeline`, and works on a production key: what it
copies was promoted there once.

### `POST …/settings/promote {to, goldens?, note?}` — `pipeline`

Two hops, one verb. **`to: "team"`**: the key's own corner's newest becomes the org's own corner's
next version, and every colleague's next call reads it. **`to: "production"`**, from a sandbox key
only: the agent's goldens are driven first, through `POST /v1/evals/run`'s own machinery, against
the app serving this key's sandbox corner under the team's settings — the goldens travel in the body,
as that door takes them, because the gateway keeps none (`pinecall agent promote --prod` reads them
where `pinecall test` does). Every one holds → the team's newest becomes production's next version,
`note: "promoted from sandbox v{n}"`, and the answer names the eval run. Otherwise `409` naming the
goldens that did not hold and the run to read, and production stays as it is. `400` with no goldens;
`409` when the key's own corner sets the agent differently from the team's, so that what the goldens
test is what production will run; `404` with nobody serving the sandbox. Nobody writes production
any other way.

```json
{ "world": "production", "holder": "", "version": 12, "run": "run_3f9a1c2b7d4e" }
```

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
`409`, the same `403` on a production key, and a blank word refused. `GET /v1/lexicon/history`.
`POST /v1/lexicon/promote {to, note}` makes the two hops with **no goldens between them**: a word
said wrong is what the agent already does, and the person who hears it is the one this door is for.
All four open to `pipeline` or `words`.

## The six-knob door, kept one release

`PUT /v1/agents/{slug}/pipeline/overrides` ([pipeline-api.md](pipeline-api.md)) still answers, for
the console's Pipeline screen: its six knobs become the next version of the key's corner's tuning,
the six replaced and the rest kept, `note: "pipeline/overrides"`, through the same store — nothing
here is a second truth. On a production key it writes production, as the table it replaced did;
the settings door refuses that, and this one goes with the screen that uses it.
