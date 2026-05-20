#!/usr/bin/env bash
# corpus-forge — Postgres + pgvector bootstrap (Debian / Ubuntu).
#
# Adds the PGDG apt repo, installs postgresql-N + postgresql-N-pgvector,
# creates a role + database, enables the vector extension, edits
# listen_addresses + pg_hba.conf, and reloads the service. Idempotent
# end-to-end: re-running with the same inputs is a no-op.
#
# Dual mode:
#   * TTY  → interactive prompts for any missing values.
#   * non-TTY (pipe / CI) → values must come from flags or CF_PG_* env vars;
#     missing required values cause exit 2.
#
# Recommended pre-commit lint: shellcheck (not bundled — install separately).
# Supported targets: Debian 12, Ubuntu 22.04 / 24.04. Other distros exit 3.
#
# See: docs/deployment/postgres.md  for the canonical guide.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults / argv parsing
# ---------------------------------------------------------------------------

PROG_NAME="$(basename "$0")"
CF_OS_RELEASE="${CF_OS_RELEASE:-/etc/os-release}"

DB="${CF_PG_DB:-}"
USR="${CF_PG_USER:-}"
PASSWORD="${CF_PG_PASSWORD:-}"
CIDR="${CF_PG_CIDR:-}"
PG_VERSION="${CF_PG_VERSION:-17}"
NO_LISTEN=0
DRY_RUN=0
QUIET=0

