# Optional Context Refresh LaunchAgent

This LaunchAgent refreshes Prime Observer's optional local context artifacts every 30 minutes.

The refresh wrapper is fail-safe:

- if NextDNS configuration is missing, the API is unavailable, or the request fails, `bin/fetch_nextdns_summary.py` writes an `unavailable` summary to `viz/nextdns_summary.json`
- if the Cloudflare token is missing, the API is unavailable, or the request fails, `bin/fetch_cloudflare_radar.py` writes an `unavailable` summary to `viz/internet_conditions.json`
- if APS data is unavailable or malformed, `bin/fetch_aps_power_context.py` writes an `unavailable` summary to `viz/aps_power_context.json`
- if the current investigation-derived assistant input is missing or invalid, `bin/build_operator_assistant_input.py` writes a bounded local input artifact that preserves the failure state
- either failure remains non-fatal so future scheduled refreshes continue running

The scheduled wrapper does not invoke `bin/build_operator_assistant_output.py`
or call OpenRouter. If optional context changes the normalized semantic input,
`bin/build_operator_assistant_input.py` marks generation pending for the separate
worker documented in `docs/operator-assistant-worker.md`. Freshness-only context
changes do not create new work.

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

`bin/refresh_optional_context.sh` runs the optional context and assistant-input refreshers in order:

1. `bin/fetch_nextdns_summary.py`
2. `bin/fetch_cloudflare_radar.py`
3. `bin/fetch_aps_power_context.py`
4. `bin/build_operator_assistant_input.py`

The provider scripts load local configuration from the repo root if present:

- `.env.nextdns`
- `.env.cloudflare`

Do not store API tokens in the plist. `bin/fetch_cloudflare_radar.py` reads `CLOUDFLARE_API_TOKEN` from the process environment or the repo-local `.env.cloudflare` file, which keeps launchd-compatible configuration out of shell profiles.

The wrapper rebuilds `viz/operator_assistant_input.json` but does not build or
replace valid `viz/operator_assistant_output.json`. Changed semantic input is
consumed on the next separate worker run. The worker reads OpenRouter
configuration independently, reuses matching valid output by input hash and
model, and records provider/configuration failure in generation state instead of
replacing useful prior interpretation.

## Check Status

```bash
launchctl print gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
tail -n 50 logs/nextdns-refresh.log
```

The log should show all four refresh steps. No token values are printed. It
should not show an Operator Assistant output step or any OpenRouter request.

## Unload And Remove

```bash
launchctl bootout gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
rm -f ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
```

The generated summaries and log can be removed separately if needed:

```bash
rm -f viz/nextdns_summary.json viz/internet_conditions.json viz/aps_power_context.json viz/operator_assistant_input.json viz/operator_assistant_output.json logs/nextdns-refresh.log
```
