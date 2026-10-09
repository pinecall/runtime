# The runtime on a laptop

`pinecall-runtime local up` is the whole of it: the services in Docker, the schema, the secrets, the
gateway and a worker (`docs/the-runtime-cli.md`, "local"). The compose files it writes are the
package's own, `pinecall/cli/local/`: Postgres 17 with pgvector and pg_textsearch (`postgres/`,
also the suites' database, `make db`), Redis, LiveKit in dev mode on a pair that exists only on
one machine, and LiveKit's SIP under the `phone` profile, Linux only.

From this checkout, `make local` is `local up --services-only --dir .local`, and `make
local-gateway` and `make local-worker` run the two processes from the sources on `.local/env`.
