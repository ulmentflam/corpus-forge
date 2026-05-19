# Zotero library connector

corpus-forge can ingest a Zotero library directly — PDF attachments plus
the item-level metadata (authors, year, DOI, collection path, tags,
abstract) that makes academic search useful. Three modes:

| mode    | reads from              | needs           | typical use |
|---------|-------------------------|------------------|-------------|
| `local` | `zotero.sqlite` on disk | nothing extra    | single user, Zotero installed locally |
| `web`   | `api.zotero.org`        | `user_id` + API key | shared / headless / no local install |
| `both`  | both, reconciled        | both             | want web for cloud-only items, local for everything else |

`local` is the default. PDF attachments flow through the existing
`PdfDigitalExtractor` (with optional VLM-backed OCR escalation for
scanned papers); Zotero item metadata is enriched onto every
`RawDocument` so the persisted chunks carry author / year / DOI / etc.

## Quick start (local mode)

```bash
# 1. Install corpus-forge (no extra deps needed for local mode).
uv tool install 'corpus-forge[postgres,hf]'

# 2. Add a Zotero source to a dataset.
corpus-forge source add -d zotero-library
#   Plugin: zotero
#   Mode: local
#   Local zotero.sqlite path: ~/Zotero/zotero.sqlite

# 3. Ingest.
corpus-forge ingest --once
```

The reader opens `zotero.sqlite` with
`sqlite3.connect("file:...?mode=ro&immutable=1", uri=True)` — required
when Zotero is running, because Zotero owns the WAL. **Trade-off:** the
snapshot you read is whatever was last checkpointed, so edits made in
the Zotero UI in the last few minutes may not be visible until Zotero
itself flushes the WAL. If you need strict freshness, quit Zotero
before the ingest pass.

## Web mode

```bash
# 1. Get a Zotero API key:
#    https://www.zotero.org/settings/keys/new
#    Read-only access to your library is sufficient.

# 2. Put it in your shell env (or in ~/.config/corpus-forge/secrets.env).
export ZOTERO_API_KEY="xxxxxxxxxxxxxxxxxxxx"

# 3. Find your user id at https://www.zotero.org/settings/keys (top of page).

# 4. Configure the source.
corpus-forge source add -d zotero-cloud
#   Plugin: zotero
#   Mode: web
#   Zotero user_id (numeric): 1234567
#   API key env var: ZOTERO_API_KEY

corpus-forge ingest --once
```

The web client honors:

- Pagination (`Total-Results` header, `?start=<N>&limit=100`).
- `If-Modified-Since` (304 → empty iterator; corpus-forge will pick this
  up automatically once the watch loop tracks `dateModified` cursors).
- `Retry-After` on 429 (ONE retry, then raise).
- Bounded exponential backoff on 5xx (3 tries total).

Attachment binaries are cached under
`~/.cache/corpus-forge/zotero/<library_id>/<attachment_key>/<filename>`.
Subsequent runs with the same attachment key are zero-network.

## Both mode

Reads local **and** web, reconciles on `zotero_item_key`. Local wins
unless web's `dateModified` is strictly newer. Useful when your Zotero
desktop client is behind cloud sync for an extended time, or when you
have library items added directly via the web interface that haven't
been pulled down yet.

```toml
[[datasets.sources]]
plugin = "zotero"
chunker = "markdown"

  [datasets.sources.zotero]
  mode = "both"
  library_path = "~/Zotero/zotero.sqlite"
  user_id = "1234567"
  api_key_env = "ZOTERO_API_KEY"
```

## Group libraries

```toml
[datasets.sources.zotero]
mode = "web"  # or "both"
user_id = "1234567"
library_type = "group"
group_id = "987654"
```

The local SQLite file backs *all* libraries the user belongs to (your
personal library + every group). The local reader currently emits every
item; future work could filter by `libraryID`.

## MIME allowlist

By default only `application/pdf` attachments are emitted. Add the
`text/html` snapshot type, or whatever else you've got, via:

```toml
[datasets.sources.zotero]
include_attachments = ["application/pdf", "text/html"]
```

Items with NO matching attachments but a non-empty `abstractNote` emit a
single text-only `RawDocument` carrying the abstract — so corpus-forge
still indexes the bibliographic surface area when the PDF isn't on disk.

## Collection filters

```toml
[datasets.sources.zotero]
include_collections = ["Research/Quantum"]   # prefix match — sub-collections included
exclude_collections = ["Archive"]            # applied AFTER include
```

The collection path is the parent-child concatenation Zotero shows in
the UI's sidebar (e.g. `"Research/Quantum/Photosynthesis"`).

## Troubleshooting

### `database is locked` when running ingest

You're not using the read-only URI form. corpus-forge **does** use
`mode=ro&immutable=1`; this error generally means you've replaced the
SQLite path with a snapshot tool that opens it read-write. Verify with:

```bash
corpus-forge doctor --json | jq '.checks[] | select(.name=="zotero")'
```

### `settings.lastclient missing — refusing to proceed`

The file at `library_path` doesn't look like a Zotero library. The
reader probes the `settings` table for a Zotero-specific row before
running any joins; this guard surfaces on accidentally-pointed paths
(stale snapshots, mistaken copies) rather than silently producing
empty output.

### Items appear to be missing recent edits

See the WAL-checkpoint tradeoff above. Quit Zotero before ingesting if
you need bit-exact freshness, or run in `mode = "web"` where the cloud
state is canonical.

### The web client is rate-limited (429)

The client honors `Retry-After` for ONE retry. If you're hitting the
limit repeatedly, your library is large enough that the first sync
should be done with a generous `--once` budget (or run during off-peak
hours). Subsequent runs will be faster because attachment binaries are
cached on disk.

## Reference: emitted `RawDocument` shape

```python
RawDocument(
    source_uri="zotero://<library_id>/<item_key>/<attachment_key>",
    content_hash="<sha256 of the PDF bytes>",
    text="<extracted PDF text>",
    title="<Zotero item title>",
    metadata={
        "zotero_item_key": "ITEMKEY01",
        "zotero_authors": ["Alice Quanta", "Bob Photon"],
        "zotero_year": 2024,
        "zotero_doi": "10.1234/qcp.2024.001",
        "zotero_collection": "Research/Quantum",
        "zotero_abstract": "We demonstrate...",
        "zotero_attachment_key": "ATTKEY01",
        "zotero_mime": "application/pdf",
        "itemType": "journalArticle",
        "chunker_hint": "markdown",
    },
    labels=[
        ("zotero_tag", "quantum"),
        ("zotero_tag", "physics"),
        ("zotero_collection", "Research/Quantum"),
    ],
)
```

Abstract-only docs use `source_uri = "zotero://<library_id>/<item_key>/abstract"`
and carry the same metadata schema with `zotero_attachment_key = None`
and `zotero_mime = None`.

## MCP tool

When the MCP server is started with `--writes-enabled`, a new
`zotero_sync` tool is exposed:

```json
{
  "name": "zotero_sync",
  "input": {"dataset": "zotero-library", "dry_run": true}
}
```

`dry_run=true` walks the configured Zotero sources and returns
`{would_ingest: N, by_mode: {local: N, web: N}}` without touching the
backend. `dry_run=false` invokes the real ingest path and returns
`{ingested, skipped, by_mode, audit_id}`.
