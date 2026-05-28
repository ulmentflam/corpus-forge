"""Phase M Wave 1 — defaults for the managed ``.corpusignore`` block.

``corpus_forge.ignore_defaults`` is a pure module (no I/O at import or
at call time):

- ``MANAGED_START`` / ``MANAGED_END`` sentinel constants.
- ``_ALWAYS_ON`` / ``_AUDIO_VIDEO`` / ``_RAW_IMAGES`` pattern tuples.
- ``feature_flags_from_config(cfg) -> dict[str, bool]`` derives the
  four feature bools from a :class:`corpus_forge.config.Config`.
- ``default_managed_lines(features) -> list[str]`` composes the
  managed-block body deterministically from a feature map.
- ``render_managed_block(features, *, include_timestamp=True) -> str``
  wraps the body in the two sentinels (and optional timestamp comment).
- ``parse_managed_lines(text) -> list[str] | None`` extracts the body
  back out so a doctor / sync round-trip can compare drift.
"""

from __future__ import annotations

from corpus_forge.config import Config
from corpus_forge.ignore_defaults import (
    _ALWAYS_ON,
    _AUDIO_VIDEO,
    _RAW_IMAGES,
    MANAGED_END,
    MANAGED_START,
    default_managed_lines,
    feature_flags_from_config,
    parse_managed_lines,
    render_managed_block,
)

# ── module-level constants ────────────────────────────────────────────


class TestModuleConstants:
    def test_sentinels_are_full_line_comments(self) -> None:
        assert MANAGED_START.startswith("# ")
        assert MANAGED_END.startswith("# ")
        # Hard to mistake one for the other.
        assert MANAGED_START != MANAGED_END

    def test_always_on_patterns_include_essentials(self) -> None:
        # The required-conservative-set lives in _ALWAYS_ON.
        assert ".DS_Store" in _ALWAYS_ON
        assert "*.icloud" in _ALWAYS_ON
        # Lockfiles
        assert any(p.endswith(".lock") for p in _ALWAYS_ON)
        # Sourcemaps and minified
        assert "*.min.js" in _ALWAYS_ON
        assert "*.map" in _ALWAYS_ON
        # Build dirs
        assert "dist/" in _ALWAYS_ON
        assert "build/" in _ALWAYS_ON

    def test_always_on_does_not_swallow_docs_or_source(self) -> None:
        # The conservative-pattern policy: PDFs / notebooks / source code
        # NEVER end up in the always-on tuple regardless of feature flags.
        assert "*.pdf" not in _ALWAYS_ON
        assert "*.ipynb" not in _ALWAYS_ON
        assert "*.py" not in _ALWAYS_ON
        assert "*.md" not in _ALWAYS_ON
        assert "*.rs" not in _ALWAYS_ON

    def test_always_on_is_sorted_tuple(self) -> None:
        # Determinism: every release must order these the same way.
        assert isinstance(_ALWAYS_ON, tuple)
        assert list(_ALWAYS_ON) == sorted(_ALWAYS_ON)

    def test_audio_video_patterns_present(self) -> None:
        assert "*.mp3" in _AUDIO_VIDEO
        assert "*.mp4" in _AUDIO_VIDEO
        assert "*.mov" in _AUDIO_VIDEO
        # And not in always-on
        assert "*.mp4" not in _ALWAYS_ON
        # Sorted determinism
        assert list(_AUDIO_VIDEO) == sorted(_AUDIO_VIDEO)

    def test_raw_image_patterns_present(self) -> None:
        # RAW formats, not common .jpg / .png (those are an extractor
        # decision higher up the stack).
        assert "*.heic" in _RAW_IMAGES
        assert "*.cr2" in _RAW_IMAGES
        assert "*.dng" in _RAW_IMAGES
        assert "*.raw" in _RAW_IMAGES
        # Sorted determinism
        assert list(_RAW_IMAGES) == sorted(_RAW_IMAGES)


