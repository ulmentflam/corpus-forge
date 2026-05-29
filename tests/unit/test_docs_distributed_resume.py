"""RED tests for DR-T8: docs + config.example.toml surface for distributed resume.

These tests assert that the documentation and config example are updated to
describe the distributed multi-machine resume feature:

  1. ``config.example.toml`` — ``logical_name`` (commented example) under the
     first ``[[datasets.sources]]`` block (the obsidian-vault source); AND
     ``stale_run_threshold`` under ``[scan]`` with both a numeric AND a string
     form referenced.

  2. ``docs/architecture.md`` — ``## Multi-machine ingest`` section present,
     positioned after ``## Backends`` and before ``## Multi-format extractor
     layer``, containing the five API anchors: ``logical_name``,
     ``content_hash``, ``socket.gethostname``, ``stale_run_threshold``,
     ``mark_stale_runs``.

  3. ``README.md`` — contains the prose link ``Multi-machine corpus`` AND the
     anchor ``#multi-machine-ingest`` (pointing into architecture.md).

All tests currently FAIL (RED) because the documentation has not been written
yet (DR-G7 is the GREEN task that will write it).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.toml"
ARCH_DOC = REPO_ROOT / "docs" / "architecture.md"
README = REPO_ROOT / "README.md"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_raw() -> str:
    return CONFIG_EXAMPLE.read_text(encoding="utf-8")


def _arch_raw() -> str:
    return ARCH_DOC.read_text(encoding="utf-8")


def _readme_raw() -> str:
    return README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# config.example.toml — logical_name (commented) under obsidian-vault source
# ---------------------------------------------------------------------------


class TestConfigExampleLogicalName:
    """config.example.toml must illustrate the ``logical_name`` field in
    the obsidian-vault (first) ``[[datasets.sources]]`` block.

    The line must be *commented out* so existing configs remain
    byte-compatible — it is an example, not a default.
    """

    def test_logical_name_appears_in_config_example(self) -> None:
        """``logical_name`` substring must be present somewhere in the file."""
        raw = _config_raw()
        assert "logical_name" in raw, (
            "config.example.toml must contain a 'logical_name' example. "
            "Add it (commented out) to the obsidian-vault [[datasets.sources]] block per §C9."
        )

    def test_logical_name_is_commented_out(self) -> None:
        """The ``logical_name`` example line must start with ``#`` (commented).

        A live value would break existing configs that lack the field.
        The line may have leading whitespace before the ``#``.
        """
        raw = _config_raw()
        # Find every line that contains 'logical_name'
        lines_with_field = [line for line in raw.splitlines() if "logical_name" in line]
        assert lines_with_field, (
            "config.example.toml must contain at least one line with 'logical_name'."
        )
        # At least one of those lines must be a comment (stripped line starts with '#')
        commented = [ln for ln in lines_with_field if ln.lstrip().startswith("#")]
        assert commented, (
            "The 'logical_name' example in config.example.toml must be commented out "
            "(line should start with '#' after optional whitespace). "
            f"Found lines: {lines_with_field!r}"
        )

    def test_logical_name_appears_before_second_dataset_block(self) -> None:
        """The ``logical_name`` comment must appear inside the first
        ``[[datasets.sources]]`` block — i.e. before the second ``[[datasets]]``
        header — so it is clearly associated with the obsidian-vault source.
        """
        raw = _config_raw()
        # Find char-offset of first occurrence of logical_name
        ln_pos = raw.find("logical_name")
        assert ln_pos != -1, "config.example.toml must contain 'logical_name'. Not found at all."
        # Find char-offset of the second [[datasets]] header
        first_datasets = raw.find("[[datasets]]")
        assert first_datasets != -1, "config.example.toml must contain [[datasets]] blocks."
        second_datasets = raw.find("[[datasets]]", first_datasets + 1)
        assert second_datasets != -1, (
            "config.example.toml must have at least two [[datasets]] blocks."
        )
        assert ln_pos < second_datasets, (
            f"'logical_name' (at offset {ln_pos}) must appear before the second "
            f"[[datasets]] header (at offset {second_datasets}). "
            "Place it in the obsidian-vault [[datasets.sources]] block."
        )

    def test_logical_name_has_string_value_in_comment(self) -> None:
        """The commented ``logical_name`` example must include a quoted string
        value to show users the expected shape (e.g. ``# logical_name = "notes"``).
        """
        raw = _config_raw()
        # Look for a commented line that contains logical_name = "..."
        pattern = re.compile(r"#.*logical_name\s*=\s*\"[^\"]+\"")
        assert pattern.search(raw), (
            "config.example.toml must have a commented example of the form "
            '# logical_name = "<name>" showing the expected string value. '
            "Found no such line."
        )


# ---------------------------------------------------------------------------
# config.example.toml — stale_run_threshold under [scan]
# ---------------------------------------------------------------------------


class TestConfigExampleStaleRunThreshold:
    """config.example.toml must contain ``stale_run_threshold`` in the
    ``[scan]`` block with both a numeric example AND a reference to the
    string shorthand form (e.g. ``"15m"``).
    """

    def test_stale_run_threshold_present(self) -> None:
        """``stale_run_threshold`` must appear in the file."""
        raw = _config_raw()
        assert "stale_run_threshold" in raw, (
            "config.example.toml must contain 'stale_run_threshold' in the [scan] block. "
            "Add it per §C9 after the 'workers' field."
        )

    def test_stale_run_threshold_is_in_scan_block(self) -> None:
        """``stale_run_threshold`` must appear after the ``[scan]`` header."""
        raw = _config_raw()
        scan_pos = raw.find("[scan]")
        assert scan_pos != -1, "config.example.toml must have a [scan] block."
        threshold_pos = raw.find("stale_run_threshold", scan_pos)
        assert threshold_pos != -1, (
            "'stale_run_threshold' must appear inside the [scan] block "
            "(i.e. after the '[scan]' header). "
            f"[scan] found at offset {scan_pos}; 'stale_run_threshold' not found after it."
        )

    def test_stale_run_threshold_has_numeric_value(self) -> None:
        """The ``stale_run_threshold`` line must include a numeric value
        (e.g. ``stale_run_threshold = 900.0``).
        """
        raw = _config_raw()
        # Match uncommented assignment with numeric value
        pattern = re.compile(r"^\s*stale_run_threshold\s*=\s*\d[\d.]*", re.MULTILINE)
        assert pattern.search(raw), (
            "config.example.toml must have an uncommented 'stale_run_threshold = <number>' "
            "line. The value should be a float (e.g. 900.0). "
            "Not found."
        )

    def test_stale_run_threshold_references_string_shorthand(self) -> None:
        """The ``stale_run_threshold`` block (line or nearby comment) must
        mention the string shorthand form so users know both ``900.0`` and
        ``"15m"`` are accepted.

        Accepted evidence: the substring ``"15m"`` appears in the file at all
        (inline comment, adjacent comment line, or doc comment above the field).
        """
        raw = _config_raw()
        assert '"15m"' in raw or "'15m'" in raw, (
            "config.example.toml must reference the string shorthand form "
            '(e.g. "15m") near the stale_run_threshold field '
            "so users know both forms are accepted (per §C9). "
            "Add it as an inline comment: "
            'stale_run_threshold = 900.0  # 15 min; also accepts "15m"'
        )


# ---------------------------------------------------------------------------
# docs/architecture.md — ## Multi-machine ingest section
# ---------------------------------------------------------------------------


class TestArchitectureDocMultiMachineSection:
    """docs/architecture.md must contain a dedicated ``## Multi-machine ingest``
    section positioned after ``## Backends`` and before
    ``## Multi-format extractor layer``.
    """

    def test_multi_machine_ingest_heading_present(self) -> None:
        """``## Multi-machine ingest`` heading must be present (line-start anchor)."""
        raw = _arch_raw()
        pattern = re.compile(r"^## Multi-machine ingest", re.MULTILINE)
        assert pattern.search(raw), (
            "docs/architecture.md must contain '## Multi-machine ingest' as a "
            "top-level section heading. Not found. Add it per §C10."
        )

    def test_multi_machine_section_after_backends(self) -> None:
        """``## Multi-machine ingest`` must appear AFTER ``## Backends``."""
        raw = _arch_raw()
        backends_match = re.search(r"^## Backends", raw, re.MULTILINE)
        assert backends_match, "docs/architecture.md must contain '## Backends'. Not found."
        mm_match = re.search(r"^## Multi-machine ingest", raw, re.MULTILINE)
        assert mm_match, "docs/architecture.md must contain '## Multi-machine ingest'. Not found."
        assert mm_match.start() > backends_match.start(), (
            f"'## Multi-machine ingest' (offset {mm_match.start()}) must appear "
            f"AFTER '## Backends' (offset {backends_match.start()})."
        )

    def test_multi_machine_section_before_multi_format(self) -> None:
        """``## Multi-machine ingest`` must appear BEFORE ``## Multi-format extractor layer``."""
        raw = _arch_raw()
        mf_match = re.search(r"^## Multi-format extractor layer", raw, re.MULTILINE)
        assert mf_match, (
            "docs/architecture.md must contain '## Multi-format extractor layer'. Not found."
        )
        mm_match = re.search(r"^## Multi-machine ingest", raw, re.MULTILINE)
        assert mm_match, "docs/architecture.md must contain '## Multi-machine ingest'. Not found."
        assert mm_match.start() < mf_match.start(), (
            f"'## Multi-machine ingest' (offset {mm_match.start()}) must appear "
            f"BEFORE '## Multi-format extractor layer' (offset {mf_match.start()})."
        )


# ---------------------------------------------------------------------------
# docs/architecture.md — API anchors inside the Multi-machine ingest section
# ---------------------------------------------------------------------------


def _extract_multi_machine_section(raw: str) -> str:
    """Return the text from ``## Multi-machine ingest`` to the next ``## `` heading."""
    start_match = re.search(r"^## Multi-machine ingest", raw, re.MULTILINE)
    if start_match is None:
        return ""
    start = start_match.start()
    # Find the next top-level heading after the section start
    next_heading = re.search(r"^## ", raw[start + 1 :], re.MULTILINE)
    if next_heading is None:
        return raw[start:]
    return raw[start : start + 1 + next_heading.start()]


class TestArchitectureDocMultiMachineAnchors:
    """The ``## Multi-machine ingest`` section must mention all five API
    anchors so that the section is actually useful to a developer reading it.

    Checked as case-sensitive substrings (they are code identifiers).
    """

    @pytest.fixture
    def section(self) -> str:
        raw = _arch_raw()
        text = _extract_multi_machine_section(raw)
        if not text:
            pytest.skip(
                "## Multi-machine ingest section not found — "
                "blocking prerequisite test already fails above."
            )
        return text

    def test_section_mentions_logical_name(self, section: str) -> None:
        assert "logical_name" in section, (
            "The '## Multi-machine ingest' section of docs/architecture.md "
            "must mention 'logical_name' (the per-source config field). "
            "Not found."
        )

    def test_section_mentions_content_hash(self, section: str) -> None:
        assert "content_hash" in section, (
            "The '## Multi-machine ingest' section of docs/architecture.md "
            "must mention 'content_hash' (document-level dedup mechanism). "
            "Not found."
        )

    def test_section_mentions_socket_gethostname(self, section: str) -> None:
        assert "socket.gethostname" in section, (
            "The '## Multi-machine ingest' section of docs/architecture.md "
            "must mention 'socket.gethostname' (host-scoped resume). "
            "Not found."
        )

    def test_section_mentions_stale_run_threshold(self, section: str) -> None:
        assert "stale_run_threshold" in section, (
            "The '## Multi-machine ingest' section of docs/architecture.md "
            "must mention 'stale_run_threshold' (the config knob). "
            "Not found."
        )

    def test_section_mentions_mark_stale_runs(self, section: str) -> None:
        assert "mark_stale_runs" in section, (
            "The '## Multi-machine ingest' section of docs/architecture.md "
            "must mention 'mark_stale_runs' (the backend method). "
            "Not found."
        )


# ---------------------------------------------------------------------------
# README.md — Multi-machine corpus discoverability link
# ---------------------------------------------------------------------------


class TestReadmeMultiMachineLink:
    """README.md must carry a discoverability entry so users know the
    feature exists, linking to the new architecture section.
    """

    def test_readme_contains_multi_machine_corpus_prose(self) -> None:
        """The prose ``Multi-machine corpus`` must appear in README.md."""
        raw = _readme_raw()
        assert "Multi-machine corpus" in raw, (
            "README.md must contain the prose 'Multi-machine corpus' "
            "(either as a bullet or feature-list entry). "
            "Add it near the existing '## Why corpus-forge' section per §C10."
        )

    def test_readme_contains_anchor_link_to_architecture(self) -> None:
        """README.md must contain the anchor link to the architecture section.

        The canonical form is ``docs/architecture.md#multi-machine-ingest``
        (GitHub auto-generates anchors from headings by lower-casing and
        replacing spaces with hyphens).
        """
        raw = _readme_raw()
        assert "docs/architecture.md#multi-machine-ingest" in raw, (
            "README.md must contain a link "
            "'docs/architecture.md#multi-machine-ingest' "
            "so users can navigate to the Multi-machine ingest section. "
            "Add it per §C10."
        )
