# Contributing to corpus-forge

Thanks for considering a contribution! This file describes the
expectations for local development, the commit-message style used
across the repo, and the gate every change must pass before it lands
on `main`.

## Code of conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
By participating you agree to keep interactions respectful. Report
issues to `evan@jwo3.io`.

## Developer setup

```bash
git clone --recurse-submodules https://github.com/ulmentflam/corpus-forge
cd corpus-forge

# One-shot dev install — installs runtime deps, every optional extra,
# the dev tool-chain, and registers pre-commit hooks.
make dev
```

`make dev` runs `uv sync --all-extras --group dev` plus
`uv run pre-commit install --hook-type pre-commit --hook-type pre-push`,
which wires up the following enforcement gates:

- **pre-commit (fast, auto-fixing):** `ruff format`, `ruff check --fix`,
  and per-file `pyrefly` typecheck on the staged files.
- **pre-push (strict, no auto-fix):** `ruff format --check`,
  `ruff check`, and project-wide `pyrefly` typecheck. Mirrors the CI
  `quality` job so what passes locally also passes on the remote.

Tests are intentionally not in the push gate — they depend on every
optional extra being installed and would block legitimate pushes from
contributors with partial dev environments. The CI matrix exercises
the full test suite on every PR.

Both stages route `pyrefly` through `scripts/check-pyrefly.sh`, which
greps the output and exits non-zero on any reported error (pyrefly
itself always exits 0). The same script powers `make typecheck` and
the CI `quality` job, so local hooks and CI agree on the failure
surface.

The `make _unhide-pth` Darwin workaround is invoked automatically — see
the comment in the `Makefile` for the iCloud-Drive UF_HIDDEN flag this
works around.

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

Maintainers only. Release tags are annotated + signed (SSH or GPG):

```bash
git tag -s v0.1.0b2 -m "corpus-forge v0.1.0b2 — Living Corpus beta"
git push origin v0.1.0b2
```

Pushing the tag fires `.github/workflows/release.yml`, which runs five
jobs in sequence:

1. **gate** — re-runs the full CI matrix against the exact tag SHA.
2. **build** — `uv build` produces the wheel + sdist; `sha256sum` writes
   `dist/SHA256SUMS`; the `dist/` directory is uploaded as an artifact.
3. **pypi-publish** — downloads the artifact, strips `SHA256SUMS`, and
   publishes to PyPI via [Trusted Publishing][tp] (OIDC; no token in CI).
   Gated by the `pypi` GitHub environment for an audit trail.
4. **publish** — creates the GitHub release, attaches `dist/*`, and
   marks beta/RC tags as `prerelease` automatically.
5. **brew-bump** — syncs `Formula/corpus-forge.rb` into the
   [`ulmentflam/homebrew-tap`][tap] repo, rewriting `url` and `sha256`
   to point at the live source tarball. Soft-fail (`continue-on-error`)
   so a tap-side hiccup after the GitHub release is already live
   doesn't redden the workflow — fall back to the manual recipe below.

[tp]: https://docs.pypi.org/trusted-publishers/

### One-time setup for PyPI Trusted Publishing

Before the **first** release fires, the publisher must be pre-registered:

1. Visit <https://pypi.org/manage/account/publishing/> logged in as the
   maintainer.
2. **"Add a new pending publisher"** with:
   - PyPI Project Name: `corpus-forge`
   - Owner: `ulmentflam`
   - Repository name: `corpus-forge`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
3. Create the GitHub environment (one-liner from a checkout):
   ```bash
   gh api -X PUT \
     "/repos/ulmentflam/corpus-forge/environments/pypi" \
     -f wait_timer=0 \
     -F reviewers='[]'
   ```
   (Optional: add a required reviewer if you want PyPI uploads to be
   manual-approve.)

After the first successful upload, PyPI promotes the publisher from
"pending" to "active" automatically — no further account-side action is
needed on subsequent releases.

### Homebrew tap sync

The `packaging/distribution/corpus-forge.rb` formula in this repo is the
**source of truth**. On each release, the `brew-bump` job in
`release.yml` copies it into [`ulmentflam/homebrew-tap`][tap] as
`Formula/corpus-forge.rb`, rewriting `url` and `sha256` from the live
source tarball.

[tap]: https://github.com/ulmentflam/homebrew-tap

**One-time setup** (a fresh `ulmentflam/corpus-forge` checkout needs
this once):

1. Create a fine-grained PAT at
   <https://github.com/settings/personal-access-tokens/new>:
   - Token name: `corpus-forge → homebrew-tap (brew-bump)`
   - Expiration: **1 year** (the maximum GitHub allows). GitHub emails
     you 7 days before it expires; regenerate from the same UI.
   - Resource owner: `ulmentflam`
   - Repository access: Only select repositories → `homebrew-tap`
   - Permissions: Repository → Contents: Read and write (only this
     one — least privilege)
2. Store it as a repo secret on `ulmentflam/corpus-forge`:
   ```bash
   gh secret set HOMEBREW_TAP_TOKEN
   # paste the github_pat_… value at the prompt; Ctrl-D to commit
   ```

**Manual recovery** (if `brew-bump` ever soft-fails — the GH release is
already published, so the rest of the pipeline is fine):

```bash
# Compute the sha256 of the source tarball for the tag:
curl -sSL https://github.com/ulmentflam/corpus-forge/archive/refs/tags/v0.1.0b2.tar.gz \
  | shasum -a 256 | awk '{ print $1 }'

# Bump the scaffold's url + sha256 in this repo, then sync to the tap:
git -C ../homebrew-tap pull
cp packaging/distribution/corpus-forge.rb ../homebrew-tap/Formula/corpus-forge.rb
git -C ../homebrew-tap commit -am "corpus-forge v0.1.0b2"
git -C ../homebrew-tap push
```

Or, re-run only the `brew-bump` job from the GitHub Actions UI on the
failed release run.

## Reporting vulnerabilities

Don't open a public issue. Email `evan@jwo3.io` per the policy in
[`SECURITY.md`](SECURITY.md).
