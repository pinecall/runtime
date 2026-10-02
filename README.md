# pinecall

Voice AI that runs where you do. Pinecall is a self-hosted runtime for voice agents: phone
calls, web voice, chat and WhatsApp, on your box, your carrier, your models.

This repository is the runtime: the gateway that answers the doors and the worker that runs the
calls, one Python package, one wheel, on LiveKit. It runs Pinecall's own production
(`box.pinecall.io`) and its sandbox on one box. The agents are written with the SDK, in
[pinecall/agents](https://github.com/pinecall/agents) (TypeScript) or the Ruby SDK, and talk to
this runtime over the wire `pinecall/wire/` declares. Nothing here is imported by an agent.

On a machine with Ubuntu 24.04 and its names pointed at it, the runtime makes it a box by itself:

```console
$ curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
$ sudo uvx --from pinecall pinecall-runtime box up --domains voice.example.com,sandbox.example.com
```

`docs/from-zero.md` takes it from there to a caller heard. To work on the runtime itself:

```console
$ git clone https://github.com/pinecall/runtime && cd runtime
$ uv sync                      # Python 3.12, one venv, every dev tool
$ make check                   # the rules and every suite that needs no database
$ make test                    # every suite, on a throwaway Postgres (colima or docker)
```

`docs/architecture.md` is the map: what each folder does and what it may import.
`docs/glossary.md` defines the ten words of the domain. `docs/conventions.md` says how a file is
written.

## What it is

- **Any vendor, no list.** Every LiveKit plugin works out of the box: the model, the ears and the
  voice are whatever is installed and keyed. Bring your own key, or use the box's. Nothing in the
  code decides which models you may run.
- **One deployment, two worlds.** Production and sandbox on one gateway and one database, each
  with its own worker fleet and its own name. One login, one key, a switch in the console.
- **The log is the product.** Every call is an append-only log: what was said, what the model
  read, every tool call and its answer, every measure LiveKit took, the judges' verdicts at
  hang-up. The console, the CLI and the API read the same log.
- **Compliance is built in, and provable.** Calling hours and a frequency cap by destination,
  consent on file and a do-not-call list the agent honours with one call, the AI disclosure and
  the recording notice said before the greeting and logged, judges that settle by code whether a
  call identified the business and honoured a "stop". Erasure through one path the append-only
  trigger admits, with a trail; retention per org; an export in one request; an access log of who
  read what; call records kept 24 months for a carrier's traceback; encrypted nightly backups.
- **A wheel, not a checkout.** A deploy is one file copied to the box and three units restarted.
  Migrations run before the gateway comes up; a deploy never cuts a call.
- **Three hops from a door to its effect,** enforced by tests the commit hook runs. No
  registries, no layers, no vendor named in code.

## What runs

The call's log; voice and text on one session; tenants, people, keys and sign-in (password,
one-use codes, SSO); numbers from any carrier account and outbound calls with their guards;
WhatsApp; knowledge bases and contact memory looked up in the call; goldens, simulated callers and
judges; every operator door and verb; a fleet per world that grows through a cloud script; the
console served at the box's names. And open models end to end, below.

## Open models, on your own GPU

Nothing in the runtime names a vendor, so a box can run the whole call on open models: the ears,
the model, the voice and the embedder on one NVIDIA card, no cloud vendor in the call and nothing
a vendor bills. `infra/models/` is that stack, as data:

| stage | model | server |
|---|---|---|
| ears | Whisper large-v3-turbo (99 languages) | Speaches, OpenAI-shaped |
| end of turn | Smart Turn v3 (8 MB) | the worker's CPU |
| model | Google Gemma 4 12B | Ollama, OpenAI-shaped |
| voice | Kokoro-82M | Kokoro-FastAPI, OpenAI-shaped |
| memory, knowledge | bge-m3 | Ollama, OpenAI-shaped |

Three steps on a machine with the GPU (12 GB or more), Docker and the NVIDIA container toolkit;
no account anywhere:

```console
$ curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/compose.yaml
$ curl -fsSLO https://raw.githubusercontent.com/pinecall/runtime/main/infra/models/providers.json
$ docker compose up -d                  # the first start pulls ~10 GB of models
```

Then, on the box, the providers row that points at them — `MODELS_HOST` is the address the
servers listen on:

```console
$ sed 's/MODELS_HOST/127.0.0.1/g' providers.json > /tmp/providers.json
$ sudo pinecall-runtime providers seed /tmp/providers.json
```

Measured on an RTX 3090, over a five-turn call: about 1.6 s from the caller's last word to the
agent's first, the ears exact on every turn. `docs/the-open-stack.md` is the walk, what each line of the row means, the numbers
and what it does not do yet. Pinecall's own box runs on cloud vendors; this stack is for the box
you run.

## Where to start reading

- `docs/from-zero.md` walks a box to its first call; `docs/a-box-in-production.md` is what a
  box keeps, backs up and forgets.
- `docs/protocol/gateway-api.md` is every door a tenant's code knocks at, and
  `docs/protocol/every-door.md` all of them in one table.
- `docs/protocol/numbers.md` is the phone side: carriers, numbers, dials and their guards.
- `docs/the-runtime-cli.md` is the operator's terminal; `docs/the-environment.md` names every
  variable.
- `docs/wire/` describes every frame, event and command on the wire.

## Working on it

```
make check      the rules and the suites that need no database
make test       every suite, on a local Postgres in colima (T=tests/log for one folder)
make hooks      install the pre-commit hook (runs `make check`)
make deploy     the console built in, a wheel, released on the box, the live suite, the journal
```

Python 3.12 and uv; colima for the suites that need Postgres (`make db`). Nothing runs LiveKit
locally. Apache-2.0.
