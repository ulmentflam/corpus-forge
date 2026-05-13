"""Phase CI-3 LICENSE + py.typed + README license-mention pins.

User override: license is Apache-2.0 (NOT MIT, despite the plan).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LICENSE_PATH = REPO_ROOT / "LICENSE"
PY_TYPED_PATH = REPO_ROOT / "corpus_forge" / "py.typed"
README_PATH = REPO_ROOT / "README.md"


# ── LICENSE ─────────────────────────────────────────────────────────────────


class TestLicenseFile:
    """LICENSE file must be the canonical Apache 2.0 text with 2026 / Evan Owen."""

    def test_license_file_exists(self) -> None:
        assert LICENSE_PATH.exists(), f"LICENSE file missing at {LICENSE_PATH}"

    def test_license_is_apache(self) -> None:
        text = LICENSE_PATH.read_text(encoding="utf-8")
        # Canonical Apache 2.0 opening (line 1 of the upstream text).
        assert "Apache License" in text, "Expected 'Apache License' in LICENSE"
        assert "Version 2.0, January 2004" in text, (
            "Expected canonical Apache 2.0 version line in LICENSE"
        )

    def test_license_includes_apache_url(self) -> None:
        text = LICENSE_PATH.read_text(encoding="utf-8")
        assert "http://www.apache.org/licenses/" in text, (
            "Expected the canonical apache.org/licenses URL in LICENSE"
        )

    def test_license_holder_and_year(self) -> None:
        text = LICENSE_PATH.read_text(encoding="utf-8")
        assert "2026" in text, "Expected year 2026 in LICENSE copyright header"
        assert "Evan Owen" in text, "Expected 'Evan Owen' in LICENSE copyright header"
        assert "corpus-forge contributors" in text, (
            "Expected 'corpus-forge contributors' in LICENSE copyright header"
        )

    def test_license_no_mit_remnant(self) -> None:
        text = LICENSE_PATH.read_text(encoding="utf-8")
        forbidden = ["MIT License", "Permission is hereby granted, free of charge"]
        for token in forbidden:
            assert token not in text, f"LICENSE contains forbidden MIT remnant: {token!r}"

    def test_license_size_sane(self) -> None:
        """Canonical Apache 2.0 license text is about 11kB; require >=10kB."""
        size = LICENSE_PATH.stat().st_size
        assert size >= 10_000, (
            f"LICENSE looks truncated ({size} bytes); canonical Apache 2.0 is ~11.3kB"
        )


# ── py.typed (PEP 561) ──────────────────────────────────────────────────────


class TestPyTypedMarker:
    def test_py_typed_exists(self) -> None:
        assert PY_TYPED_PATH.exists(), (
            f"PEP 561 marker missing at {PY_TYPED_PATH}; "
            f"the Typing :: Typed classifier requires this file"
        )

    def test_py_typed_is_empty_or_tiny(self) -> None:
        # PEP 561 says the marker is conventionally empty.
        size = PY_TYPED_PATH.stat().st_size
        assert size <= 64, f"py.typed should be empty/tiny; got {size} bytes"


# ── README license footer ───────────────────────────────────────────────────


class TestReadmeLicense:
    def test_readme_mentions_apache(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        assert "Apache-2.0" in text or "Apache 2.0" in text, (
            "Expected Apache 2.0 mention in README (license footer or install)"
        )

    def test_readme_has_no_mit_license_claim(self) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        # We're searching for the literal license claim. Be conservative:
        # token must be the explicit "MIT License" / "MIT-licensed" / "License: MIT"
        # phrase. Random uses of MIT (e.g. acronym in a citation) are fine.
        forbidden = [
            "MIT License",
            "MIT-licensed",
            "License: MIT",
            "Licensed under MIT",
            "license is MIT",
        ]
        for token in forbidden:
            assert token not in text, (
                f"README contains forbidden MIT license claim: {token!r}"
            )


# ── governance file references (optional in CI-3) ───────────────────────────


class TestGovernanceLicenseRefs:
    """If CONTRIBUTING.md / CODE_OF_CONDUCT.md exist, they must say Apache-2.0.

    Phase BR may create these later; here we just ensure that *if* they
    exist, they don't carry stale MIT language.
    """

    @pytest.mark.parametrize("name", ["CONTRIBUTING.md", "CODE_OF_CONDUCT.md"])
    def test_no_mit_in_governance(self, name: str) -> None:
        path = REPO_ROOT / name
        if not path.exists():
            pytest.skip(f"{name} not present; license clean-up deferred")
        text = path.read_text(encoding="utf-8")
        forbidden = ["MIT License", "MIT-licensed", "License: MIT"]
        for token in forbidden:
            assert token not in text, f"{name} contains forbidden MIT token: {token!r}"
