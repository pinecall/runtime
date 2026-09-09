# The toolchain

What `pyproject.toml`, `scripts/` and `.github/workflows/ci.yml` decide, and why. The layout
itself is `README.md`; this is the reasoning behind the lines a reader would otherwise guess at.
The TypeScript half of the product has a page of the same name in `pinecall/agents`.

## One distribution, two extras, two groups

`pip install pinecall` is the gateway. `[runtime]` adds the three vendor plugins LiveKit's own
inference gateway does not front — onnxruntime and transformers come with them, hundreds of
megabytes the control plane never loads. `[memory-graph]` adds Graphiti, optional because
pgvector is the default memory and needs nothing extra.

`asyncpg` and `pgvector` sit in the base because Postgres is the one stateful service and the
gateway always talks to it. So does `livekit-agents[anthropic,openai]`: the text session IS a
livekit LLM caller — `ChatContext`, `LLMStream` and `LLMMetrics` are its own vocabulary — so the
library and the two text plugins are core, not an extra.

**The judges are not a group and not a distribution.** They were `pinecall-evals`, a second
`pyproject.toml` with its own lock, depending on `pinecall[runtime]` by path while the runtime's
`evals` group depended back on it — two editable sources in one tree, and nobody ever installed
one without the other. They are `src/pinecall/evals/` now. What that cost was found on the way:
three ring-1 door tests had been skipping in silence behind `pytest.importorskip`, and the
judges' modules were type-checked at `standard` rather than `strict` because they were outside
the package pyright pointed at.

## The wire is a path dependency

`pinecall-protocol` is generated and committed in `pinecall/protocol`, and resolved here as the
checkout beside this one — `[tool.uv.sources] path = "../protocol/python", editable = true`. A
wire change is three commits by design: the schema, then a bump in each consumer. That is what a
wire is for, and it is what `livekit/protocol` accepted for the same reason.

The golden call log ships inside that package (`pinecall_protocol.fixtures`), so the log this
suite folds is byte-for-byte the one TypeScript folds.

## Pins, and what each checker is for

`pyright` and `mypy` are pinned exactly, `ruff` and pytest by range: the two type-checkers
disagree about new rules on a patch release and a red tree that nobody changed is a morning
gone. Both run in strict mode over `src` and `tests` alike — a test that stops type-checking is
a test that has stopped describing the thing it tests.

## What the suite refuses to be

Unit tests run on dead-sentinel provider keys: everything constructs, and a real call dies in
seconds rather than spending money in CI. The run is SHUFFLED (`pytest-randomly`), so green
three times is a gate and not luck — a test that needs the one before it fails on its own.
`filterwarnings = error`, a 10-second timeout per test, and no network.

The Postgres ring is a marker of its own (`-m postgres`) against the real database, because a
store that is only ever exercised through a fake is a store nobody has run.
