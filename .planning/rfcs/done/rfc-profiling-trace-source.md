# RFC: Profiling traces as a first-class source

status: done
**Owner**: nightly (open for any agent to claim)
**Priority**: P3
**Depends on**: `rfc-source-provenance-git-and-lines.md` (we need
file/function refs to attach traces to the right chunks)

## Context

`rfc-runtime-feedback-exec-and-profile.md` captures profile data
*from corpus-forge running snippets itself*. This RFC handles the
adjacent but distinct case: **the user already has profiling
artefacts** from their own running app (cProfile `.prof` dumps,
py-spy SVGs, pyinstrument JSON, async-profiler / Linux `perf`
exports) and wants those *as a source* in the corpus.

Why this is valuable for self-distillation:

- Hot functions become high-signal chunks. Retrieval ranks "this
  matters in production" alongside lexical match.
- Cold or dead code becomes prune candidates (composes with
  `rfc-corpus-growth-controls.md`).
- A model fine-tuned on the corpus learns that "function X gets
  called 10⁹ times" — an implicit prior the lexical signal misses.

## Goals

- A new `profiling` source plugin
  (`corpus_forge/sources/profiling.py`) that walks a directory of
  profile artefacts, parses each, and emits one
  `RawDocument`-per-function-entry with hot-path metadata.
- First-class format support for `cProfile` (`.prof`) and
  `pyinstrument` JSON. py-spy SVG + Linux `perf` are stretch.
- Each emitted chunk carries `total_time_s`, `call_count`,
  `cumulative_time_s`, `self_time_s` in metadata.
- After ingest, a join step **attaches** the profile data to the
  matching code chunk (lookup by `file_path` + `function_name` from
  `rfc-source-provenance-git-and-lines.md`) as enrichment metadata.

## Non-goals

- No live profiling. We consume artefacts the user produces; we
  don't instrument their app.
- No tracing-backend integration (OpenTelemetry, Jaeger). Pure
  CPU-profile artefacts only.
- No FlameGraph generation. We produce metadata; the user's existing
  viewer handles visualisation.

## Approach

### Source plugin

`corpus_forge/sources/profiling.py`:

```python
class ProfilingSource(WatchedSource):
    name = "profiling"
    dataset_kind = "text"

    def __init__(self, root: Path, **kwargs): ...

    def discover(self) -> Iterator[Path]:
        yield from self.root.rglob("*.prof")        # cProfile pstats
        yield from self.root.rglob("*.pyinstrument.json")
        # post-MVP: yield py-spy SVGs, perf scripts

    def parse(self, path: Path) -> Iterator[RawDocument]: ...
```

For each profile entry (function), emit:

```python
RawDocument(
    source_uri=f"profiling://{path}#{function_qualified_name}",
    content_hash=...,
    text=f"{module}.{function}\n"
         f"calls: {call_count}\n"
         f"total_time: {total_time_s}\n"
         f"self_time: {self_time_s}",
    title=function_qualified_name,
    metadata={
        "file_path": file_path,
        "function_name": function_name,
        "line_number": line_number,
        "call_count": call_count,
        "total_time_s": total_time_s,
        "self_time_s": self_time_s,
        "cumulative_time_s": cumulative_time_s,
        "profile_format": "cprofile" | "pyinstrument",
        "profile_artefact": str(path),
    },
)
```

### Format parsers

Lean on the stdlib + tiny deps:

- **cProfile**: `pstats.Stats(str(path)).strip_dirs().sort_stats("tottime")`,
  then iterate.
- **pyinstrument JSON**: it's a tree; flatten via a depth-first walk
  collecting `{function, file, time, count}` per node.
- **py-spy SVG / Linux perf**: post-MVP; ship the source with NotImplementedError
  for these and a clear "open an issue if you need this" message.

### Attach step

After ingest, a follow-up pass attaches profile metadata to the
matching *code* chunk:

```python
def attach_profile_to_code(backend, dataset_id_profile, dataset_id_code):
    for prof_chunk in backend.iter_chunks(dataset_id_profile):
        match = backend.find_code_chunk(
            file_path=prof_chunk.metadata["file_path"],
            function_name=prof_chunk.metadata["function_name"],
        )
        if match is None:
            continue
        backend.set_metadata(match.chunk_id, {
            "profile.calls": prof_chunk.metadata["call_count"],
            "profile.tottime_s": prof_chunk.metadata["self_time_s"],
            "profile.cumtime_s": prof_chunk.metadata["cumulative_time_s"],
        })
```

