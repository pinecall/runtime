# The settings: where the environment comes from

`_settings.py` is the one file that names an environment variable. Everything else asks
`load_settings()`. Two decisions live here: which `.env` a process reads, and why the example
beside it is generated rather than written.

## The mechanism is pydantic-settings', not ours

`Settings.model_config` declares `env_file`, `env_file_encoding` and `extra="ignore"`. No
module calls `load_dotenv`, and nothing reads a file by hand. Until 2026-09-06 the class
declared none of it, so `.env` — the file `.env.example` tells the reader to write — was read
by nothing at all: a trap that cost an evening twice, and fixed here.

**A real environment variable wins over the file.** That is pydantic-settings' own source
order (the process environment before the dotenv source), and it is the behaviour both
deployments need: a box loads `/etc/pinecall/pinecall.env` through systemd's
`EnvironmentFile`, and a laptop that exports `PINECALL_API_KEY` from Pinecall v1 sees the
export shadow the file — which is why `env | grep PINECALL` is still the first thing to run
when a key surprises you.

**`extra="ignore"`,** because a laptop's `.env` carries names this runtime does not read: v1's,
a neighbour project's, a commented block that was uncommented. An unknown name is skipped.
Without it pydantic raises at startup on the first one, which turns a stale line into an
outage.

## Two names, walked up to the repository root and not one directory further

`ENV_FILES = (".env", "runtime/.env")`, looked for in the directory the process started in and
then in each parent, stopping at the first directory that holds either — and at the first one
that holds a `.git`, or the filesystem root, whichever comes first. Two names because there are
two ways in: `uv run pinecall-runtime …` starts in `runtime/`, where the first name *is*
`runtime/.env`; `pinecall run` starts at the repo root, where the second one is that same file.
With both present where the walk stops, the later wins, which is pydantic's order for a list.

Until 2026-09-07 those two directories were the whole rule — no walk — and the argument for it
was that a config depending on how deep you happened to `cd` is worse than none. It was the wrong
argument, because it covered exactly those two ways in and no third: a worker is started in the
**tenant's app directory**, which is neither — `pinecall-runtime worker talk` from
`examples/clinica-norte` is exactly that. From
`examples/clinica-norte` nothing was found, `livekit_api_key` was empty, and livekit raised
`ValueError: either token, or api_key and api_secret, must be set` before a call could start. It
worked only in a shell that happened to be sitting in `runtime/`. A walk is what git, pytest and
node do, and it is what makes "run it from anywhere in your project" true.

**The bound is the whole point, and it is a `.git`.** An unbounded climb on a customer's box
picks up a stray `.env` from a parent directory nobody meant — the wrong keys, silently, on a run
that otherwise looks healthy. That is a worse failure than finding no file, which the runtime
says out loud. The repository is the widest thing a developer means by "my project", so the
repository root is where the walk stops; `.git` is matched as a file as well as a directory,
because that is what a git worktree has.

pydantic resolves an `env_file` *name* against the working directory alone, so `model_config`
cannot express this on its own. `Settings.settings_customise_sources` rebuilds the dotenv source
over the absolute paths `env_files_read()` found; the prefix, the encoding and its place after
the process environment are still `model_config`'s. When `model_config` carries no `env_file` the
default source is handed back untouched — which is what keeps the next section true.

`pinecall-runtime doctor` prints the file it read as its first line — `env: /…/runtime/.env`,
or `env: no .env — environment only`. The trap was that the runtime said nothing; the fix is
not only that it reads the file, but that it always says which.

## A library that wants a value gets it from Settings, by parameter

