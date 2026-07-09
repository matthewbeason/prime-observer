# Optional Context Refresh LaunchAgent

This LaunchAgent refreshes Prime Observer's optional local context artifacts every 30 minutes.

The refresh wrapper is fail-safe:

- if NextDNS configuration is missing, the API is unavailable, or the request fails, `bin/fetch_nextdns_summary.py` writes an `unavailable` summary to `viz/nextdns_summary.json`
- if the Cloudflare token is missing, the API is unavailable, or the request fails, `bin/fetch_cloudflare_radar.py` writes an `unavailable` summary to `viz/internet_conditions.json`
- if APS data is unavailable or malformed, `bin/fetch_aps_power_context.py` writes an `unavailable` summary to `viz/aps_power_context.json`
- either failure remains non-fatal so future scheduled refreshes continue running

## Files

- Source plist: `launchd/com.mbeason.prime-observer.nextdns-refresh.plist`
- Installed plist: `~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist`
- Refresh wrapper: `bin/refresh_optional_context.sh`
- Log file: `logs/nextdns-refresh.log`

## Install

Run from the repository root:

```bash
mkdir -p logs ~/Library/LaunchAgents
cp launchd/com.mbeason.prime-observer.nextdns-refresh.plist ~/Library/LaunchAgents/
chmod +x bin/refresh_optional_context.sh
plutil -lint ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
launchctl kickstart -k gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
```

`bin/refresh_optional_context.sh` runs the optional context refreshers in order:

1. `bin/fetch_nextdns_summary.py`
2. `bin/fetch_cloudflare_radar.py`
3. `bin/fetch_aps_power_context.py`

Both scripts load local configuration from the repo root if present:

- `.env.nextdns`
- `.env.cloudflare`

Do not store API tokens in the plist. `bin/fetch_cloudflare_radar.py` reads `CLOUDFLARE_API_TOKEN` from the process environment or the repo-local `.env.cloudflare` file, which keeps launchd-compatible configuration out of shell profiles.

## Check Status

```bash
launchctl print gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
tail -n 50 logs/nextdns-refresh.log
```

The log should show all three refresh steps. No token values are printed.

## Unload And Remove

```bash
launchctl bootout gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
rm -f ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
```

The generated summaries and log can be removed separately if needed:

```bash
rm -f viz/nextdns_summary.json viz/internet_conditions.json viz/aps_power_context.json logs/nextdns-refresh.log
```
