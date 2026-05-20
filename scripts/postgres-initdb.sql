-- corpus-forge — Postgres init-DB script.
--
-- Mounted to /docker-entrypoint-initdb.d/00-init.sql:ro by the Phase R
-- compose stack. Postgres's entrypoint executes every *.sql in that
-- directory exactly once, on first container start (before any client
-- connection is accepted). The file is mounted read-only as a defense
-- against accidental in-container edits.
--
-- All this script does is ensure the pgvector extension is registered
-- in the default database so `corpus-forge migrate` can install the
-- schema without manual psql intervention.

CREATE EXTENSION IF NOT EXISTS vector;
