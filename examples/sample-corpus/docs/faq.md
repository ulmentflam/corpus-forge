# Skycast — FAQ

## How do I look up the weather for a city?

Run `skycast now <city>`. For example, `skycast now Reykjavik` prints a
single line with the location, the current temperature, and a short
summary.

## Why does it default to Celsius?

Celsius is the canonical internal unit (see the architecture note). At
kickoff we decided against locale auto-detection for the first release,
so the default is fixed. Pass `--fahrenheit` to switch the display unit.

## Does Skycast cache results?

Yes. Each lookup is cached on disk for ten minutes, keyed by the
normalized city name. Repeated lookups within that window are served from
the cache and do not contact the upstream provider.

## What happens if the cache is corrupt or unreadable?

The cache is best-effort. If Skycast cannot read a cached entry it falls
back to a fresh fetch instead of failing the command.

## Can I get a multi-day forecast?

Not in the first release. The kickoff scope is a read-only `now`
command; forecasts are planned for a later version.

## Where does configuration live?

In a single TOML file (see `config/settings.toml`). The first release
does not read configuration from environment variables.
