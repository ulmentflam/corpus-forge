# Task 0003 — embedder list/get smoke

## Setup

No `~/.config/corpus-forge/config.toml` on this host, so I couldn't
exercise the verbs against the user's live two-embedder DB (the
`qwen3-2048 orphaned` / `qwen3-2000 active` scenario the priority list
called out). Both `embedder list` and `embedder get` exit 1 with
`Could not load config` when there's no config — verified at
`corpus_forge/admin/embedder.py:182-184` (and the same pattern in
`cmd_get` at L226-228).

## Code review (in lieu of live exercise)

Read `corpus_forge/admin/embedder.py:176-215` (the `list` verb):

- Iterates `config.embedders` (config-driven, NOT a `SELECT * FROM
  corpus.embedders`).
- For each config row, calls `_count_coverage(backend, cfg.name)`, which
  hits `backend.find_embedder_row_by_name` then
  `backend.count_existing_embeddings`. Backend unreachable → coverage
  column degrades to `"?"`.
- Renders a Rich Table with Name / Provider / Model / Dim / Active /
  Fingerprint / Coverage columns. The `Active` column shows yes/no
  based on `cfg.active`.

## Findings

1. **Two-config-row case will render correctly.** If both `qwen3-2048`
   and `qwen3-2000` are entries in `[[embedders]]` (the active flag
   distinguishes them), the table shows both rows with their respective
   coverage counts. The "orphaned" embedder will show with `Active: no`
   and whatever coverage count remains for its vectors.

2. **DB-only orphan case will NOT render.** If `qwen3-2048` was
   *removed from config* but its row + vectors are still in the DB,
   `embedder list` never enumerates it — the loop is over
   `config.embedders` only. There is no public
   `list_all_embedder_rows()` on the backend (grep confirms no such
   verb in `corpus_forge/backends/postgres.py`).

   This is a UX gap for the priority-list scenario as described:
   "make sure the Rich table renders both with coverage counts." If
   "orphan" means "config entry removed, vectors still in DB," the
   table will silently underreport.

   Recommendation: add `corpus-forge embedder list --include-orphans`
   that JOINs `config.embedders` with `corpus.embedders` and renders
   rows marked `(orphan)` for DB-only entries. Or always show them
   with a separate section heading. Scoped as a follow-up task — out
   of scope for this smoke since it needs a backend method that
   doesn't exist yet.

3. **`embedder get NAME` works on config rows.** Looking at
   `cmd_get` at L218-260, it looks up `config.embedders[name]` first;
   if `name` isn't in config it exits 1 with `not found in config`.
   So `embedder get qwen3-2048` will succeed when 2048 is still a
   config row (just inactive), and fail when 2048 is a DB-only orphan.

## Verdict

The Rich rendering itself is fine — the same code path handles 1 row,
2 rows, or N rows correctly. The interesting question is whether
"orphaned" in the priority list means "inactive config row" (works
today) or "removed from config but still in DB" (silently
underreported). Recommend opening a follow-up issue for the latter
case if it's what the user meant.

## Caveats

- Did not exercise against live DB. The user (with the actual config
  + DB) should run `corpus-forge embedder list` and confirm both rows
  show. If `qwen3-2048` is missing from the table output, see
  finding (2) above.
