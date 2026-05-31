# NextDNS Summary LaunchAgent

This LaunchAgent refreshes the optional NextDNS summary every 30 minutes.

The fetcher is fail-safe: if NextDNS configuration is missing, the API is unavailable, or the request fails, `bin/fetch_nextdns_summary.py` writes an `unavailable` summary to `viz/nextdns_summary.json`. The LaunchAgent also treats those fetch failures as non-fatal so future scheduled refreshes continue running.

## Files

- Source plist: `launchd/com.mbeason.prime-observer.nextdns-refresh.plist`
- Installed plist: `~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist`
- Log file: `logs/nextdns-refresh.log`

## Install

Run from the repository root:

```bash
mkdir -p logs ~/Library/LaunchAgents
cp launchd/com.mbeason.prime-observer.nextdns-refresh.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
launchctl kickstart -k gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
```

## Check Status

```bash
launchctl print gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
tail -n 50 logs/nextdns-refresh.log
```

## Unload And Remove

```bash
launchctl bootout gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
rm -f ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
```

The generated summary and log can be removed separately if needed:

```bash
rm -f viz/nextdns_summary.json logs/nextdns-refresh.log
```
