-- Once, when the cluster is first made: the migrations create no extension, a superuser does.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;
