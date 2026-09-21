#!/bin/zsh
set -euo pipefail

STATE_DIR='/Library/Application Support/Prime Observer'
CONFIG="$STATE_DIR/newsyslog.conf"
MARKER="$STATE_DIR/http-reopen-pending"
HTTP_PLIST='/Library/LaunchDaemons/com.mbeason.prime-observer.http.plist'
HTTP_LABEL='system/com.mbeason.prime-observer.http'
HTTP_ERR='/Users/mbeason/Projects/prime-observer/logs/http.err'
HTTP_OUT='/Users/mbeason/Projects/prime-observer/logs/http.out'
OTHER_LOGS=(
  /Users/mbeason/Projects/prime-observer/logs/collector.out
  /Users/mbeason/Projects/prime-observer/logs/collector.err
  /Users/mbeason/Projects/prime-observer/logs/transform.out
  /Users/mbeason/Projects/prime-observer/logs/transform.err
  /Users/mbeason/Projects/prime-observer/logs/storage-backup.log
  /Users/mbeason/Projects/prime-observer/logs/nextdns-refresh.log
  /Users/mbeason/Projects/prime-observer/logs/operator-assistant-worker.log
)

[[ "$(id -u)" == 0 ]] || { print -u2 'Prime log rotation requires root'; exit 77; }
[[ -f "$CONFIG" ]] || { print -u2 "Missing $CONFIG"; exit 1; }

recover_http() {
  if [[ -f "$MARKER" ]]; then
    if ! /bin/launchctl print "$HTTP_LABEL" >/dev/null 2>&1; then
      /bin/launchctl bootstrap system "$HTTP_PLIST"
    fi
    /bin/rm -f "$MARKER"
  fi
}
trap 'recover_http' EXIT
recover_http

# Existing launchd logs predate this policy; narrow their modes immediately.
for log in "$HTTP_ERR" "$HTTP_OUT" "${OTHER_LOGS[@]}"; do
  [[ -L "$log" ]] && { print -u2 "Refusing symlink log: $log"; exit 1; }
  [[ -f "$log" ]] && /bin/chmod go-rwx "$log"
done

# A launchd StandardErrorPath is an open descriptor. Stop only HTTP before
# newsyslog renames/compresses its inode, then bootstrap it with a fresh FD.
preview="$(/usr/sbin/newsyslog -n -f "$CONFIG" "$HTTP_ERR" "$HTTP_OUT")"
if [[ "$preview" == *': trimming'* ]]; then
  if /bin/launchctl print "$HTTP_LABEL" >/dev/null 2>&1; then
    /usr/bin/touch "$MARKER"
    /bin/launchctl bootout "$HTTP_LABEL"
  fi
  /usr/sbin/newsyslog -f "$CONFIG" "$HTTP_ERR" "$HTTP_OUT"
  recover_http
fi
/usr/sbin/newsyslog -f "$CONFIG" "${OTHER_LOGS[@]}"
