# Application Experience Probes

`bin/fetch_application_experience.py` performs local synthetic application-level
checks and writes `viz/application_experience.json`.

The collector is separate from `bin/transform_latest.py`. The transform reads the
artifact when present but never performs DNS, TCP, TLS, HTTPS, OpenRouter, or
provider API calls.

## Checks

The artifact includes three DNS transactions:

- direct query to the configured primary resolver
- direct query to the configured secondary resolver
- query through the system resolver

It also includes one lightweight HTTPS transaction to a configurable endpoint.
The HTTPS check records DNS lookup duration when measurable, TCP connect time,
TLS setup time, time to first byte, total duration, HTTP status, timeout, and
failure category.

## Configuration

Configuration is provider-neutral and can come from process environment or the
repo-local `.env.application_experience` file.

Supported variables:

- `PRIME_OBSERVER_APP_PROBE_DNS_HOSTNAME`
- `PRIME_OBSERVER_APP_PROBE_PRIMARY_RESOLVER`
- `PRIME_OBSERVER_APP_PROBE_SECONDARY_RESOLVER`
- `PRIME_OBSERVER_APP_PROBE_HTTPS_URL`
- `PRIME_OBSERVER_APP_PROBE_TIMEOUT_SECONDS`
- `PRIME_OBSERVER_APP_PROBE_STALE_AFTER_SECONDS`

The collector records only safe endpoint metadata. Query strings, credentials,
and API keys are not written to the artifact.

## Impact Use

Fresh application evidence can refine only `estimated_user_impact`:

- healthy system DNS and HTTPS transactions reduce estimated impact
- direct resolver timeouts count as timeout evidence
- system DNS, TCP, TLS, or HTTP failures weigh more than latency alone
- broad transaction failures can produce likely or severe estimated impact
- stale, malformed, or missing artifacts do not affect current impact

Observed user impact remains separate and is still derived from fresh reports or
symptom evidence, not synthetic checks.

Phase 4.5 surfaces this evidence in the dashboard and Investigation page. The
renderer shows compact operator-facing states for system DNS, direct primary
resolver, direct secondary resolver, and HTTPS transaction. Raw timings remain
secondary or collapsed; browser code does not run probes and does not call
providers.
