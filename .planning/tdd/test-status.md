# Test Status — owned by tdd-tester

Record of test suites written by tdd-tester.
| task-id | status | notes |
|---------|--------|-------|
| P0-01   | red    | handed off to tdd-coder |


## P0-01 — `chunk_content_hash`
- Test files: `tests/unit/test_identity.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_identity.py -v`
- Edge case checklist:
  - [x] happy path — basic ASCII text
  - [x] boundaries — empty string, single char, multi-line, long text (1000 repeats)
  - [x] type/format — Unicode (café, 日本語, emoji), whitespace preservation
  - [x] state — determinism (10 identical calls → same output)
  - [x] equivalence — `chunk_content_hash(text) == content_hash(text.encode("utf-8"))`
  - [x] collision resistance — 5 distinct inputs → 5 distinct hashes
  - [x] output format — str, 64 hex chars, lowercase
  - [ ] concurrency — N/A (pure function, no shared state)
  - [ ] locale/time — N/A (no locale/time dependencies)
  - [x] production-realistic — multi-line markdown-like text, special chars
  - [x] regression — distinct inputs produce distinct hashes
- Red output (tail):
```
tests/unit/test_identity.py:5: in <module>
    from corpus_forge.identity import (
E   ImportError: cannot import name 'chunk_content_hash' from 'corpus_forge.identity'
```
- Status: red — handed off to tdd-coder