# ── default_managed_lines truth table ─────────────────────────────────


class TestDefaultManagedLines:
    def test_empty_features_yields_only_always_on(self) -> None:
        # Empty dict and "all features off" both mean "audio/video and
        # raw images get ignored, only PDFs/notebooks/source stay".
        lines_empty = default_managed_lines({})
        # Always-on must be a subset of empty-features output.
        for p in _ALWAYS_ON:
            assert p in lines_empty
        # All-off enables every conservative gate.
        all_off = default_managed_lines(
            {"whisper": False, "image_extractor": False, "code_enricher": False, "vlm": False}
        )
        for p in _AUDIO_VIDEO:
            assert p in all_off
        for p in _RAW_IMAGES:
            assert p in all_off

    def test_whisper_disabled_adds_audio_video(self) -> None:
        lines = default_managed_lines({"whisper": False})
        for p in _AUDIO_VIDEO:
            assert p in lines

    def test_whisper_enabled_drops_audio_video(self) -> None:
        lines = default_managed_lines({"whisper": True})
        for p in _AUDIO_VIDEO:
            assert p not in lines

    def test_image_extractor_disabled_adds_raw_images(self) -> None:
        lines = default_managed_lines({"image_extractor": False})
        for p in _RAW_IMAGES:
            assert p in lines

    def test_image_extractor_enabled_drops_raw_images(self) -> None:
        lines = default_managed_lines({"image_extractor": True})
        for p in _RAW_IMAGES:
            assert p not in lines

    def test_no_combination_ever_ignores_pdfs_notebooks_or_source(self) -> None:
        # Try a small product of feature flips.
        combos = [
            {},
            {"whisper": False},
            {"whisper": True},
            {"image_extractor": False},
            {"image_extractor": True, "whisper": True},
            {"whisper": False, "image_extractor": False, "code_enricher": False, "vlm": False},
        ]
        forbidden = {"*.pdf", "*.ipynb", "*.py", "*.md", "*.rs", "*.ts", "*.tsx", "*.go"}
        for features in combos:
            out = default_managed_lines(features)
            assert forbidden.isdisjoint(out), (
                f"forbidden pattern leaked into managed lines for features={features}"
            )

    def test_output_is_stable_across_calls(self) -> None:
        a = default_managed_lines({"whisper": False, "image_extractor": False})
        b = default_managed_lines({"whisper": False, "image_extractor": False})
        assert a == b
        # And sorted within each contiguous group, but at minimum: the
        # full output equals itself across calls.
        assert a == default_managed_lines({"whisper": False, "image_extractor": False})


# ── render_managed_block + parse_managed_lines round-trip ─────────────