print_help() {
  cat <<EOF
${PROG_NAME} — bootstrap PostgreSQL + pgvector on Debian/Ubuntu for corpus-forge.

USAGE:
  ${PROG_NAME} [FLAGS]

FLAGS:
  --help              Show this help and exit 0.
  --dry-run           Print the command plan; do not execute apt/systemctl/psql.
  --db NAME           Database name. Env: CF_PG_DB.
  --user NAME         Role name. Env: CF_PG_USER.
  --password STR      Role password. Env: CF_PG_PASSWORD.
  --cidr CIDR         pg_hba.conf source range (e.g. 192.168.1.0/24). Env: CF_PG_CIDR.
  --pg-version N      Postgres major version (default 17). Env: CF_PG_VERSION.
  --no-listen         Skip listen_addresses edit (keep default 'localhost').
  --quiet             Suppress non-essential progress output.

REQUIRED INPUTS:
  When stdin is not a TTY, --db / --user / --password / --cidr (or the
  matching CF_PG_* env vars) MUST be provided. Missing values exit 2.

EXIT CODES:
  0   Success (or successful --dry-run / --help).
  2   Missing required input on non-TTY invocation.
  3   Unsupported distro (this script targets Debian / Ubuntu only).

ENVIRONMENT:
  CF_OS_RELEASE   Override path to /etc/os-release (for tests).
  CF_PG_DB / CF_PG_USER / CF_PG_PASSWORD / CF_PG_CIDR / CF_PG_VERSION
                   Non-interactive value sources (see FLAGS).

EXAMPLES:
  # Bare-metal interactive run.
  sudo bash ${PROG_NAME}

  # CI / unattended.
  CF_PG_DB=corpus CF_PG_USER=corpus CF_PG_PASSWORD=... \\
      CF_PG_CIDR=10.0.0.0/24 sudo -E bash ${PROG_NAME}

  # See what would happen, change nothing.
  CF_PG_DB=corpus CF_PG_USER=corpus CF_PG_PASSWORD=... \\
      CF_PG_CIDR=10.0.0.0/24 bash ${PROG_NAME} --dry-run

See docs/deployment/postgres.md for the manual procedure and tuning notes.
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --help) print_help; exit 0 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --db)
      if (( $# < 2 )); then
        echo "${PROG_NAME}: --db requires a value" >&2
        print_help
        exit 64
      fi
      DB="$2"; shift 2 ;;
    --user)
      if (( $# < 2 )); then
        echo "${PROG_NAME}: --user requires a value" >&2
        print_help
        exit 64
      fi
      USR="$2"; shift 2 ;;
    --password)
      if (( $# < 2 )); then
        echo "${PROG_NAME}: --password requires a value" >&2
        print_help
        exit 64
      fi
      PASSWORD="$2"; shift 2 ;;
    --cidr)
      if (( $# < 2 )); then
        echo "${PROG_NAME}: --cidr requires a value" >&2
        print_help
        exit 64
      fi
      CIDR="$2"; shift 2 ;;
    --pg-version)
      if (( $# < 2 )); then
        echo "${PROG_NAME}: --pg-version requires a value" >&2
        print_help
        exit 64
      fi
      PG_VERSION="$2"; shift 2 ;;
    --no-listen) NO_LISTEN=1; shift ;;
    --quiet) QUIET=1; shift ;;
    *)
      echo "${PROG_NAME}: unknown flag: $1" >&2
      echo "Run '${PROG_NAME} --help' for usage." >&2
      exit 64
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
  if (( QUIET == 0 )); then
    echo "[corpus-forge bootstrap] $*"
  fi
}

emit() {
  # Print a command (dry-run) or execute it (live). Uses argv-style
  # execution to avoid shell injection risks.
  if (( DRY_RUN == 1 )); then
    echo "  $*"
  else
    "$@"
  fi
}

prompt_if_tty() {
  # $1 = current value, $2 = prompt label, $3 = (optional) "secret" → -s
  local current="$1" label="$2" secret="${3:-}"
  if [[ -n "${current}" ]]; then
    echo "${current}"
    return 0
  fi
  if [[ -t 0 ]]; then
    local val
    if [[ "${secret}" == "secret" ]]; then
      read -r -s -p "${label}: " val
      echo "" >&2
    else
      read -r -p "${label}: " val
    fi
    echo "${val}"
    return 0
  fi
  # Non-TTY, no value → caller decides exit 2.
  echo ""
  return 1
}

prompt_password_with_confirm() {
  # $1 = current value (env/flag). When provided, used verbatim — no
  # confirmation step (caller is responsible for their own value).
  # When stdin is a TTY and the value is empty, prompts twice and
  # loops until both entries match.
  local current="$1"
  if [[ -n "${current}" ]]; then
    echo "${current}"
    return 0
  fi
  if [[ ! -t 0 ]]; then
    echo ""
    return 1
  fi
  local pw1 pw2
  while :; do
    read -r -s -p "Role password: " pw1
    echo "" >&2
    if [[ -z "${pw1}" ]]; then
      echo "Empty password not allowed." >&2
      continue
    fi
    read -r -s -p "Confirm password: " pw2
    echo "" >&2
    if [[ "${pw1}" == "${pw2}" ]]; then
      echo "${pw1}"
      return 0
    fi
    echo "Passwords don't match — try again." >&2
  done
}

require_value() {
  # $1 = current, $2 = env var name, $3 = flag name
  if [[ -z "${1}" ]]; then
    echo "${PROG_NAME}: missing required value: set ${2} or pass ${3}" >&2
    exit 2
  fi
}

# ---------------------------------------------------------------------------
# Distro guard — Debian/Ubuntu only
# ---------------------------------------------------------------------------

if [[ ! -r "${CF_OS_RELEASE}" ]]; then
  echo "${PROG_NAME}: cannot read ${CF_OS_RELEASE} — see docs/deployment/postgres.md" >&2
  exit 3
fi

OS_ID="$(grep -E '^ID=' "${CF_OS_RELEASE}" | head -1 | cut -d= -f2 | tr -d '"' || true)"
OS_ID_LIKE="$(grep -E '^ID_LIKE=' "${CF_OS_RELEASE}" | head -1 | cut -d= -f2 | tr -d '"' || true)"

case "${OS_ID}" in
  debian|ubuntu) : ;;
  *)
    case "${OS_ID_LIKE}" in
      *debian*|*ubuntu*) : ;;
      *)
        echo "${PROG_NAME}: unsupported distro '${OS_ID}'. This script targets Debian/Ubuntu only." >&2
        echo "See docs/deployment/postgres.md for the RHEL / Arch / macOS path." >&2
        exit 3
        ;;
    esac
    ;;
esac

# ---------------------------------------------------------------------------
# Resolve required values (flag → env → prompt → exit 2)
# ---------------------------------------------------------------------------

DB="$(prompt_if_tty "${DB}" "Database name" || true)"
require_value "${DB}" CF_PG_DB --db

USR="$(prompt_if_tty "${USR}" "Role / user name" || true)"
require_value "${USR}" CF_PG_USER --user

PASSWORD="$(prompt_password_with_confirm "${PASSWORD}" || true)"
require_value "${PASSWORD}" CF_PG_PASSWORD --password

