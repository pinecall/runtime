---
name: add-a-vendor
description: Add an LLM, STT or TTS vendor the way this tree does it — a catalog row, a key, and the four places that are still by hand. Use when adding a provider, a model row, a price, or a provider key.
---

# Adding a vendor

**Almost always, there is nothing to add.** `providers/catalog.py` already holds every vendor
livekit-agents ships a plugin for, `providers/plugin.py` builds any of them out of the plugin's own
constructor signature, and `_vendor_keys.py` holds a field for each. Check first:

```bash
uv run pinecall-runtime providers --does tts | grep -i <the vendor>
```

- **`ready`** — it runs today. Nothing to do.
- **`install`** — the row is right and the plugin is not on this box: add its extra to
  `[project.optional-dependencies] providers` in `pyproject.toml`, `uv lock`, deploy. Use
  `providers-big` when it drags a cloud SDK (boto3, azure-cognitiveservices-speech,
  google-cloud-*, onnxruntime) — that is a decision a box makes, never a default.
- **`no key`** — `printf '%s' <the key> | make secret NAME=<ITS_VARIABLE>` from the checkout, then
  `make restart`. The variable is in the `providers` table's own column.
- **not listed at all** — livekit added a plugin since. One row, below.

## A row in the catalog

`providers/catalog.py`, alphabetical, positional in the order the fields are declared:

```python
(Provider("acme", ("stt", "tts"), "ACME_API_KEY", ("acmeai",), "what a person is choosing"),)
```

- `does` is what the plugin's own `__all__` exports — `tests/providers/test_catalog.py` checks the
  row against the plugin and fails if they disagree.
- `env` is the variable **the plugin itself reads**, spelled the vendor's way. `None` when the
  vendor has no single-string credential (AWS's chain, Google's service account, RTZR's pair):
  BYOK then does not apply to it and nothing anywhere asks for one.
- An alias must **never** also be a model name of that vendor. A bare word that names a vendor IS
  the vendor at the pipeline door, so `sonic`, `octave`, `sonar`, `mist`, `nova` and `aura` are
  deliberately not aliases — every one of them is something a person could type meaning the model.

Then **one field in `_vendor_keys.py`**, named the variable lowercased. That rule is the whole of
the key wiring: `catalog.settings_field_of` reads it, the doctor reads it, the vault accepts the
vendor, `.env.example` and the box's units list it. `tests/providers/test_provider_keys.py` fails
while a row has no field.

## A tuned file — only when this build has an OPINION

A file under `providers/llm/`, `stt/` or `tts/` exists to **override a plugin default**: a model a
phone line should not wait for, an endpointing number, a model this build forbids. Five of them do.
If there is nothing to override, do not write one — the generic path is not a fallback, it is the
normal case, and a file that only forwards `model=` and `api_key=` is one more thing to go stale.

```python
"""Acme: one line saying what the plugin is, and the one default this repo overrides."""

from livekit.plugins import acme

from pinecall.providers.llm import VENDORS
from pinecall.providers.registry import Asked, Chat, a_key

DEFAULT_MODEL = "acme-fast"  # the plugin's own is acme-large (acme/llm.py:67)


@VENDORS.registers("acme")
def build(asked: Asked) -> Chat:
    return acme.LLM(model=asked.model or DEFAULT_MODEL, api_key=a_key("acme", asked))
```

## The four places that are still by hand

1. `providers/knocks.py` — the vendor's cheapest authenticated GET, so `doctor` knocks and
   `make deploy` refuses a dead key by name. **Only a URL you have opened with a live key and
   watched answer 200.** A guessed one 404s a perfectly good key and refuses a box that was fine;
   a vendor with no row is simply not knocked, which is honest.
2. `providers/prices.py` — a row only where you read the vendor's own page, with `AS_OF` beside
   it. Everything else is priced by `published_prices.json`
   (`scripts/refresh-prices`, mahimailabs/voice-prices), and the hand-read table wins where the
   two disagree — Soniox is the row that says why.
3. `infra/box/*.service` and `WORKER_CREDENTIALS` in the `Makefile` — one `ImportCredential=` line
   each and one name, when the vendor has an env.
4. `tests/conftest.py` — a dead sentinel, if a suite needs to construct that vendor.

Then `scripts/generate-env-example` (a test fails while it drifts), and `CHANGELOG.md`.

## What the tests will refuse

- A vendor SDK import anywhere but `providers/` (`tests/test_isolation.py`, `VENDOR_SDKS`).
- A catalog row that disagrees with its plugin's `__all__` (`tests/providers/test_catalog.py`).
- A catalogued vendor with no settings field (`tests/providers/test_provider_keys.py`).
- A second definition of a default model, a header name, a URL: `grep` first
  (`tests/test_one_definition_per_thing.py`).
- Two files in one directory one letter apart (`openai.py` next to `openia.py`).
- A public name added to `pinecall.providers` without editing `tests/test_the_public_surface.py`.

## Verify

```bash
scripts/format && scripts/lint && uv run pytest -m unit -q        # three times: it is shuffled
uv run pinecall-runtime providers --does stt                      # the row, and what it wants
uv run pinecall-runtime doctor                                    # the knock, with a real key in .env
```

An STT vendor is verified on a spoken call (`debug-a-call`): the transcript entries name the
vendor in `metrics.stt`. A TTS vendor: `tts_node_ttfb` on `turn.agent`.
