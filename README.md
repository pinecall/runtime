# pinecall

**Work in progress.** Rewriting it from scratch since Sunday, 2026-09-27.

Voice AI that runs where you do. Pinecall is a self-hosted runtime for voice agents: phone
calls, web voice, chat and WhatsApp, on your box, your carrier, your models.

**This repository is the runtime**: the gateway that answers the doors and the worker that runs
the calls, one Python package, one wheel, on [LiveKit](https://livekit.io). The agents themselves
are written with the SDK, in [pinecall/agents](https://github.com/pinecall/agents) (TypeScript) or
the Ruby SDK, and talk to this runtime over the wire `pinecall/wire/` declares. Nothing here is
imported by an agent.

```
git clone https://github.com/pinecall/runtime && cd runtime
uv sync                      # Python 3.12, one venv, every dev tool
make check                   # the rules and every suite that needs no database
make test                    # every suite, on a throwaway Postgres (colima or docker)
```

`docs/architecture.md` is the map: what each folder does and what it may import.
`docs/glossary.md` defines the ten words of the domain. `docs/conventions.md` says how a file is
written. `examples/` walks an inbound number, an outbound call and a chat with `curl`.

Why from scratch: v1 has been answering real calls for real customers in production. v2 is what
those calls taught us, written again to run anywhere: a cloud box, a rack in your office, a
network that talks to nobody. Your calls, your data, your models, and nothing leaves unless you
send it.

What the rewrite gives you:

- **Any vendor, no list.** Every LiveKit plugin works out of the box. Bring your own key, or use
  the box's. Nothing in the code decides which models you may run.
- **One deployment, two worlds.** Production and sandbox on one gateway and one database, with a
  worker fleet per world. One login, one key, a switch in the console.
- **Your agents do not change.** Same wire, same doors. Everything behind them is new.
- **A wheel, not a checkout.** A deploy is one file copied to the box and three units restarted.
- **Three hops from a door to its effect,** enforced by tests the commit hook runs. No registries,
  no layers.

Running today: the call's log, voice and text on one session, tenants and sign-in, the gateway
and the worker, numbers from any carrier account, outbound calls, WhatsApp. Coming: local models end to
end (the model, the ears and the voice on your own hardware), retrieval and memory, evals, the
operator CLI, the docs.

## Working on it

```
make check      the rules and the suites that need no database
make test       every suite, on a local Postgres in colima (T=tests/log for one folder)
make hooks      install the pre-commit hook (runs `make check`)
make deploy     the console built in, a wheel, released on the box, the live suite, the journal
```

Python 3.12 and `uv`; colima for the suites that need Postgres (`make db`). Nothing runs LiveKit
locally. `docs/the-environment.md` names every variable. Apache-2.0.
