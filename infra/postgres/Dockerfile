# The Postgres of a box and of the laptop's suites: 17, with pgvector and pg_textsearch, the two
# halves of hybrid search. Built for the test container here; the box builds the same file.

# Fully qualified: podman refuses a short name that docker would resolve against Docker Hub.
# The digest is the multi-architecture index's, so a Mac and the box build on the same bytes.
ARG POSTGRES_IMAGE=docker.io/library/postgres:17.11-trixie@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f

FROM ${POSTGRES_IMAGE} AS build
ARG PGVECTOR_TAG=v0.8.6
ARG PG_TEXTSEARCH_TAG=v1.4.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates git postgresql-server-dev-17 \
 && rm -rf /var/lib/apt/lists/*
# pgvector builds with -march=native unless told otherwise, and that binary dies with SIGILL on
# any other CPU.
RUN git clone --depth 1 --branch ${PGVECTOR_TAG} https://github.com/pgvector/pgvector.git /build/pgvector \
 && make -C /build/pgvector OPTFLAGS="" && make -C /build/pgvector install
RUN git clone --depth 1 --branch ${PG_TEXTSEARCH_TAG} https://github.com/timescale/pg_textsearch.git /build/pg_textsearch \
 && make -C /build/pg_textsearch && make -C /build/pg_textsearch install

FROM ${POSTGRES_IMAGE}
COPY --from=build /usr/lib/postgresql/17/lib/ /usr/lib/postgresql/17/lib/
COPY --from=build /usr/share/postgresql/17/extension/ /usr/share/postgresql/17/extension/
# pg_textsearch keeps BM25 statistics in shared memory: CREATE EXTENSION fails unless preloaded.
RUN echo "shared_preload_libraries = 'pg_textsearch'" >> /usr/share/postgresql/17/postgresql.conf.sample
COPY extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql
