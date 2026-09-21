#!/bin/zsh
set -euo pipefail

BASE="${0:A:h:h}"
OWNER_USER="mbeason"
OWNER_UID="501"
OWNER_GROUP="staff"
SYSTEM_PLIST_SOURCE="$BASE/launchd/system"
SYSTEM_PLIST_DIRECTORY="/Library/LaunchDaemons"
AGENT_DIRECTORY="/Users/$OWNER_USER/Library/LaunchAgents"
ROLLBACK_DIRECTORY="/Users/$OWNER_USER/Library/Application Support/Prime Observer/LaunchAgent Rollback"
ROTATION_SOURCE="$BASE/launchd/rotation"
ROTATION_DIRECTORY='/Library/Application Support/Prime Observer'
ROTATION_LABEL='com.mbeason.prime-observer.log-rotation'
ROTATION_PLIST="$SYSTEM_PLIST_DIRECTORY/$ROTATION_LABEL.plist"
LABELS=(
  com.mbeason.prime-observer.collector
  com.mbeason.prime-observer.transform
  com.mbeason.prime-observer.http
  com.mbeason.prime-observer.storage-backup
)

usage() {
  print "usage: $0 {install|start|stop|restart|status|rollback|install-rotation|uninstall-rotation|rotate-logs}"
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    print -u2 "This command modifies the system launchd domain; run it with sudo."
    exit 77
  fi
}

service_loaded() {
  /bin/launchctl print "system/$1" >/dev/null 2>&1
}

gui_domain_exists() {
  /bin/launchctl print "gui/$OWNER_UID" >/dev/null 2>&1
}

validate_configuration() {
  local actual_uid
  actual_uid="$(/usr/bin/id -u "$OWNER_USER")"
  [[ "$actual_uid" == "$OWNER_UID" ]] || {
    print -u2 "Expected $OWNER_USER to have UID $OWNER_UID; found $actual_uid."
    return 1
  }
  [[ "$BASE" == "/Users/$OWNER_USER/Projects/prime-observer" ]] || {
    print -u2 "Core plists target /Users/$OWNER_USER/Projects/prime-observer; current checkout is $BASE."
    return 1
  }
  [[ -x "$BASE/bin/run_collector.sh" ]] || {
    print -u2 "Collector wrapper is not executable: $BASE/bin/run_collector.sh"
    return 1
  }
  for label in $LABELS; do
    /usr/bin/plutil -lint "$SYSTEM_PLIST_SOURCE/$label.plist" >/dev/null
  done
}

bootstrap_system_services() {
  local label
  for label in $LABELS; do
    /bin/launchctl enable "system/$label"
    if ! service_loaded "$label"; then
      /bin/launchctl bootstrap system "$SYSTEM_PLIST_DIRECTORY/$label.plist"
    fi
  done
  /bin/launchctl kickstart -k system/com.mbeason.prime-observer.collector
  /bin/launchctl kickstart -k system/com.mbeason.prime-observer.transform
  /bin/launchctl kickstart -k system/com.mbeason.prime-observer.http
}

stop_system_services() {
  local label
  for label in $LABELS; do
    if service_loaded "$label"; then
      /bin/launchctl bootout "system/$label"
    fi
  done
}

restore_launchagents() {
  local label source target
  for label in $LABELS; do
    source="$ROLLBACK_DIRECTORY/$label.plist"
    target="$AGENT_DIRECTORY/$label.plist"
    /bin/launchctl enable "user/$OWNER_UID/$label" 2>/dev/null || true
    /bin/launchctl enable "gui/$OWNER_UID/$label" 2>/dev/null || true
    if [[ -f "$source" && ! -e "$target" ]]; then
      /bin/mv "$source" "$target"
      /usr/sbin/chown "$OWNER_USER:$OWNER_GROUP" "$target"
      /bin/chmod 644 "$target"
    fi
    if [[ -f "$target" ]] && gui_domain_exists; then
      /bin/launchctl bootstrap "gui/$OWNER_UID" "$target" 2>/dev/null || true
    fi
  done
}

install_core() {
  require_root
  validate_configuration

  local label source installed agent saved
  for label in $LABELS; do
    installed="$SYSTEM_PLIST_DIRECTORY/$label.plist"
    if [[ -e "$installed" ]] || service_loaded "$label"; then
      print -u2 "System service already exists for $label; use restart or rollback first."
      exit 1
    fi
    agent="$AGENT_DIRECTORY/$label.plist"
    saved="$ROLLBACK_DIRECTORY/$label.plist"
    if [[ -e "$agent" && -e "$saved" ]]; then
      print -u2 "Both active and rollback LaunchAgent plists exist for $label; refusing to overwrite either."
      exit 1
    fi
  done

  /usr/bin/install -d -o "$OWNER_USER" -g "$OWNER_GROUP" -m 700 "$ROLLBACK_DIRECTORY"
  /usr/bin/install -d -o "$OWNER_USER" -g "$OWNER_GROUP" -m 700 "$BASE/logs"

  trap 'print -u2 "Install failed; restoring the prior LaunchAgents."; stop_system_services || true; for label in $LABELS; do /bin/rm -f "$SYSTEM_PLIST_DIRECTORY/$label.plist"; done; restore_launchagents || true' ZERR

  for label in $LABELS; do
    agent="$AGENT_DIRECTORY/$label.plist"
    saved="$ROLLBACK_DIRECTORY/$label.plist"
    /bin/launchctl bootout "gui/$OWNER_UID/$label" 2>/dev/null || true
    /bin/launchctl disable "user/$OWNER_UID/$label" 2>/dev/null || true
    /bin/launchctl disable "gui/$OWNER_UID/$label" 2>/dev/null || true
    if [[ -f "$agent" ]]; then
      /bin/mv "$agent" "$saved"
      /usr/sbin/chown "$OWNER_USER:$OWNER_GROUP" "$saved"
      /bin/chmod 600 "$saved"
    fi
  done

  for label in $LABELS; do
    source="$SYSTEM_PLIST_SOURCE/$label.plist"
    installed="$SYSTEM_PLIST_DIRECTORY/$label.plist"
    /usr/bin/install -o root -g wheel -m 644 "$source" "$installed"
  done

  bootstrap_system_services
  trap - ZERR
  install_rotation
  print "Prime Observer core now belongs to the system launchd domain and runs as $OWNER_USER."
  "$0" status
}

install_rotation() {
  require_root
  /usr/bin/plutil -lint "$ROTATION_SOURCE/$ROTATION_LABEL.plist" >/dev/null
  /bin/zsh -n "$ROTATION_SOURCE/rotate_logs.sh"
  [[ ! -L "$ROTATION_DIRECTORY" ]] || { print -u2 "Refusing symlink $ROTATION_DIRECTORY"; return 1; }
  local source target
  for source in newsyslog.conf rotate_logs.sh; do
    target="$ROTATION_DIRECTORY/$source"
    [[ ! -L "$target" ]] || { print -u2 "Refusing symlink $target"; return 1; }
    if [[ -e "$target" ]] && ! /usr/bin/cmp -s "$ROTATION_SOURCE/$source" "$target"; then
      print -u2 "Refusing to overwrite differing $target"
      return 1
    fi
  done
  [[ ! -L "$ROTATION_PLIST" ]] || { print -u2 "Refusing symlink $ROTATION_PLIST"; return 1; }
  if [[ -e "$ROTATION_PLIST" ]] && ! /usr/bin/cmp -s "$ROTATION_SOURCE/$ROTATION_LABEL.plist" "$ROTATION_PLIST"; then
    print -u2 "Refusing to overwrite differing $ROTATION_PLIST"
    return 1
  fi
  /usr/bin/install -d -o root -g wheel -m 755 "$ROTATION_DIRECTORY"
  /usr/bin/install -o root -g wheel -m 644 "$ROTATION_SOURCE/newsyslog.conf" "$ROTATION_DIRECTORY/newsyslog.conf"
  /usr/bin/install -o root -g wheel -m 755 "$ROTATION_SOURCE/rotate_logs.sh" "$ROTATION_DIRECTORY/rotate_logs.sh"
  /usr/bin/install -o root -g wheel -m 644 "$ROTATION_SOURCE/$ROTATION_LABEL.plist" "$ROTATION_PLIST"
  if ! service_loaded "$ROTATION_LABEL"; then
    /bin/launchctl bootstrap system "$ROTATION_PLIST"
  fi
  print "Prime log rotation installed."
}

uninstall_rotation() {
  require_root
  local source target
  for source in newsyslog.conf rotate_logs.sh; do
    target="$ROTATION_DIRECTORY/$source"
    [[ ! -L "$target" ]] || { print -u2 "Refusing symlink $target"; return 1; }
    if [[ -e "$target" ]] && ! /usr/bin/cmp -s "$ROTATION_SOURCE/$source" "$target"; then
      print -u2 "Refusing to remove differing $target"
      return 1
    fi
  done
  [[ ! -L "$ROTATION_PLIST" ]] || { print -u2 "Refusing symlink $ROTATION_PLIST"; return 1; }
  if [[ -e "$ROTATION_PLIST" ]] && ! /usr/bin/cmp -s "$ROTATION_SOURCE/$ROTATION_LABEL.plist" "$ROTATION_PLIST"; then
    print -u2 "Refusing to remove differing $ROTATION_PLIST"
    return 1
  fi
  if service_loaded "$ROTATION_LABEL"; then
    /bin/launchctl bootout "system/$ROTATION_LABEL"
  fi
  if [[ -e "$ROTATION_DIRECTORY/http-reopen-pending" ]] && ! service_loaded com.mbeason.prime-observer.http; then
    /bin/launchctl bootstrap system "$SYSTEM_PLIST_DIRECTORY/com.mbeason.prime-observer.http.plist"
  fi
  /bin/rm -f "$ROTATION_PLIST" "$ROTATION_DIRECTORY/newsyslog.conf" "$ROTATION_DIRECTORY/rotate_logs.sh" "$ROTATION_DIRECTORY/http-reopen-pending"
  print "Prime log rotation uninstalled; archives remain in $BASE/logs."
}

show_rotation() {
  local state='not loaded' config='missing' script='missing' plist='missing'
  service_loaded "$ROTATION_LABEL" && state='loaded'
  if [[ -f "$ROTATION_DIRECTORY/newsyslog.conf" ]]; then
    config='installed'
    /usr/bin/cmp -s "$ROTATION_SOURCE/newsyslog.conf" "$ROTATION_DIRECTORY/newsyslog.conf" || config='installed, differs from source'
  fi
  if [[ -f "$ROTATION_DIRECTORY/rotate_logs.sh" ]]; then
    script='installed'
    /usr/bin/cmp -s "$ROTATION_SOURCE/rotate_logs.sh" "$ROTATION_DIRECTORY/rotate_logs.sh" || script='installed, differs from source'
  fi
  if [[ -f "$ROTATION_PLIST" ]]; then
    plist='installed'
    /usr/bin/cmp -s "$ROTATION_SOURCE/$ROTATION_LABEL.plist" "$ROTATION_PLIST" || plist='installed, differs from source'
  fi
  print "Log rotation: $state; config=$config; script=$script; plist=$plist; job=$ROTATION_PLIST"
  if [[ -e "$ROTATION_DIRECTORY/http-reopen-pending" ]]; then
    print "Log rotation: HTTP recovery pending"
  fi
  return 0
}

rollback_core() {
  require_root
  stop_system_services
  local label
  for label in $LABELS; do
    /bin/launchctl disable "system/$label" 2>/dev/null || true
    /bin/rm -f "$SYSTEM_PLIST_DIRECTORY/$label.plist"
  done
  restore_launchagents
  print "Prime Observer core system services were removed and saved LaunchAgents restored."
}

show_service() {
  local label="$1" domain="missing" details state runs exit_code pid
  if /bin/launchctl print "system/$label" >/dev/null 2>&1; then
    domain="system"
    details="$(/bin/launchctl print "system/$label")"
  elif /bin/launchctl print "gui/$OWNER_UID/$label" >/dev/null 2>&1; then
    domain="gui/$OWNER_UID"
    details="$(/bin/launchctl print "gui/$OWNER_UID/$label")"
  else
    print "${label##*.}: missing"
    return
  fi
  state="$(print -r -- "$details" | /usr/bin/awk -F' = ' '/^[[:space:]]*state =/{print $2; exit}')"
  runs="$(print -r -- "$details" | /usr/bin/awk -F' = ' '/^[[:space:]]*runs =/{print $2; exit}')"
  exit_code="$(print -r -- "$details" | /usr/bin/awk -F' = ' '/^[[:space:]]*last exit code =/{print $2; exit}')"
  pid="$(print -r -- "$details" | /usr/bin/awk -F' = ' '/^[[:space:]]*pid =/{print $2; exit}')"
  print "${label##*.}: loaded domain=$domain state=${state:-unknown} runs=${runs:-0} pid=${pid:-none} last_exit=${exit_code:-not-recorded}"
}

show_core_summary() {
  local label system_count=0 gui_count=0
  for label in $LABELS; do
    if /bin/launchctl print "system/$label" >/dev/null 2>&1; then
      (( system_count += 1 ))
    elif /bin/launchctl print "gui/$OWNER_UID/$label" >/dev/null 2>&1; then
      (( gui_count += 1 ))
    fi
  done
  if [[ "$system_count" -eq "${#LABELS[@]}" ]]; then
    print "Prime core: running (system ownership, user=$OWNER_USER)"
  elif [[ "$gui_count" -eq "${#LABELS[@]}" ]]; then
    print "Prime core: running with legacy GUI-session ownership (migration required)"
  else
    print "Prime core: degraded (system=$system_count gui=$gui_count expected=${#LABELS[@]})"
  fi
}

show_health() {
  /usr/bin/python3 - "$BASE" <<'PY'
import datetime as dt
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

base = Path(sys.argv[1])
now = dt.datetime.now(dt.timezone.utc)

try:
    with sqlite3.connect(base / "data/prime_observer.db", timeout=2) as connection:
        count, latest = connection.execute(
            "SELECT COUNT(*), MAX(observed_at) FROM raw_probe_observations"
        ).fetchone()
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    print(f"Storage: quick_check={quick} observations={count} latest={latest}")
except Exception as exc:
    print(f"Storage: degraded ({exc})")

latest_csv = base / "viz/latest.csv"
if latest_csv.exists():
    age = (now - dt.datetime.fromtimestamp(latest_csv.stat().st_mtime, dt.timezone.utc)).total_seconds()
    print(f"Transform: artifact_age_seconds={int(age)} path={latest_csv}")
else:
    print("Transform: missing viz/latest.csv")

try:
    with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=3) as response:
        print(f"Web: reachable status={response.status} endpoint=127.0.0.1:8000")
except Exception as exc:
    print(f"Web: unreachable ({exc})")

for name in ("nextdns_summary.json", "internet_conditions.json", "aps_power_context.json", "application_experience.json", "operator_assistant_generation_state.json"):
    path = base / "viz" / name
    if not path.exists():
        print(f"Optional {name}: missing")
        continue
    try:
        payload = json.loads(path.read_text())
        status = payload.get("status") or payload.get("overall_status") or "present"
        generated = payload.get("generated_at") or payload.get("updated_at") or "unknown"
        print(f"Optional {name}: status={status} updated={generated}")
    except Exception:
        print(f"Optional {name}: malformed")

replication = Path.home() / "Library/Application Support/Prime Observer/Backups/.icloud-replication-status.json"
if replication.exists():
    try:
        payload = json.loads(replication.read_text())
        print(f"Optional iCloud replication: status={payload.get('status', 'unknown')} updated={payload.get('last_attempt_at', 'unknown')}")
    except Exception:
        print("Optional iCloud replication: malformed status")
else:
    print("Optional iCloud replication: no status")
PY
  /usr/bin/python3 "$BASE/bin/storage.py" restore-latest --dry-run >/dev/null \
    && print "Backup: healthy restore_readiness=true" \
    || print "Backup: degraded restore_readiness=false"
  /usr/sbin/lsof -nP -iTCP:8000 -sTCP:LISTEN 2>/dev/null | /usr/bin/awk 'NR == 1 || NR == 2 {print}'
}

case "${1:-}" in
  install)
    install_core
    ;;
  start)
    require_root
    validate_configuration
    bootstrap_system_services
    ;;
  stop)
    require_root
    stop_system_services
    ;;
  restart)
    require_root
    validate_configuration
    stop_system_services
    bootstrap_system_services
    ;;
  status)
    show_core_summary
    for label in $LABELS; do show_service "$label"; done
    show_rotation
    show_health
    ;;
  install-rotation)
    install_rotation
    ;;
  uninstall-rotation)
    uninstall_rotation
    ;;
  rotate-logs)
    require_root
    "$ROTATION_DIRECTORY/rotate_logs.sh"
    ;;
  rollback)
    rollback_core
    ;;
  *)
    usage
    exit 64
    ;;
esac
