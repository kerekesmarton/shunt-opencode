#!/bin/bash
# OpenCode transport for shunt: replaces the Portal AiKA plumbing (aika.sh) and
# the former Muse transport (muse.sh) with one headless turn against the
# shunt worker server. Same function names, so bulk-read and code-write only
# change their source line.
#
# The instructions and corpus are piped over stdin to `opencode run --attach`
# so there is no ARG_MAX ceiling and no --file attachment quirks. Events are
# parsed as JSON: only a completed, non-empty answer is accepted.

SHUNT_MAX_PAYLOAD_BYTES="${SHUNT_MAX_PAYLOAD_BYTES:-600000}"
SHUNT_TIMEOUT_SECONDS="${SHUNT_TIMEOUT_SECONDS:-300}"
SHUNT_OPENCODE_BIN="${SHUNT_OPENCODE_BIN:-opencode}"
SHUNT_OPENCODE_MODEL="${SHUNT_OPENCODE_MODEL:-}"

SHUNT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SHUNT_TMPFILES=()
shunt_tmpfile() {
  local f
  f=$(mktemp) || return 1
  SHUNT_TMPFILES+=("$f")
  trap 'rm -rf "${SHUNT_TMPFILES[@]}"' EXIT
  printf -v "$1" '%s' "$f"
}

shunt_preflight() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 not found. The shunt OpenCode transport needs python3." >&2
    return 1
  fi
  if [ -z "$SHUNT_OPENCODE_SERVER" ] && ! command -v "$SHUNT_OPENCODE_BIN" >/dev/null 2>&1; then
    echo "Error: $SHUNT_OPENCODE_BIN not found. Install OpenCode or set SHUNT_OPENCODE_BIN." >&2
    return 1
  fi
  if [ -z "$SHUNT_OPENCODE_MODEL" ]; then
    echo "Error: set SHUNT_OPENCODE_MODEL to a configured provider/model (e.g. openrouter/google/gemini-3.1-flash-lite)." >&2
    return 1
  fi
}

# Runs one headless OpenCode turn under a mode's instructions and prints the answer.
#   $1 mode name (bulk-reader | code-writer)
#   $2 file holding the message
shunt_invoke() {
  python3 "$SHUNT_LIB_DIR/worker.py" invoke "$1" "$2"
}
