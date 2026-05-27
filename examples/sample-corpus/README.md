# Sample corpus — Skycast

A small, self-contained knowledge base you can point **corpus-forge** at
to try the ingest → embed → search loop end to end. Everything here is
original, evergreen, and released under CC0 — no real people, dates, or
PII.

The files describe a fictional tiny project: **Skycast**, a terminal
weather CLI. They reference one another (a kickoff meeting, an
architecture note, an FAQ, usage metrics, a config file, and the actual
Python module) so search returns coherent, cross-referencing results.

## What's inside

```
examples/sample-corpus/
├── notes/
│   ├── kickoff-meeting.md   # meeting notes: agenda, decisions, action items
│   └── architecture.md      # design note: components + data flow
├── docs/
│   └── faq.md               # a handful of Q&A entries
├── data/
│   └── metrics.csv          # fictional weekly usage metrics
├── config/
│   └── settings.toml        # the Skycast config file
└── src/
    └── skycast.py           # the Skycast module (dataclass + helpers)
```

## Try it

The commands below mirror the repo README **Quickstart**. They assume
corpus-forge is installed (`pip install corpus-forge[sqlite,hf]`) and a
config exists at `~/.config/corpus-forge/config.toml`.

```bash
# 1. Point a scan source at this directory by adding a [[datasets.sources]]
#    entry to your config.toml (use the absolute path to this folder):
#
#      [[datasets.sources]]
#      root = "/abs/path/to/corpus-forge/examples/sample-corpus"
#
# 2. Initialize the database (idempotent).
corpus-forge migrate

# 3. Run a one-shot ingestion pass over the configured sources.
corpus-forge ingest --once

# 4. Backfill embeddings for your configured embedder.
corpus-forge embed -e qwen3_8b

# 5. Search the corpus end to end.
corpus-forge search "what is the cache TTL" --k 5
corpus-forge search "why does skycast default to celsius" --k 5
corpus-forge search "who owns the data model" --k 5
```

## Good first searches

- *"what is the cache TTL"* — pulls from the FAQ, the config, and the
  architecture note.
- *"what was decided at kickoff"* — surfaces the decision list.
- *"how do I switch to fahrenheit"* — spans the FAQ and `skycast.py`.
