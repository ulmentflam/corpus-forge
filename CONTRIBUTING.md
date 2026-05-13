# Contributing to corpus-forge

Thanks for considering a contribution! This file describes the
expectations for local development, the commit-message style used
across the repo, and the gate every change must pass before it lands
on `main`.

## Code of conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
By participating you agree to keep interactions respectful. Report
issues to `evan@qwerky.ai`.

## Developer setup

```bash
git clone --recurse-submodules https://github.com/ulmentflam/corpus-forge
cd corpus-forge

# One-shot dev install — installs runtime deps, every optional extra,
# the dev tool-chain, and registers pre-commit hooks.
make dev
```

`make dev` runs `uv sync --all-extras --group dev` plus
`uv run pre-commit install`. The `make _unhide-pth` Darwin workaround
is invoked automatically — see the comment in the `Makefile` for the
iCloud-Drive UF_HIDDEN flag this works around.

## Branching

- `main` — protected, signed-tag releases land here.
- Feature branches — use a short slug: `b-sqlite-wiring`,
  `r4-rerank`, `cs-skill-frontmatter`, `br-banner`, …
- Long-running spike branches — prefix with `spike/`.

## Commit-message style

The repo uses a `[role] phase-slice: summary` prefix so the audit
trail mirrors the TDD ensemble:

```
[tdd-tester]  phase-r4/R4-02: red suite for cross-encoder pipeline
[tdd-coder]   phase-r4/R4-02: GREEN — cross-encoder rerank impl
[tdd-principal] phase-br/W0: claim BR-01..BR-04 for tdd-tester
```

Roles in use: `tdd-tester`, `tdd-coder`, `tdd-qa`, `tdd-principal`.
`phase-*/slice` is the master-plan task ID.

External contributors should adopt the same shape (`[contrib] <slice>:
<summary>`). The commit body explains the **why**, not just the
**what**; a Co-Authored-By trailer for any AI assistant used is
encouraged. Signed commits are required on `main`.

## Tests and the gate

Every change must keep the full pipeline green:

```bash
make ci          # full local pipeline — format-check + lint + typecheck + tests
make test-unit   # the fast lane (parallel, coverage-gated ≥ 85%)
```

If you add new behaviour, write the test first (red), then the code
(green). The repo's test layout:

- `tests/unit/` — millisecond-fast, no Docker, deterministic.
- `tests/integration/` — Docker-backed (pgvector containers).
- `tests/fuzz/` — Hypothesis property tests.
- `tests/smoke/` — end-to-end happy paths against fake embedders.

Linters and type-checker:

```bash
make format      # ruff format
make lint        # ruff check (auto-fix)
make typecheck   # pyrefly strict on corpus_forge/
```

## Pull requests

1. Branch from `main`, push your work, open a PR.
2. The PR template asks for a **summary** and a **test plan** —
   please fill both in.
3. CI must be green. The `CI` workflow runs on every push to a PR
   branch.
4. A maintainer will review. Tag `@ulmentflam` if it sits stale.

## Releasing

Maintainers only. Release tags are annotated + GPG-signed:

```bash
git tag -as v0.1.0b1 -m "corpus-forge 0.1.0b1 — first beta"
git push origin v0.1.0b1
```

Pushing the tag fires `.github/workflows/release.yml`: it re-runs CI,
builds the wheel + sdist with `uv build`, attaches SHA-256 checksums,
and creates a GitHub release. Beta / RC tags are marked as
`prerelease` automatically.

## Reporting vulnerabilities

Don't open a public issue. Email `evan@qwerky.ai` per the policy in
[`SECURITY.md`](SECURITY.md).
