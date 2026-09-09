# infra — the compose stack

Why `infra/compose/dev.yml` holds the services it holds, pinned the way it is.
What each service does and which ports it owns is `infra/README.md`; this is the argument
behind the lines a reader would otherwise have to take on faith. The three settings that
look arbitrary — the SFU's node IP, Redis without a host port, the SIP config path — are
in that README beside the ports they affect, where somebody debugging will actually meet
them.

## One compose file, not three

The same five services run on a laptop and on a customer's box, so there is one file and
no per-environment fork. The differences that do exist are a profile (`gpu`) or an
environment variable (`TEI_IMAGE`), both visible in the file itself. An override file
would hide them.

## Postgres is built, everything else is pulled

Hybrid retrieval needs pgvector for the embedding half and pg_textsearch for the BM25 half,
and no published image carries both. So one small two-stage Dockerfile compiles both against
`postgres:17.11-trixie` and the runtime image is the official one plus two `.so` files.

`shared_preload_libraries` is appended to `postgresql.conf.sample` in the image rather than
passed as `-c` on the compose command line: pg_textsearch keeps its BM25 term statistics in
shared memory, so without the preload `CREATE EXTENSION` simply fails, and a setting the
extension cannot live without does not belong somewhere a caller can forget it.

pgvector builds with `OPTFLAGS=""`. Its default is `-march=native`, which bakes the build
machine's CPU into the binary; the image would then run only on the machine that built it,
and fail with SIGILL — not a clear error — anywhere else.

## Every tag is exact

Image tags are pinned to a patch (`livekit/livekit-server:v1.13.6`, `redis:8.10.1-alpine`,
`postgres:17.11-trixie`), and the two source builds to a git tag (`pgvector v0.8.6`,
`pg_textsearch v1.4.0`). A floating tag makes "it worked yesterday" unanswerable, and the
stack under test has to be the stack that ships. Bumps are their own commit.

The one exception is forced: HuggingFace publishes no versioned arm64 CPU tag for
text-embeddings-inference. `dev.yml` therefore defaults to the amd64 `cpu-1.9.3`, and
Apple Silicon sets `TEI_IMAGE` to `cpu-arm64-sha-4150561` — the sha-tagged alias of
`cpu-arm64-latest`, which at least does not move. Emulating the amd64 image instead is not
a fallback: bge-m3 under qemu takes minutes per request.

## CPU embeddings by default

bge-m3 is 568M parameters and answers a single Spanish sentence in well under a second on
four cores, so the default stack needs no GPU and no API key, and `pinecall-runtime doctor`
means the same thing on every machine. The `gpu` profile adds what a GPU is actually worth:
a reranker (`BAAI/bge-reranker-v2-m3`, the same model family as the embedder, so the two
agree about what a document means) and vLLM, for a turn that must not leave the building.
Both reserve an nvidia device, which is why they are behind a profile and not behind a
comment — a machine without a GPU refuses to start them, loudly.

## Not taken

A separate GPU embedder service alongside the CPU one: two services on one port, and
nothing in compose to choose between them. On a GPU box the same `TEI_IMAGE` variable
already points `tei` at a CUDA image.

`network_mode: host`: it does make the SFU's addressing problem disappear, and it is
Linux-only — the laptop this has to run on is not.
