"""Skycast — a tiny terminal weather helper.

Celsius is the canonical internal unit; conversion to Fahrenheit happens
only at the edge when formatting output. See ``notes/architecture.md``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Reading:
    """A single current-weather observation for one city."""

    city: str
    temperature_c: float
    summary: str


def to_fahrenheit(celsius: float) -> float:
    """Convert a Celsius temperature to Fahrenheit."""
    return celsius * 9 / 5 + 32


def normalize_city(name: str) -> str:
    """Normalize a city name into a stable cache key.

    Collapses surrounding whitespace and lowercases the result so that
    ``"  Reykjavik "`` and ``"reykjavik"`` map to the same entry.
    """
    return " ".join(name.split()).lower()


def format_reading(reading: Reading, *, fahrenheit: bool = False) -> str:
    """Render a :class:`Reading` as a single human-facing line.

    The unit conversion is applied here and nowhere else, keeping the
    rest of the code unit-agnostic.
    """
    if fahrenheit:
        temperature = to_fahrenheit(reading.temperature_c)
        unit = "F"
    else:
        temperature = reading.temperature_c
        unit = "C"
    return f"{reading.city}: {temperature:.1f}°{unit}, {reading.summary}"
