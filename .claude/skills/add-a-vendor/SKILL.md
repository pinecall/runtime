---
name: add-a-vendor
description: Add an LLM, STT or TTS vendor the way this tree does it — one file, one registration line, and the eight places a key's name has to appear. Use when adding a provider, a model row, or a provider key.
---

# Adding a vendor

A vendor is **one file under `providers/<modality>/`** and one registration line; the package is
read whole, so the file being there IS the registration (`providers/registry.py`, `Vendors`).
Nothing of ours sits between the session and the plugin: livekit's plugin is the adapter, it
streams, measures and names the model for the price table.

```python
"""Acme: one line saying what the plugin is, and the one default this repo overrides."""

from livekit.plugins import acme

from pinecall.providers.llm import VENDORS
from pinecall.providers.registry import Asked, Chat, a_key


@VENDORS.registers("acme")
def build(asked: Asked) -> Chat:
    """One row, one plugin."""
    return acme.LLM(model=asked.model or DEFAULT_MODEL, api_key=a_key("acme", asked))
```

## The eight places a key's name appears — all of them, or the doctor and the box disagree

1. `_settings.py` — `acme_api_key: str | None = Field(default=None, validation_alias="ACME_API_KEY", …)`,
   the vendor's OWN variable name, in the provider-keys block.
2. `scripts/generate-env-example` — rerun; `tests/test_env_example.py` fails until you do.
3. `providers/registry.py` `_the_boxes_key` — the field the vendor's key is read from.
4. `providers/knocks.py` — the vendor's cheapest authenticated GET (a listing, the account),
   so `pinecall-runtime doctor` knocks and `make deploy` refuses a dead key by name.
5. `cli/doctor/verbs.py` `PROVIDER_KEYS` — under its role (`llm` · `stt` · `tts`).
6. `tests/conftest.py` — a dead sentinel for it: unit tests construct every vendor with no key.
7. `infra/box/pinecall-gateway.service` and `pinecall-worker.service` — an `ImportCredential=`
   line each; and `WORKER_CREDENTIALS` in the root `Makefile`, so `make worker-secrets` copies it.
8. `infra/box/README.md` — the credential table row, and `types/provider_keys.py` if an org may
   bring its own (BYOK) — then `orgs provider-key` and the vault accept the vendor name.

Then `pyproject.toml`: the plugin extra under `[project.optional-dependencies] runtime`
(`livekit-agents[…,acme]`), `uv lock`.

## Prices

`providers/prices.py`: one `Price` row per model id exactly as livekit's usage names it, in EUR
with `AS_OF` and the rate beside it; media vendors in `MEDIA_PRICES`. A model with no row costs
nothing in `call.summary` and the test says so — add the row in the same commit.

## What the tests will refuse

- A vendor SDK import anywhere but `providers/` (`tests/test_isolation.py`, `VENDOR_SDKS`).
- A second definition of a default model, a header name, a URL: `grep` first
  (`tests/test_one_definition_per_thing.py`).
- Two files in one directory one letter apart (`openai.py` next to `openia.py`).
- A public name added to `pinecall.providers` without editing `tests/test_the_public_surface.py`.

## Verify

```bash
scripts/format && scripts/lint && uv run pytest -m unit -q        # three times: it is shuffled
uv run pinecall-runtime doctor                                    # the knock, with a real key in .env
```

An STT vendor is verified on a spoken call (`debug-a-call`): the transcript entries name the
vendor in `metrics.stt`. A TTS vendor: `tts_node_ttfb` on `turn.agent`.
