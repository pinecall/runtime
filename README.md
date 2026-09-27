# pinecall

The Pinecall voice-AI runtime: the gateway and the worker, on LiveKit. One package, one wheel.

```
make check      the rules and the suites that need no database
make test       every suite, on the sandbox database through an SSH tunnel
make hooks      install the pre-commit hook (runs `make check`)
```

`tests/rules/` is what the commit hook refuses: every rule is a test, and every rule is proven by
a fixture that breaks it. The runtime needs Python 3.12 and `uv`; nothing runs a local Postgres or
LiveKit. `docs/the-environment.md` names every variable.