class TestRenderAndParse:
    def test_render_wraps_block_with_sentinels(self) -> None:
        text = render_managed_block({"whisper": False}, include_timestamp=False)
        assert MANAGED_START in text
        assert MANAGED_END in text
        # MANAGED_END appears after MANAGED_START.
        assert text.index(MANAGED_START) < text.index(MANAGED_END)

    def test_render_includes_timestamp_when_requested(self) -> None:
        with_ts = render_managed_block({}, include_timestamp=True)
        without_ts = render_managed_block({}, include_timestamp=False)
        # Timestamp comment marker — accept any "rendered" / "generated"
        # variant the implementation picks, just require *some* comment
        # line in the with-ts case that's NOT in without.
        assert with_ts != without_ts
        assert len(with_ts) > len(without_ts)

    def test_parse_returns_body_lines_between_sentinels(self) -> None:
        features = {"whisper": False, "image_extractor": False}
        rendered = render_managed_block(features, include_timestamp=False)
        body = parse_managed_lines(rendered)
        assert body is not None
        # Body excludes the sentinel lines themselves.
        assert MANAGED_START not in body
        assert MANAGED_END not in body
        # Every pattern from default_managed_lines should be present in
        # the parsed body (parser may keep comments around the patterns,
        # so check membership, not equality).
        for p in default_managed_lines(features):
            assert p in body

    def test_parse_no_sentinels_returns_none(self) -> None:
        assert parse_managed_lines("no sentinels here\nfoo\nbar\n") is None
        assert parse_managed_lines("") is None

    def test_parse_only_start_returns_none(self) -> None:
        # Half a block is corruption — parse_managed_lines just reports
        # None; the splicer raises ManagedBlockCorrupted from its own
        # path.
        text = MANAGED_START + "\nfoo\n*.mp4\n"
        assert parse_managed_lines(text) is None

    def test_parse_only_end_returns_none(self) -> None:
        text = "*.mp4\n" + MANAGED_END + "\n"
        assert parse_managed_lines(text) is None

    def test_roundtrip_render_then_parse_then_diff(self) -> None:
        features = {"whisper": True, "image_extractor": False}
        rendered = render_managed_block(features, include_timestamp=False)
        body = parse_managed_lines(rendered)
        assert body is not None
        # Subset relationship — anything that should have been emitted
        # is in the parsed body.
        expected = set(default_managed_lines(features))
        # Sentinel lines themselves are excluded by the parser.
        assert expected.issubset(set(body))


# ── dev/build junk patterns (2026-05-27) ──────────────────────────────
#
# A real user's ingested roots contained code repos whose dev/build
# artifacts DROWNED the scanner: one repo's ``.venv`` alone was 61,217
# files, plus 577+ ``node_modules`` / ``.git`` / ``__pycache__`` dirs.
# These patterns must be UNCONDITIONALLY in the always-on managed
# template so a fresh ``corpus-forge setup`` / init auto-ignores them.
#
# Tests assert through the PUBLIC surface (``default_managed_lines`` /
# ``render_managed_block`` / ``parse_managed_lines``) so the coder is
# free to fold them into ``_ALWAYS_ON`` or a new sorted group.

# The canonical 25 patterns from the requirement (gitignore syntax).
# Extended (post-PR-#68) to cover Elixir/Erlang Mix (`deps/`, `_build/`),
# Go modules / PHP Composer / Ruby Bundler (`vendor/`), and legacy JS
# package manager (`bower_components/`).
_DEV_BUILD_JUNK: tuple[str, ...] = (
    ".git/",
    ".venv/",
    "venv/",
    "env/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tox/",
    ".eggs/",
    "*.egg-info/",
    "*.egg",
    ".cache/",
    ".gradle/",
    ".terraform/",
    ".ipynb_checkpoints/",
    "site-packages/",
    # Multi-language vendor + build dirs (this PR).
    "_build/",
    "bower_components/",
    "deps/",
    "vendor/",
)


class TestDevBuildJunkPatterns:
    def test_all_junk_patterns_are_unconditionally_present(self) -> None:
        # Always-on: produced for the empty / all-off feature map.
        lines = set(default_managed_lines({}))
        for pat in _DEV_BUILD_JUNK:
            assert pat in lines, f"dev/build junk pattern missing from template: {pat!r}"

    def test_junk_patterns_survive_every_feature_combination(self) -> None:
        # The junk set is unconditional — no feature flip drops it.
        combos = [
            {},
            {"whisper": True},
            {"whisper": False},
            {"image_extractor": True},
            {"image_extractor": True, "whisper": True, "vlm": True},
            {"whisper": True, "image_extractor": True, "code_enricher": True, "vlm": True},
        ]
        junk = set(_DEV_BUILD_JUNK)
        for features in combos:
            out = set(default_managed_lines(features))
            assert junk.issubset(out), (
                f"dev/build junk dropped for features={features}: missing {junk - out}"
            )

    def test_junk_patterns_round_trip_through_render_and_parse(self) -> None:
        rendered = render_managed_block({}, include_timestamp=False)
        body = parse_managed_lines(rendered)
        assert body is not None
        body_set = set(body)
        for pat in _DEV_BUILD_JUNK:
            assert pat in body_set, f"junk pattern not round-tripped: {pat!r}"

    def test_notebook_file_still_not_ignored_but_checkpoints_dir_is(self) -> None:
        # ``.ipynb_checkpoints/`` is a junk DIRECTORY (safe to ignore);
        # ``*.ipynb`` is a real notebook FILE and must NEVER be ignored.
        lines = set(default_managed_lines({}))
        assert ".ipynb_checkpoints/" in lines
        assert "*.ipynb" not in lines

    def test_junk_patterns_do_not_swallow_source_or_docs(self) -> None:
        # Belt-and-suspenders alongside the existing conservative-policy
        # test: none of the source/doc extensions sneak in via the junk
        # group.
        lines = set(default_managed_lines({}))
        for forbidden in ("*.py", "*.pdf", "*.md", "*.ipynb", "*.rs", "*.ts", "*.go"):
            assert forbidden not in lines


