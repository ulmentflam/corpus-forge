# Skycast — Architecture Note

A short design note for the Skycast weather CLI. Skycast is intentionally
small: one command, one data model, a thin cache.

## Components

- **CLI front-end** — parses `skycast now <city>` plus the `--fahrenheit`
  flag and prints a single formatted line.
- **Provider client** — fetches a current `Reading` for a city from the
  upstream weather provider. Wrapped behind one function so the provider
  can be swapped later.
- **Cache** — an on-disk key/value store. The key is the normalized city
  name; the value is a serialized `Reading` plus a fetch timestamp.
- **Formatter** — turns a `Reading` into the human-facing summary line and
  applies the Celsius-or-Fahrenheit unit choice.

## Data flow

```
skycast now "Reykjavik"
        │
        ▼
  normalize city name  ──►  cache hit?  ──yes──►  format & print
        │                       │
        │                       no
        ▼                       ▼
  (cache miss)            provider client fetch
                               │
                               ▼
                        store Reading + timestamp
                               │
                               ▼
                          format & print
```

## The Reading model

A `Reading` is the single value object that flows through the system. It
carries the city, the temperature in Celsius (the canonical internal
unit), and a short summary string such as "light rain". The formatter
converts to Fahrenheit only at the edge, so the rest of the code never
has to reason about units.

## Cache semantics

Entries live for ten minutes (the TTL agreed at kickoff, decision D-2).
A read past the TTL is treated as a miss and triggers a fresh fetch. The
cache is best-effort: if the store is unreadable, Skycast falls back to a
direct fetch rather than failing the command.
