# Security Policy

## Supported versions

corpus-forge follows [PEP 440](https://peps.python.org/pep-0440/)
version numbers. During the beta phase, only the latest beta of the
`0.1.x` line receives security updates.

| Version | Supported          |
| ------- | ------------------ |
| `0.1.x` | :white_check_mark: |
| `< 0.1` | :x:                |

Once `0.1.0` ships as a stable release, this table will move to the
latest stable minor + the previous minor.

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, email **`evan@jwo3.io`** with:

1. A description of the vulnerability and where it lives in the code
   (file path, function, version).
2. Reproduction steps or a minimal proof-of-concept.
3. The impact you've observed (information disclosure, code execution,
   denial of service, etc).
4. Any suggested mitigation.

You should receive an acknowledgment within **5 business days**. If you
don't, please follow up — your message may have been caught by a spam
filter.

## Disclosure process

1. Reporter emails the contact above.
2. Maintainer acknowledges receipt within 5 business days.
3. Maintainer investigates and confirms / triages the report.
4. A fix is developed in a private branch; the reporter is kept in the
   loop.
5. A patch release is cut. The release notes credit the reporter
   (unless they request anonymity).
6. Coordinated public disclosure within 90 days of acknowledgment, or
   sooner if the vulnerability is already actively exploited.

## Out of scope

- Issues that require physical access to the user's machine.
- Issues in third-party dependencies — please report those upstream.
  We will, however, update our pins promptly once a CVE is published.
- Issues in example configurations (`examples/`) that require the user
  to misconfigure secrets.

## Hardening recommendations for users

- Run the daemon under a dedicated unprivileged service account.
- Store API keys for embedders (OpenAI, etc.) in a secrets manager,
  not in `config.toml` directly. The `dotenv` integration reads
  `${VAR}` references at load time so secrets can stay in a file with
  `0600` permissions outside the repo.
- If you expose the MCP server beyond `stdio`, gate the transport
  behind a local-only socket. The v1 MCP surface is stdio-only.

Thanks for helping keep corpus-forge and its users safe.