CIDR="$(prompt_if_tty "${CIDR}" "pg_hba CIDR (e.g. 192.168.1.0/24)" || true)"
require_value "${CIDR}" CF_PG_CIDR --cidr

# Sanity: PG_VERSION must be an integer.
if ! [[ "${PG_VERSION}" =~ ^[0-9]+$ ]]; then
  echo "${PROG_NAME}: --pg-version must be an integer (got '${PG_VERSION}')" >&2
  exit 64
fi

# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

if (( DRY_RUN == 1 )); then
  log "DRY-RUN — printing planned command sequence; no changes will be made."
else
  log "Bootstrapping PostgreSQL ${PG_VERSION} + pgvector for db='${DB}' user='${USR}' cidr='${CIDR}'"
fi

# ---- Step 1: PGDG apt repo (idempotent — keyring + .list file checked) ----

PGDG_KEYRING="/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg"
PGDG_LIST="/etc/apt/sources.list.d/pgdg.list"
# Resolve the Debian/Ubuntu codename at SCRIPT-write time and embed it
# in the sources.list line literally. APT does NOT shell-expand entries
# in /etc/apt/sources.list.d/*.list — the `$(lsb_release -cs)` form
# would end up as a literal string in the file and apt rejects it with
# a 404 like:
#   Err: https://apt.postgresql.org/pub/repos/apt $(lsb_release Release
#        404  Not Found
# Earlier revisions of this script were wrong about that.
#
# Source: parse /etc/os-release's VERSION_CODENAME — always present on
# Debian 12+, Ubuntu 18.04+. Avoids depending on `lsb_release` which is
# installed by Step 1 *below* this code (chicken-and-egg).
PGDG_CODENAME="$(
  grep -E '^VERSION_CODENAME=' "${CF_OS_RELEASE}" \
    | head -1 \
    | cut -d= -f2 \
    | tr -d '"' \
    || true
)"
if [[ -z "${PGDG_CODENAME}" ]]; then
  echo "${PROG_NAME}: cannot read VERSION_CODENAME from ${CF_OS_RELEASE}" >&2
  exit 4
fi
PGDG_LINE="deb [signed-by=${PGDG_KEYRING}] https://apt.postgresql.org/pub/repos/apt ${PGDG_CODENAME}-pgdg main"

log "Step 1/7: Ensure PGDG apt repo is configured."

# Pre-existing PGDG entry detection.
#
# An LXC / VM may already have apt.postgresql.org configured via a
# different file (e.g. /etc/apt/sources.list.d/pgdg.sources or any
# user-managed list pointing at /etc/apt/keyrings/pgdg.gpg). Writing
# OUR own pgdg.list on top creates a Signed-By conflict like:
#
#   E: Conflicting values set for option Signed-By regarding source
#      https://apt.postgresql.org/pub/repos/apt/ trixie-pgdg:
#      /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
#      != /etc/apt/keyrings/pgdg.gpg
#
# To avoid that, scan every file under /etc/apt/sources.list.d/ AND
# /etc/apt/sources.list itself for any line referencing
# apt.postgresql.org. If we find one, skip Step 1's write entirely —
# the host is already configured to fetch from PGDG and apt-get update
# will pick up postgresql-${PG_VERSION} from there in Step 2.
#
# ``grep -rlI``: -l = list filenames only, -I = skip binary files.
# 2>/dev/null suppresses "no such file or directory" if either path
# is absent (e.g. on macOS during tests).
PGDG_EXISTING="$(
  grep -rlI 'apt\.postgresql\.org' \
    /etc/apt/sources.list.d/ \
    /etc/apt/sources.list \
    2>/dev/null \
    | head -1 \
    || true
)"

if [[ -n "${PGDG_EXISTING}" ]]; then
  log "  PGDG repo already configured in ${PGDG_EXISTING} — skipping apt-source write."
  if (( DRY_RUN == 1 )); then
    echo "  [skipped] PGDG repo already present in ${PGDG_EXISTING}"
  fi
else
  emit apt-get install -y curl ca-certificates gnupg lsb-release
  emit install -d /usr/share/postgresql-common/pgdg
  if (( DRY_RUN == 1 )); then
    echo "  [ -s ${PGDG_KEYRING} ] || curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o ${PGDG_KEYRING}"
  else
    [ -s "${PGDG_KEYRING}" ] || curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o "${PGDG_KEYRING}"
  fi
  if (( DRY_RUN == 1 )); then
    echo "  echo \"${PGDG_LINE}\" > ${PGDG_LIST}"
  else
    echo "${PGDG_LINE}" > "${PGDG_LIST}"
  fi
