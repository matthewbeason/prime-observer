# Optional Context Refresh LaunchAgent

This LaunchAgent refreshes Prime Observer's optional local context artifacts every 30 minutes.

The refresh wrapper is fail-safe:

- if NextDNS configuration is missing, the API is unavailable, or the request fails, `bin/fetch_nextdns_summary.py` writes an `unavailable` summary to `viz/nextdns_summary.json`
- if the Cloudflare token is missing, the API is unavailable, or the request fails, `bin/fetch_cloudflare_radar.py` writes an `unavailable` summary to `viz/internet_conditions.json`
- if APS data is unavailable or malformed, `bin/fetch_aps_power_context.py` writes an `unavailable` summary to `viz/aps_power_context.json`
- if the current investigation-derived assistant input is missing or invalid, `bin/build_operator_assistant_input.py` writes a bounded local input artifact that preserves the failure state
- if OpenRouter is not configured, the request fails, or the provider response is invalid, `bin/build_operator_assistant_output.py` writes an `unavailable` review artifact to `viz/operator_assistant_output.json`
- if the normalized operator-assistant evidence hash is unchanged from an existing successful review, `bin/build_operator_assistant_output.py` skips the OpenRouter request and preserves the existing review artifact
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

`bin/refresh_optional_context.sh` runs the optional context and assistant refreshers in order:

1. `bin/fetch_nextdns_summary.py`
2. `bin/fetch_cloudflare_radar.py`
3. `bin/fetch_aps_power_context.py`
4. `bin/build_operator_assistant_input.py`
5. `bin/build_operator_assistant_output.py`

Both scripts load local configuration from the repo root if present:

- `.env.nextdns`
- `.env.cloudflare`
- `.env.openrouter`

Do not store API tokens in the plist. `bin/fetch_cloudflare_radar.py` reads `CLOUDFLARE_API_TOKEN` from the process environment or the repo-local `.env.cloudflare` file, which keeps launchd-compatible configuration out of shell profiles.

`bin/build_operator_assistant_output.py` reads `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` from the process environment or the repo-local `.env.openrouter` file. If no model is configured, it defaults to `google/gemini-3.5-flash`. The generated review stays local in `viz/operator_assistant_output.json`, and the browser reads only that artifact. Even though the wrapper rebuilds the input package every run, the OpenRouter request is skipped only when the normalized evidence hash and requested model both match an existing successful review.

## Check Status

```bash
launchctl print gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
tail -n 50 logs/nextdns-refresh.log
```

The log should show all five refresh steps. No token values are printed. When evidence and requested model are unchanged, the log should also show that the OpenRouter request was skipped because the assistant input hash matched an existing successful review for that model.

## Unload And Remove

```bash
launchctl bootout gui/$(id -u)/com.mbeason.prime-observer.nextdns-refresh
rm -f ~/Library/LaunchAgents/com.mbeason.prime-observer.nextdns-refresh.plist
```

The generated summaries and log can be removed separately if needed:

```bash
rm -f viz/nextdns_summary.json viz/internet_conditions.json viz/aps_power_context.json viz/operator_assistant_input.json viz/operator_assistant_output.json logs/nextdns-refresh.log
```
