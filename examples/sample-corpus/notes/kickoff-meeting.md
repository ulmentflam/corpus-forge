# Skycast — Kickoff Meeting Notes

**Project:** Skycast, a tiny terminal weather CLI
**Attendees:** Rina (eng lead), Omar (CLI/UX), Pria (data)

## Agenda

1. What problem are we solving?
2. Scope for the first release.
3. Data source and caching strategy.
4. Owners and action items.

## Discussion

We want a single-binary command, `skycast <city>`, that prints the
current temperature and a one-line forecast without opening a browser.
The audience is people who live in the terminal and want a glance, not a
dashboard.

We agreed to keep the first release deliberately small:

- One subcommand: `skycast now <city>`.
- Output is a single line: location, temperature, and a short summary.
- Temperatures default to Celsius; `--fahrenheit` flips the unit.
- Responses are cached on disk so repeated lookups within a few minutes
  do not re-hit the upstream provider.

## Decisions

- **D-1:** Ship a read-only `now` command first; forecasts come later.
- **D-2:** Cache lookups for 10 minutes keyed by normalized city name.
- **D-3:** Celsius is the default unit; no auto-detection from locale in v1.
- **D-4:** Configuration lives in a single TOML file, not env vars.

## Action items

- **Rina** — stand up the `Reading` data model and the unit conversion
  helper. (Due: before next sync.)
- **Omar** — draft the `skycast now` argument parser and the one-line
  output format.
- **Pria** — write the FAQ and document the cache TTL behavior.