fi
emit apt-get update

# ---- Step 2: Install Postgres + pgvector ----

log "Step 2/7: Install postgresql-${PG_VERSION} + postgresql-${PG_VERSION}-pgvector."
emit apt-get install -y "postgresql-${PG_VERSION}" "postgresql-${PG_VERSION}-pgvector"
emit systemctl enable --now postgresql

# ---- Step 3: Create role (idempotent via \gexec at top-level) ----
#
# psql -v variables are NOT substituted inside DO $$ … $$ blocks (the body
# is sent to the server as a single string literal). Instead we build the
# CREATE ROLE statement at the top level, where :'name' substitution works,
# and use \gexec to execute the resulting row only when the role is absent.
# format() does the SQL-side identifier / literal quoting so credentials
# with special characters are handled safely.

log "Step 3/7: Create role '${USR}' if it does not exist."
ROLE_SQL=$(cat <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'username', :'password') AS cmd
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'username')
\gexec
SQL
)
if (( DRY_RUN == 1 )); then
  echo "  sudo -u postgres psql -v ON_ERROR_STOP=1 -v username=\"${USR}\" -v password=\"***\" <<'SQL'"
  printf '  %s\n' "${ROLE_SQL//$'\n'/$'\n'  }"
  echo "  SQL"
else
  sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -v "username=${USR}" -v "password=${PASSWORD}" <<<"${ROLE_SQL}"
fi

# ---- Step 4: Create database (idempotent: skip if exists) ----

log "Step 4/7: Create database '${DB}' if it does not exist."
if (( DRY_RUN == 1 )); then
  echo "  sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname = '${DB}'\" | grep -q 1 || sudo -u postgres psql -v ON_ERROR_STOP=1 -v dbname=\"${DB}\" -v owner=\"${USR}\" -c \"CREATE DATABASE :dbname OWNER :owner\""
else
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = '${DB}'" | grep -q 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${DB}\" OWNER \"${USR}\""
  fi
fi

# ---- Step 5: Enable pgvector extension in the target DB ----

log "Step 5/7: CREATE EXTENSION IF NOT EXISTS vector in '${DB}'."
if (( DRY_RUN == 1 )); then
  echo "  sudo -u postgres psql -v ON_ERROR_STOP=1 -d \"${DB}\" -c \"CREATE EXTENSION IF NOT EXISTS vector\""
else
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB}" -c "CREATE EXTENSION IF NOT EXISTS vector"
fi

# ---- Step 6: listen_addresses + pg_hba.conf ----

PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/main"
HBA_LINE="host    ${DB}             ${USR}             ${CIDR}            scram-sha-256"

if (( NO_LISTEN == 1 )); then
  log "Step 6/7: --no-listen set — leaving listen_addresses at default."
else
  log "Step 6/7: Edit listen_addresses in ${PG_CONF_DIR}/postgresql.conf."
  if (( DRY_RUN == 1 )); then
    echo "  sed -i \"s/^#\\?listen_addresses *=.*/listen_addresses = '*'/\" ${PG_CONF_DIR}/postgresql.conf"
  else
    sed -i "s/^#\\?listen_addresses *=.*/listen_addresses = '*'/" "${PG_CONF_DIR}/postgresql.conf"
  fi
fi
log "       Append pg_hba.conf entry for ${CIDR} (idempotent — grep before append)."
if (( DRY_RUN == 1 )); then
  echo "  grep -qF \"${HBA_LINE}\" ${PG_CONF_DIR}/pg_hba.conf || echo \"${HBA_LINE}\" >> ${PG_CONF_DIR}/pg_hba.conf"
else
  grep -qF "${HBA_LINE}" "${PG_CONF_DIR}/pg_hba.conf" || echo "${HBA_LINE}" >> "${PG_CONF_DIR}/pg_hba.conf"
fi

# ---- Step 7: reload Postgres ----

log "Step 7/7: Reload postgresql."
emit systemctl reload postgresql

# ---- Final ----

log ""
log "Done. Suggested DSN:"
log "  postgresql://${USR}:<password>@<host>:5432/${DB}"
log ""
log "Next: run 'corpus-forge migrate' to install the schema."
