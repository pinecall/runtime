# provider keys — managed is the absence of a row, and BYOK is one row

Written 2026-09-08, the card after orgs. Until it, every call of every tenant on a box ran on the
five keys the process read at startup — `ANTHROPIC_API_KEY`, `SONIOX_API_KEY`, `ELEVEN_API_KEY` and
the other two. That is still the default, still the whole of a self-hosted install, and still what
happens when nothing here is used. What this card adds is the other half of the same mechanism: a
tenant who has their own account with a vendor puts their key in a row, and their calls — only
theirs, only that vendor — run with it.

There is one mechanism and not two. `providers/registry.py:a_key` is the whole of it:

```python
def a_key(vendor: str, asked: Asked) -> str:
    key = asked.keys.get(vendor) or _the_boxes_key(vendor, asked.settings)
    if not key:
        raise NoProvider(NO_KEY.format(vendor=vendor))
    return key
```

Everything above that function — `pipeline_for`, the five vendor files, the session, the bridge —
is unchanged in what it does and knows nothing about which of the two keys it got. "Managed" is
not a code path; it is `asked.keys` being empty.

## A row per (org, vendor), and nothing else in the table

`provider_keys(org, vendor, ciphertext, set_at)`, primary key `(org, vendor)`, `org` referencing
`orgs(id) ON DELETE CASCADE`. Three consequences worth stating:

- **Rotation is the upsert.** A tenant who changes their key sets it again and the previous
  ciphertext is overwritten. There is deliberately no history of a secret in this table: a row that
  kept the last three keys would be three keys to steal instead of one.
- **`orgs rm` takes the keys with it.** The cascade is the reason the foreign key is there. An
  operator who removes a tenant must not leave that tenant's secret in the database.
- **The vendor is a word this build knows.** `domain/provider_keys.py:VENDORS` is the list, and a
  door refuses anything else with `400` and the list in the sentence. A key stored under `11labs`
  would be a key nobody ever reads, which is worse than a refusal.

`registry.KEY_OF` says which settings field each of those vendors falls back to; a test asserts
`tuple(sorted(KEY_OF)) == VENDORS`, so the two cannot drift. They are two facts — which vendors a
tenant may bring a key for, and which variable the box reads for each — and the test is what keeps
them one.

## Fernet with a key from the environment, and not a KMS

The row holds a Fernet token under `PINECALL_VAULT_KEY`, generated once on the box and kept in
the box's credstore beside the ops key, a systemd credential like every secret there. Three reasons it is not a cloud KMS:

- **A self-hosted box has no KMS.** The same image runs on our machine and on a customer's, and
  the repo has no fork. A design that needed AWS KMS would be a design that only we could run,
  which is the one thing `docs/decisions/box.md` says we do not build.
- **What it buys is exactly what it claims.** A stolen database dump is not a stolen tenant: the
  key that opens the rows is in the environment of the process, not in a column beside them. It
  does not defend against somebody who already has the box, and no envelope scheme would.
- **The cloud's vault is the private layer's.** `pinecall/cloud` — signup, plans, Stripe, the
  managed-versus-BYOK question as a *product* — keeps whatever it keeps and pushes a row here
  through `PUT /v1/ops/orgs/{org}/provider-keys/{vendor}`. The runtime is the thing that runs
  calls; it is not the thing that decides who pays for which key.

A runtime given no `PINECALL_VAULT_KEY` has no vault at all: `vault_for` answers `None`, the three
operator doors answer `503 no PINECALL_VAULT_KEY: this runtime cannot keep a tenant's key`, and
the worker's door answers `{"keys": {}}` — which is true, and which is why a box without a vault
key is a complete install and not a broken one. A 503 there would end calls that were going to run
on the box's own keys anyway.

## One door reads a key back, and it is the worker's

`GET /v1/agents/{slug}/provider-keys`, on the org's own API key, mirroring
`GET /v1/agents/{slug}/config` line for line: the registry must hold the slug for that key's org,
and a slug somebody else holds is `404` in the same sentence. It answers
`{"keys": {"elevenlabs": "sk…"}}` — the org's own keys, decrypted — and it is **the only response
body in the runtime that carries a provider key**, the way `POST /v1/ops/orgs/{org}/keys` is the
only one that carries an API key (`docs/decisions/keys.md`).

Everything else is names: `GET /v1/ops/orgs/{org}/provider-keys` answers `{"vendors": [...]}`,
never a value and not even a prefix. What is worth knowing about a stored key is whether it is
there; anything more is a way to read a secret back, and there is none — the same rule the API
keys table has followed since ms-2.

