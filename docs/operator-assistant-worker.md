# Operator Assistant Worker

Prime Observer generates Operator Assistant interpretation asynchronously. The
collector and deterministic transform never wait for OpenRouter, and the browser
never calls OpenRouter.

## Flow

```text
collector
  -> bin/transform_latest.py
  -> deterministic investigation artifacts
  -> viz/operator_assistant_input.json
  -> viz/operator_assistant_generation_state.json: pending

launchd/com.mbeason.prime-observer.operator-assistant.plist
  -> bin/run_operator_assistant_worker.py
  -> bin/build_operator_assistant_output.py
  -> validated, atomic viz/operator_assistant_output.json publication
  -> atomic generation-state update
```

`bin/build_operator_assistant_input.py`, including the scheduled optional-context
refresh path, also writes `pending` state when its normalized semantic hash
changes. Freshness-only rebuilds leave the existing state unchanged.

## Schedule

The tracked LaunchAgent runs the worker at load and every 60 seconds. It does not
use `KeepAlive`, so provider or configuration failure cannot create a tight
launchd retry loop. The worker exits successfully when there is no work, retry
backoff has not elapsed, work is permanently failed for the current hash, or
another worker owns the generation lock.

## State Machine

The generated state artifact uses schema 2 and these worker states:

```text
pending -> generating -> complete
pending -> generating -> retry_wait
pending -> generating, when another worker holds the active lock
retry_wait -> generating, after next_retry_at
retry_wait -> failed, after the cross-run attempt limit
```

A changed semantic input hash resets work to `pending` with `attempt_count: 0`.
A matching valid output moves pending work directly to `complete` without a
provider request. `complete` and `failed` are terminal for the same input hash.

State fields include `input_hash`, `requested_at`, `updated_at`,
`attempt_count`, and, when applicable, `started_at`, `completed_at`,
`next_retry_at`, `last_error_category`, `output_input_hash`, `worker_id`, and
`output_validation_result`.

Worker invocation result codes are separate from persisted states. They include
`no_work`, `lock_held`, `generation_completed`, `retry_scheduled`, and
`terminal_failure`.

## Retry And Concurrency

The existing output producer performs at most three bounded attempts inside one
worker run. The worker performs at most three generation runs for one semantic
input hash. Cross-run delays are 5 minutes after the first failed run and 15
minutes after the second. The 60-second LaunchAgent schedule only checks whether
work is due.

Transport failures, HTTP 429/500/502/503/504, and truncated or invalid provider
responses are retryable while attempts remain. Missing OpenRouter configuration,
missing charter, invalid input, non-retryable HTTP responses, and output contract
failure become `failed` for the current hash and require configuration or a new
semantic input before another provider request.

`viz/.operator_assistant_generation.lock` uses exclusive creation to suppress
duplicate requests. A lock older than 900 seconds is stale and may be replaced.
State and output JSON writes are atomic. A failed run never deletes or overwrites
`viz/operator_assistant_output.json`.

## Configuration

The worker uses the same configuration as the explicit output producer:

- process environment variables, or
- repo-local `.env.openrouter`

At minimum, configure `OPENROUTER_API_KEY`. Optional values include
`OPENROUTER_MODEL`, `OPENROUTER_TIMEOUT_SECONDS`, and
`OPENROUTER_MAX_TOKENS`. Never put secrets in the LaunchAgent plist.

## Manual Operation

Run one worker cycle:

```bash
/usr/bin/python3 bin/run_operator_assistant_worker.py
```

Run the producer directly for troubleshooting:

```bash
python3 bin/build_operator_assistant_output.py
python3 bin/build_operator_assistant_output.py --force
```

Inspect state and logs:

```bash
python3 -m json.tool viz/operator_assistant_generation_state.json
launchctl print gui/$(id -u)/com.mbeason.prime-observer.operator-assistant
tail -n 50 logs/operator-assistant-worker.log
```

Provider failure remains isolated from collector and transform health. While work
is pending, waiting, or failed, Investigation renders a valid matching LLM
assessment when available and otherwise renders the deterministic
`operator_brief` fallback.

## Install

These commands are proposed for review and are not run by repository validation:

```bash
mkdir -p logs ~/Library/LaunchAgents
cp launchd/com.mbeason.prime-observer.operator-assistant.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/com.mbeason.prime-observer.operator-assistant.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mbeason.prime-observer.operator-assistant.plist
launchctl kickstart -k gui/$(id -u)/com.mbeason.prime-observer.operator-assistant
```

If an older copy is already loaded, reload it:

```bash
launchctl bootout gui/$(id -u)/com.mbeason.prime-observer.operator-assistant
cp launchd/com.mbeason.prime-observer.operator-assistant.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mbeason.prime-observer.operator-assistant.plist
```

Verify repository paths and last exit status:

```bash
plutil -p ~/Library/LaunchAgents/com.mbeason.prime-observer.operator-assistant.plist
launchctl print gui/$(id -u)/com.mbeason.prime-observer.operator-assistant
```

Disable without deleting generated evidence or last-known-good output:

```bash
launchctl bootout gui/$(id -u)/com.mbeason.prime-observer.operator-assistant
rm -f ~/Library/LaunchAgents/com.mbeason.prime-observer.operator-assistant.plist
```
