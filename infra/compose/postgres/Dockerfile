# Postgres 17 with the two extensions the retriever needs, for the dev stack and for a box.
# One image, both halves of hybrid search: pgvector for the embedding, pg_textsearch for BM25.

ARG POSTGRES_TAG=17.11-trixie

FROM postgres:${POSTGRES_TAG} AS build

ARG PGVECTOR_TAG=v0.8.6
ARG PG_TEXTSEARCH_TAG=v1.4.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates git postgresql-server-dev-17 \
 && rm -rf /var/lib/apt/lists/*

# OPTFLAGS is emptied on purpose: pgvector defaults to -march=native, which bakes the
# build machine's CPU into the binary and crashes with SIGILL on any other one.
RUN git clone --depth 1 --branch ${PGVECTOR_TAG} https://github.com/pgvector/pgvector.git /build/pgvector \
 && make -C /build/pgvector OPTFLAGS="" \
 && make -C /build/pgvector install

RUN git clone --depth 1 --branch ${PG_TEXTSEARCH_TAG} https://github.com/timescale/pg_textsearch.git /build/pg_textsearch \
 && make -C /build/pg_textsearch \
 && make -C /build/pg_textsearch install


FROM postgres:${POSTGRES_TAG}

COPY --from=build /usr/lib/postgresql/17/lib/ /usr/lib/postgresql/17/lib/
COPY --from=build /usr/share/postgresql/17/extension/ /usr/share/postgresql/17/extension/

# pg_textsearch keeps its BM25 term statistics in shared memory and installs planner
# hooks, so it has to be preloaded at startup or CREATE EXTENSION fails. Appending to
# the sample is how the official image lets you change a setting before initdb copies it,
# and it means nobody has to remember a -c flag on the command line.
RUN echo "shared_preload_libraries = 'pg_textsearch'" >> /usr/share/postgresql/17/postgresql.conf.sample

COPY init.sql /docker-entrypoint-initdb.d/10-extensions.sql