# ── feature_flags_from_config ─────────────────────────────────────────


def _make_minimal_config(**overrides) -> Config:
    """Construct a minimal Config for feature-flag derivation tests.

    The wizard's rendered config doesn't include datasets without a
    scan root, so build a tiny config dict and override targeted blocks.
    """
    cfg_data: dict = {
        "backend": {"kind": "sqlite", "dsn": ":memory:"},
        "daemon": {},
        "datasets": [
            {
                "name": "default",
                "kind": "text",
                "sources": [
                    {
                        "plugin": "markdown_vault",
                        "vault_root": "/tmp/notes",
                        "chunker": "markdown",
                    }
                ],
            }
        ],
        "embedders": [
            {
                "name": "qwen3_8b",
                "provider": "sentence_transformers",
                "model_id": "Qwen/Qwen3-Embedding-8B",
                "dimension": 4096,
            }
        ],
    }
    cfg_data.update(overrides)
    return Config(**cfg_data)


class TestFeatureFlagsFromConfig:
    def test_default_config_whisper_is_false(self) -> None:
        cfg = _make_minimal_config()
        flags = feature_flags_from_config(cfg)
        # Whisper default backend is "none" → feature off.
        assert flags["whisper"] is False

    def test_whisper_local_backend_flips_flag(self) -> None:
        cfg = _make_minimal_config(whisper={"backend": "local", "model": "small"})
        flags = feature_flags_from_config(cfg)
        assert flags["whisper"] is True

    def test_whisper_remote_backend_flips_flag(self) -> None:
        cfg = _make_minimal_config(
            whisper={
                "backend": "remote",
                "remote_base_url": "https://api.openai.com/v1",
                "remote_api_key_env": "OPENAI_API_KEY",
            }
        )
        flags = feature_flags_from_config(cfg)
        assert flags["whisper"] is True

    def test_vlm_default_is_off(self) -> None:
        cfg = _make_minimal_config()
        flags = feature_flags_from_config(cfg)
        assert flags["vlm"] is False

    def test_vlm_ollama_backend_flips_flag(self) -> None:
        cfg = _make_minimal_config(vlm={"backend": "ollama"})
        flags = feature_flags_from_config(cfg)
        assert flags["vlm"] is True

    def test_code_enricher_default_off(self) -> None:
        cfg = _make_minimal_config()
        flags = feature_flags_from_config(cfg)
        assert flags["code_enricher"] is False

    def test_code_enricher_local_flips_flag(self) -> None:
        cfg = _make_minimal_config(code_enricher={"backend": "local"})
        flags = feature_flags_from_config(cfg)
        assert flags["code_enricher"] is True

    def test_keys_are_exactly_the_four_known(self) -> None:
        cfg = _make_minimal_config()
        flags = feature_flags_from_config(cfg)
        assert set(flags.keys()) == {"whisper", "image_extractor", "code_enricher", "vlm"}
