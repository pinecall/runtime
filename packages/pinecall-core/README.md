# pinecall-core

The shapes a Pinecall runtime speaks and the points a policy plugs into, installable without the
runtime. Three modules of the `pinecall` namespace:

| module | what |
|---|---|
| `pinecall.types` | the entities — `Org`, `Member`, `Quotas`, `AgentConfig`, scopes, routes — pure data, no IO |
| `pinecall.extensions` | the named points a package installed beside the runtime fills (`Extensions.admitted`), and the loader that reads `PINECALL_EXTENSIONS` |
| `pinecall.errors` | `PinecallError`, the root of every error the runtime raises on purpose |

It depends on the standard library alone. The runtime (`pinecall`, the repository's root)
depends on it; so does a package that charges for a box (`docs/charging-for-it.md`) — which is
why it is its own distribution: a policy imports these three and not FastAPI, LiveKit and a
database driver.

`pinecall` is a namespace package: neither this distribution nor the runtime ships a
`pinecall/__init__.py`, and both install side by side into the same `pinecall`.

## Tests

From the repository's root, as `scripts/test` runs them:

```console
$ uv run pytest -c packages/pinecall-core/pyproject.toml --rootdir packages/pinecall-core packages/pinecall-core/tests
```

## Releases

A `core-v*` tag publishes it (`.github/workflows/release.yml`); the version is this directory's
`pyproject.toml`, and the runtime asks for it with a range.
