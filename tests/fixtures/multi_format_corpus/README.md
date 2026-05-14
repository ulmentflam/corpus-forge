# `tests/fixtures/multi_format_corpus/`

Synthetic fixture tree for the multi-format end-to-end integration test
(`tests/integration/test_multi_format_ingest_e2e.py`).

## How to regenerate

The tree is **checked in** as committed bytes. Only regenerate when
adding new file families or fixing a determinism bug:

```bash
uv sync --group dev --extra multi-format --extra code
uv run python scripts/build_fixture_corpus.py
git diff tests/fixtures/multi_format_corpus
```

A clean `git diff` after a regen run is the determinism contract.

## Layout

```
multi_format_corpus/
├── prose/      Markdown / plain-text / LaTeX (PassthroughMarkdownExtractor + PlainTextExtractor)
├── pdf/        Digital PDFs (PdfDigitalExtractor)
├── html/       Articles with and without nav/ads noise (HtmlExtractor)
├── epub/       Tiny EPUB (EpubExtractor)
├── office/     DOCX / PPTX / XLSX (OfficeExtractor — Docling)
├── notebook/   Jupyter notebook (NotebookExtractor)
├── data/       CSV / TOML / JSON / SRT (CsvExtractor / StructuredDataExtractor / SubtitleExtractor)
└── code/       Source files for ~20 languages + Makefile / Dockerfile / dotfiles (CodeExtractor)
```

## Notes

* No timestamps, UUIDs, or per-run randomness in any file content.
* Office and EPUB files are repacked zip containers with sorted entries
  and pinned epoch mtimes, so the bytes are stable across machines.
* PDFs have their `/CreationDate` and `/ModDate` stripped to the same
  fixture epoch (2000-01-01).
* A local `.gitignore` lives at this fixture root with negation rules
  re-including `code/build/` — the project-root `.gitignore` excludes
  `build/` everywhere, which would otherwise drop the Makefile /
  Dockerfile / `.gitignore` / `.editorconfig` fixtures.
* Out of scope here (Wave 6, P1): `images/` and `pdf/scanned-paper.pdf`
  for the OCR escalation tests.