Settings is the ONLY reader of the environment and of the file. When a library reads the same
variables by itself there are two readers of one value, and the one that reads the file loses:
`pinecall-runtime worker dev` died on livekit's own `ValueError: ws_url is required, or set
LIVEKIT_URL environment variable` (`agents/worker.py:680`) in a directory where `doctor`, in the
same shell, printed `env: runtime/.env` and `✓ livekit`. `AgentServer` falls back to
`os.environ` for the url and the key pair (`worker.py:333-335`), and a file is not the
environment. The fix is never to write `os.environ` — that hides where the value came from, for
good — but to hand the library what Settings holds through the library's own parameters:
`a_server(settings)` passes `ws_url`, `api_key` and `api_secret` to `AgentServer`, livekit's CLI
leaves them alone when no `--url/--api-key/--api-secret` flag is typed
(`cli/cli.py:290-298`), and livekit itself exports them for the job processes it spawns
(`worker.py:719-724`). `tests/test_settings.py` greps the whole runtime for a write, and
`worker dev` refuses with OUR sentence — the three variables by name, and `runtime/.env` as the
place to write them — before livekit ever gets to raise its own.

## The suite reads no `.env` at all

`tests/conftest.py`'s `pytest_configure` clears `Settings.model_config["env_file"]` before
collection, and from then on `load_settings()` and a bare `Settings()` read the process
environment and nothing else — including under the walk, which widened what a *process* can find
and changed nothing about what the *suite* can:
`test_the_suite_reads_no_env_file_the_walk_would_now_find_up_the_tree` pins that from a directory
whose parent holds a `runtime/.env`. pydantic-settings looks `env_file` up on `model_config` at every
construction (`main.py:332`), which is why one line in one hook covers the whole session, in every
xdist worker, with no monkeypatch of private state.

The reason is CLAUDE.md's invariant: *unit tests run with dead-sentinel provider keys*. Until
an earlier reading that was true only for a developer with no `.env`. With one — the file
`.env.example` tells them to write — `monkeypatch.delenv("ELEVEN_API_KEY")` deleted the variable
and pydantic quietly found the key in the file again, so
`test_a_role_with_no_key_at_all_names_the_role_and_what_to_set` failed on their laptop and passed
in CI, and the `needs_llm` tests woke up and called Anthropic for real. A suite that depends on
what is on the operator's disk is not a suite.

The four tests that are *about* the file turn the source back on for themselves, through the
`the_env_file_is_read` fixture in `tests/test_settings.py`, over a `.env` they wrote in a
`tmp_path` they own. `test_the_suite_itself_never_reads_an_env_file_lying_in_the_working_directory`
pins the other side.

**A live test opts in by a mark and by an exported variable, never by a file.** That is the shape
the `postgres` mark already has — a variable naming a real thing, probed, and a skip that names it
when it is not there — and closing the dotenv source is what makes it true for `needs_llm` too:
the key has to be *exported* now, and a `.env` sitting in the directory can no longer hand it over.
`ANTHROPIC_API_KEY` unset, or set to the sentinel, skips them with that sentence. To run them:

```
ANTHROPIC_API_KEY=sk-ant-… uv run pytest -m needs_llm
```

(The environment is read here through `load_settings()`, never `os.environ` — `ruff`'s
`banned-api` rule holds in `tests/` exactly as it holds in `src/`.)

## What was NOT a settings bug: the worker that registered and got no jobs

A day after `worker dev` stopped dying on livekit's own `ValueError` (the section on parameters,
above), the same command with nothing exported registered with livekit
and was never sent a single job, while the same command in a shell that had sourced `runtime/.env`
was — so `Settings` was the suspect again. It was not. livekit-server hands a job only to a worker
whose **reported** load is under `target_load`, `0.7` by default (`pkg/service/agentservice.go`
`JobRequestAffinity`: `affinity += max(0, targetLoad - w.Load())`, `pkg/agent/config.go`
`DefaultTargetLoad = 0.7`), and what a worker reports is the whole machine's CPU average
(`agents/worker.py:83-113`, sent at `worker.py:1509`) — which dev mode does **not** turn off, it
only turns off the worker's own threshold (`worker.py:148`). The laptop was busy in the run that
failed and calm in the run that worked; the exported variables were a coincidence. The mechanism
and the fix — livekit's own `load_fnc` parameter, `dev` reporting none — are in
[worker.md](worker.md).

`start` now says it: one `WARNING` the tick the reported load crosses `0.7`, naming the number and
that livekit will route no job here until it falls. `dev` reports none at all.

The lesson for this file: "it works with the variable exported" is evidence about *that run*, not
about the reader of the value. `doctor` prints which `.env` was read; when it says the values are
there, believe it and look at the other end of the wire.

## `.env.example` is generated

`scripts/generate-env-example` renders it from the declared aliases: every field's
environment variable, its default, and its `description` as the one comment above it.
`tests/test_env_example.py` fails when the committed file is not what the generator renders,
so a field added without its line in the example cannot land. The old file had drifted —
`RECORD`, `PINECALL_API_KEY` and `PINECALL_DEV_KEY` were declared in the class and missing
from it.

The `description=` on each field is therefore load-bearing: it is what an operator reads in
the file. The prose comments in `_settings.py` stay what they always were — the why, for the
reader of the code.
