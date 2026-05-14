# D-07 Legacy Migration Test Survey

Input for D-10's deletion slice. Classifies the 12 legacy migration test files
that will be affected when `apply_migrations` is rewired to Alembic.

Classification key:
- **PROBABLY-OK-POST-D07**: calls `apply_migrations` and checks schema state
  via SQL queries (columns, tables, constraints). Alembic produces the same
  schema, so these should pass after D-07.
- **WILL-FAIL-POST-D07**: pins file-globbing behavior, specific SQL filenames,
  file discovery order, or raw-SQL content of `.sql` files on disk. These
  fail if the SQL files are deleted in D-10, or fail immediately because
  `schema_dir` is now ignored by `apply_migrations`.
- **AMBIGUOUS**: mixed content; some tests in the file are OK, some will fail.

---

## Unit tests (`tests/unit/`)

### `test_migration_002.py`
**WILL-FAIL-POST-D07** — Pins content of `corpus_forge/schema/002_chunk_content_hash.sql`
(file existence, SQL DDL content, glob discoverability, IF NOT EXISTS count). All
assertions are against the raw `.sql` file on disk; deleted in D-10 → FileNotFoundError.

### `test_migration_003.py`
**WILL-FAIL-POST-D07** — Pins `corpus_forge/schema/003_sync.sql` file existence,
naming convention, and ordering. Deleted in D-10 → FileNotFoundError.

### `test_migration_004_postgres.py`
**WILL-FAIL-POST-D07** — Pins `corpus_forge/schema/004_fts.sql` content (file
existence, ALTER TABLE DDL text, GENERATED ALWAYS AS, glob pattern). Deleted
in D-10 → FileNotFoundError.

### `test_migration_004_sqlite.py`
**WILL-FAIL-POST-D07** — Pins `corpus_forge/schema/sqlite/004_fts.sql` content
(CREATE VIRTUAL TABLE, FTS5, trigger names). Deleted in D-10 → FileNotFoundError.

### `test_migration_sqlite_001.py`
**WILL-FAIL-POST-D07** — Pins `corpus_forge/schema/sqlite/001_core.sql` file
existence, SQLite dialect checks (no BIGSERIAL/JSONB), and table DDL content.
Deleted in D-10 → FileNotFoundError.

### `test_migration_sqlite_002.py`
**WILL-FAIL-POST-D07** — Pins `corpus_forge/schema/sqlite/002_chunk_content_hash.sql`
content (ALTER TABLE, CREATE INDEX, no schema prefix). Deleted in D-10 → FileNotFoundError.

### `test_migration_sqlite_003.py`
**WILL-FAIL-POST-D07** — Pins `corpus_forge/schema/sqlite/003_sync.sql` content
(document_revisions table columns, no BIGSERIAL). Deleted in D-10 → FileNotFoundError.

### `test_sqlite_migration_loader.py`
**WILL-FAIL-POST-D07** — Pins `get_migration_files()` glob dispatch: asserts that
`get_migration_files(schema_dir, dialect='sqlite')` returns a list including
`001_core.sql`, `002_chunk_content_hash.sql`, `003_sync.sql` by filename.
After D-10 deletes the `.sql` files, glob returns empty. Also tests that
`apply_migrations()` `dialect` parameter dispatches to `schema_dir/sqlite/` —
contradicts the D-07 rewire where `schema_dir` is ignored.

---

## Integration tests (`tests/integration/`)

### `test_migrate_002.py`
**PROBABLY-OK-POST-D07** — Calls `apply_migrations(backend, _schema_dir())` (real
schema_dir) and asserts `content_hash` column exists and backfill populates NULL
rows. After D-07 rewire, `schema_dir` is ignored; Alembic runs and creates the
same column. **Caveat**: the idempotency test calls `apply_migrations` twice —
Alembic must handle re-runs gracefully (already true: `version_num` row exists,
upgrade is a no-op). Likely OK; flag for D-10 review.

### `test_migrate_003.py`
**AMBIGUOUS** — Most tests call `apply_migrations(backend, _schema_dir())` and
assert SQL-visible schema objects (tables, columns, constraints, FK rows) — those
are **PROBABLY-OK**. However, one test explicitly asserts that
`get_migration_files(_schema_dir())` returns filenames including `001_core.sql`,
`002_chunk_content_hash.sql`, `003_sync.sql` in order — that is a file-globbing
assertion that **WILL-FAIL** once D-10 deletes the SQL files. D-10 must split
or rewrite this test.

### `test_migrate_004_postgres.py`
**PROBABLY-OK-POST-D07** — Calls `apply_migrations(backend, _schema_dir())` and
asserts `text_tsv` column exists, GIN index exists, FTS queries return hits, and
idempotent re-run is a no-op. All assertions are against DB state via SQL queries.
Alembic produces the same schema. Likely OK post-D07.

### `test_migrate_004_sqlite.py`
**PROBABLY-OK-POST-D07** — Uses `SQLiteBackend.migrate()` (not `apply_migrations`
directly) and checks `chunks_fts` virtual table and trigger names via
`sqlite_master`. After D-07, `SQLiteBackend.migrate()` will call the rewired
`apply_migrations` which dispatches to Alembic. Alembic creates the same schema.
Likely OK.

### `test_migrate_sqlite.py`
**AMBIGUOUS** — Most tests use `SQLiteBackend.migrate()` and assert schema state
via `PRAGMA` / `sqlite_master` queries — those are **PROBABLY-OK**. However,
two tests directly call `apply_migrations(backend, _SQLITE_SCHEMA_DIR, dialect='sqlite')`
and one explicitly calls `get_migration_files(_SQLITE_SCHEMA_DIR, dialect='sqlite')`
asserting filenames `001_core.sql`, `002_chunk_content_hash.sql`, `003_sync.sql`
— those are file-globbing pins that **WILL-FAIL** after D-10 deletes the SQLite
SQL files. D-10 must delete or rewrite those specific test methods.

---

## Summary

| Classification        | Count | Files |
|-----------------------|-------|-------|
| WILL-FAIL-POST-D07    | 8     | `test_migration_002`, `test_migration_003`, `test_migration_004_postgres`, `test_migration_004_sqlite`, `test_migration_sqlite_001`, `test_migration_sqlite_002`, `test_migration_sqlite_003`, `test_sqlite_migration_loader` |
| PROBABLY-OK-POST-D07  | 3     | `test_migrate_002`, `test_migrate_004_postgres`, `test_migrate_004_sqlite` |
| AMBIGUOUS             | 2     | `test_migrate_003` (one glob-assert method), `test_migrate_sqlite` (two direct apply_migrations calls + one get_migration_files call) |

**D-10 action items:**
1. Delete all 8 WILL-FAIL files outright.
2. In `test_migrate_003.py`: remove `test_get_migration_files_includes_003` method (the single glob-asserting test); keep the rest.
3. In `test_migrate_sqlite.py`: remove `test_apply_migrations_explicitly_idempotent` (direct apply_migrations call with schema_dir) and `test_get_migration_files_includes_all_three` (filename-asserting method); keep all SQLiteBackend.migrate()-based tests.
4. Hidden call-site note: `SQLiteBackend.migrate()` internally calls `apply_migrations` via `_migrate_module.apply_migrations(self, schema_dir=schema_dir, dialect="sqlite")`. After D-07 rewire, `schema_dir` is passed but ignored. This is safe; no action needed beyond D-07.