`tests/log/test_no_provider_key_in_the_log.py` is what holds this. It stores a key of the shape
`sk-LEAKCANARY-…`, replays the golden call into the store, and then scans every row of the call's
log and the agent's own, plus the body of every door read with an org's API key — the routes, the
agents, the config, the pipeline, the calls, the sessions, the events, the state — for the canary.
None may carry it, and the last line of the test asserts the worker's own door does, so the test
cannot pass by never having stored anything. Since a written call runs on the org's key too, a
third test opens a chat session for that same org and scans both ways again — the call's own log
and every door that reads it back — after asserting that the session really was handed the canary.

## The worker asks a second door, in the same wait

`worker/entry.py` had one round trip before the session: `config = await gateway.agent(slug)`.
It now has two, in one `asyncio.gather` beside the existing one:

```python
config, keys = await asyncio.gather(
    worker.gateway.agent(route.agent), worker.gateway.provider_keys(route.agent)
)
```

The card asked for no second round trip. Two requests in parallel are the same wait as one — the
caller is already in the room and the clock that matters is wall time, not requests — and the
alternative, folding the keys into `/config`, would have put a provider key into the one body the
console, the pipeline screen and every future reader of an agent's configuration already read.
One door that answers keys is a door that can be audited; a field on a shared body is not.

`Kit` becomes `Callable[[AgentConfig, ProviderKeys], Pipeline]`. The box's keys are the process's
and are closed over once by `kit_for`; the org's are the **call's** and are an argument. That is
not style: one worker process serves many calls at once, and a tenant's key held in the closure
would be one tenant's key in another tenant's call. `tests/worker/test_kit.py` builds two
pipelines from one Kit and asserts the plugins were handed different keys.

## A written call asks the same question, at the door

Added 2026-09-09, the card after this one. `models_for(settings)` was process-wide, so a chat
socket and an eval run answered on the box's LLM key even for an org that had brought its own —
the one hole this feature had left. `Models` is now
`Callable[[Model | None, ProviderKeys], Chat]`, the same split `Kit` already made: the box's keys
are the process's and are closed over once, the org's are the **session's** and are an argument.

Four doors ask `orgs/vault.py:keys_brought_by(vault, org)` — one function, so the
"a runtime with no vault holds nobody's key" answer is written once and never twice:

| door | whose org | when it asks |
|---|---|---|
| `GET /v1/agents/{slug}/provider-keys` | the key's | the worker, before a voice call |
| `WS /v1/chat` | the app holding the agent | as the socket opens, before it is accepted |
| `POST /v1/evals/run` | the app holding the agent | once, as the run opens |
| `POST /v1/evals/caller`, `POST /v1/evals/voice` | the key's | per improvised turn |

Asked at the door and never held: there is no table of orgs to go stale, so a key rotated a
minute ago is the key the next call runs on — `tests/session/text/test_the_orgs_own_key.py` opens
two calls around an upsert and pins exactly that. The run asks once rather than per golden because
a run is one org's and one session, which is the same grain the chat socket asks at.

The **judge** stays on the box's key, deliberately (`evals/judges.py:a_judge`): the conversation
is the tenant's, the judgment is the platform's own measurement of it, made in the same words for
every org on the box. It is also the only way one rule holds in both places a call is judged — a
run has the org in hand, a text call judging itself at hang-up does not. What judging may spend is
a ceiling and not a key (`judge_ceiling_eur`, docs/decisions/scoring.md).

One import moved to make this possible: `orgs/admission.py` reads `Logs` as a type and now imports
it under `TYPE_CHECKING`. Importing it for real made that module import the log's whole PACKAGE,
whose `__init__` pulls a route module that reads `AdmissionDep` back — a cycle that had been
latent since the two packages met, and that `import pinecall.gateway.orgs.admission` raised on
long before this card. Reaching the vault from `api/evals/` is what made it reachable.

## What was deliberately not built

**Anything priced.** Whether an org has a row is the fact a cloud bills differently from; the
runtime writes provider cost into the log as it always has (`prices.py`, informational) and does
not know what a plan is. `docs/decisions/orgs.md` says the same about quotas.

**A retry on a wrong key.** A tenant's bad key fails the call the way a bad key in the box's
environment already does — `is_a_dead_end` sees the vendor's 401 and ends the call once, with one
entry (`docs/decisions/providers.md`). Retrying a credential that cannot become correct is the
loop ms-3 spent an afternoon on.

**Reading a key back.** No verb, no door, no CLI output. `orgs provider-key list` prints vendor
names; `set` reads the key from **stdin and never from a flag**, because argv is visible in `ps`
to every user on the box and a key pasted as an argument is a key in a shell history.
