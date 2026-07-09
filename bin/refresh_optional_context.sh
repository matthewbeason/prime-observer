#!/bin/zsh
set -u

BASE="${PRIME_OBSERVER_BASE:-/Users/mbeason/prime-observer}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

timestamp() {
  /bin/date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  print -- "[$(timestamp)] $*"
}

run_step() {
  local label="$1"
  local script_path="$2"

  log "Starting ${label} refresh."
  "$PYTHON_BIN" "$script_path"
  local rc=$?
  if [[ $rc -eq 0 ]]; then
    log "${label} refresh completed."
  else
    log "${label} refresh completed with non-fatal exit code ${rc}."
  fi
}

cd "$BASE" || exit 1

run_step "NextDNS summary" "$BASE/bin/fetch_nextdns_summary.py"
run_step "Internet Conditions" "$BASE/bin/fetch_cloudflare_radar.py"
run_step "APS power context" "$BASE/bin/fetch_aps_power_context.py"

log "Optional context refresh finished."