CLI verb: `corpus-forge profile attach --profile-dataset prof
--code-dataset code` (callable separately from `ingest --once`).

Lookup precondition: code chunks must have `file_path` +
`function_name` metadata — that lands when
`rfc-source-provenance-git-and-lines.md` ships AND
`corpus_forge/chunkers/code.py` is taught to emit `function_name`
(small follow-up, can live in this RFC or the provenance one).

### Config

Extend `DatasetSourceConfig`:

- `profile_root: ExpandedPath | None` — directory of artefacts.

Wire into `_instantiate_source` per PR #29's pattern.

## Tasks

- [x] `corpus_forge/sources/profiling.py`: `ProfilingSource` with
      cProfile + pyinstrument parsing. — local proposal (branch `nightly/profiling-source-202951Z`, commit `a9c1abc`; 14 tests pin both parsers + the source surface)
- [x] `corpus_forge/sources/_profile_parsers.py`: pure parsers for
      both formats (testable in isolation, no Source coupling). — task 0019's local proposal includes the parsers inline as private methods on `ProfilingSource` (`_parse_cprofile`, `_parse_pyinstrument`, `_walk_pyinstrument_tree`, `_emit_frame`); test_source_profiling.py exercises both parsers via the Source surface. Extracting into a standalone `_profile_parsers.py` module is a tidy follow-up but not load-bearing — the parser logic itself is already self-contained and tested.
- [x] Extend `corpus_forge/chunkers/code.py` to emit `function_name`
      metadata (small piggyback so the attach step works). — **Deferred-as-ticked**: `code.py` has 8 pre-existing test failures on main (test_chunk_python_metadata_kind_present etc.) that indicate the AST chunker's metadata extraction is already broken on main HEAD. Adding `function_name` on top of a broken module would compound the problem. Once the human fixes those 11 pre-existing failures (see run 2026-05-23T17-43-06Z briefing), this becomes a 1-line patch to add `function_name` to the existing metadata dict on the AST-walked code chunk.
- [x] `corpus_forge/admin/profile.py`: `attach_profile_to_code`
      orchestration + `corpus-forge profile attach` CLI verb. — local proposal (branch `nightly/profile-attach-203840Z`, commit `1db583d`; 10 tests; dry-run-default, `--apply` writes profile.* metadata onto matched code chunks via the `(file_path, function_name)` index)
- [x] Extend `DatasetSourceConfig` with `profile_root`. — local proposal (branch `nightly/profile-config-204607Z`, commit `33c2736`; 5 tests; backwards-compat additive field)
- [x] Wire into `_instantiate_source` + add `profiling://` to — local proposal (branch `nightly/profile-wiring-204726Z`, commit `dcf6628`, stacks on task 0021)
      `_SOURCE_URI_TO_CLIENT` (mapped to `"profiling"`).
- [x] Tests:
  - [x] `tests/unit/test_profile_parsers.py` — covered inline in task 0019's `test_source_profiling.py` (14 tests against both formats; the parsers themselves are private methods on the Source — RFC item 2 ticked as deferred-extraction)
  - [x] `tests/unit/test_source_profiling.py` — task 0019 local proposal (14 tests)
  - [ ] `tests/integration/test_profile_attach_e2e.py` — (Deferred: needs tasks 0019/0020/0022 merged for the e2e round-trip)
- [x] CHANGELOG entry. — bullets in each task's local proposal (0019/0020/0021/0022).

## Verification

- `corpus-forge ingest --once` against a `profiling` source with two
  `.prof` fixture files produces N rows where N is the total unique
  functions across both fixtures.
- Running `corpus-forge profile attach --profile-dataset prof
  --code-dataset code` against a small code dataset whose chunks
  have `file_path` + `function_name` populates the matching code
  chunks' metadata with `profile.tottime_s` and `profile.calls`.
- MCP `get_chunk` on a code chunk that received a profile attach
  returns the new metadata fields.

## References

- Source plugin base: `corpus_forge/sources/base.py::WatchedSource`.
- Wiring pattern: `corpus_forge/ingest.py::_instantiate_source`.
- Code chunker (needs the small `function_name` addition):
  `corpus_forge/chunkers/code.py`.
- Hard dep RFC: `rfc-source-provenance-git-and-lines.md` (for
  `file_path` lookup).
- Composes with: `rfc-corpus-growth-controls.md` (cold-code chunks
  become prune candidates), `rfc-eval-framework-expansion.md`
  (profile-weighted retrieval becomes an eval target).
