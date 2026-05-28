"""SR-G6: Duration-spec parser for ``--max-scan-age``.

Converts human-friendly age specs (bare seconds, or ``Ns / Nm / Nh / Nd``)
to a float of seconds.  Exposed as a public function so both the CLI
callback and the integration layer (SR-T7) can share the same parser.
"""

from __future__ import annotations

_SUFFIX_MULTIPLIERS: dict[str, float] = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def parse_scan_age_spec(s: str) -> float:
    """Parse a duration spec string and return a float of seconds.

    Accepted formats:
    - ``"0"`` / ``"0s"``           → ``0.0``
    - Bare integer/float string    → seconds (e.g. ``"60"`` → ``60.0``)
    - Suffix ``s`` = seconds, ``m`` = minutes (x60),
      ``h`` = hours (x3600), ``d`` = days (x86400)
    - Fractional coefficient supported: ``"1.5m"`` → ``90.0``

    Raises:
        ValueError: for empty/whitespace strings, alpha-only strings,
            unknown suffixes, double suffixes, bare suffixes (no coefficient),
            and negative values.
    """
    if not s or not s.strip():
        raise ValueError(f"Invalid scan age spec: {s!r} — must not be empty or whitespace")

    s = s.strip()

    # Check for a known single-character suffix at the end.
    if s[-1] in _SUFFIX_MULTIPLIERS:
        suffix = s[-1]
        coefficient_str = s[:-1]
        if not coefficient_str:
            raise ValueError(
                f"Invalid scan age spec: {s!r} — suffix '{suffix}' has no numeric coefficient"
            )
        try:
            coefficient = float(coefficient_str)
        except ValueError:
            raise ValueError(
                f"Invalid scan age spec: {s!r} — '{coefficient_str}' is not a valid number"
            ) from None
        result = coefficient * _SUFFIX_MULTIPLIERS[suffix]
        if result < 0:
            raise ValueError(
                f"Invalid scan age spec: {s!r} — result {result} is negative;"
                " use a non-negative value"
            )
        return result

    # No recognised suffix — try bare number (seconds).
    try:
        result = float(s)
    except ValueError:
        raise ValueError(
            f"Invalid scan age spec: {s!r} — not a number and not a recognised suffix "
            f"(valid suffixes: {', '.join(sorted(_SUFFIX_MULTIPLIERS))})"
        ) from None

    if result < 0:
        raise ValueError(
            f"Invalid scan age spec: {s!r} — negative values are not allowed;"
            " use 0 to always rescan"
        )
    return result
