#!/bin/zsh
set -euo pipefail

BASE="/Users/mbeason/net-bakeoff"
PHASE_FILE="$BASE/phase.txt"

PHASE="UNKNOWN"
if [[ -f "$PHASE_FILE" ]]; then
  PHASE="$(tr -d ' \n\r\t' < "$PHASE_FILE")"
fi

# Ensure python is available
PY="/usr/bin/python3"
SCRIPT="$BASE/bin/collector.py"

# Run one interval-collection (collector.py should do ONE loop or one sample set per invocation)
# If your current collector runs forever, we’ll tweak it to run once per launchd tick.
PHASE="$PHASE" "$PY" "$SCRIPT"
