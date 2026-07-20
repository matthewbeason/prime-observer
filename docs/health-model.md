# Prime Observer Health Model

Prime Observer's canonical health model is factual telemetry evidence about
network experience. It defines sample classification, WAN target groups, LAN
evidence, bad moments, attribution inputs, noticeability inputs, investigation
windows, scope facts, lifecycle facts, and deterministic fallback guidance. The
LLM interpretation layer may explain likely meaning and operator actions from
those facts, but it does not replace them.

## Model Scope

Prime Observer answers six local observability questions:

- Is the network healthy?
- Is behavior unusual?
- Is the issue local or upstream?
- Is the issue sustained?
- Would users notice?
- Is there useful DNS/security context?

The health model is implemented in three main places:

- `bin/transform_latest.py` generates `viz/latest.csv` and
  `viz/network_attribution.json` for the current 24-hour dashboard window.
- `viz/index.html` renders the dashboard from `viz/latest.csv`.
- `bin/investigation_model.py` generates automatic current-event investigation
  evidence in `viz/investigation.json` during the transform cycle.
- `bin/build_investigation.py` generates manual requested-window historical
  evidence in `viz/investigation.json` when explicitly run.

## Samples

A sample is one raw telemetry row read from a historical
`data/bakeoff_YYYYMMDD.csv` file and normalized into a dashboard or
investigation row. A usable sample has:

- `ts` / `ts_utc`: sample timestamp
- `host`: monitored target
- `phase` / `phase_label`: connection path label
- `p95_ms`: p95 latency
- `jitter_ms`: jitter
- `loss_pct`: packet loss
- `target_label`: human-readable target name
- `target_class`: target ownership class

The collection interval is whatever interval is present in the local telemetry
files. The health model treats samples as ordered observations and derives
persistence from consecutive samples within the same path and target grouping.

Target ownership is factual target metadata:

- `internet_probe`: general WAN/path probes, currently Cloudflare `1.1.1.1`
  and Quad9 `9.9.9.9`.
- `resolver_probe`: resolver path probes, currently NextDNS primary
  `45.90.28.134` and NextDNS secondary `45.90.30.134`.
- `gateway_probe`: local LAN gateway, currently `192.168.1.1`.
- `unknown_probe`: unrecognized targets retained only where safely usable.

`internet_probe` and `resolver_probe` are WAN target groups. `gateway_probe` is
LAN evidence.

## Bad Sample

A raw WAN sample is bad when any Prime Observer WAN threshold is exceeded:

- p95 latency greater than `140 ms`
- jitter greater than `50 ms`
- packet loss greater than `1%`

The thresholds are the same for `internet_probe` and `resolver_probe` samples.
They do not apply to `gateway_probe` samples. LAN uses a separate p95 elevation
threshold described below.

Implementation sources:

- `bin/transform_latest.py`: `WAN_BAD` and `is_wan_bad_row()`
- `viz/index.html`: `WAN_BAD` and `isWanBadRow()`
- `bin/build_investigation.py`: `WAN_BAD` and `is_wan_bad()`

## Sustained Sample

A sustained WAN sample is a raw bad WAN sample that is part of a consecutive bad
run meeting `WAN_BAD_PERSISTENCE = 2`.

Persistence is tracked by path and target grouping:

- Dashboard-side code tracks streaks by path and `target_class`.
- Investigation generation tracks streaks by path, `target_class`, and host.
- Attribution export generation tracks streaks by path and `target_class`.

The canonical health model is: sustained degradation requires at least two
consecutive raw bad samples within the same path and WAN target grouping.

Implementation sources:

- `bin/transform_latest.py`: `mark_persistent_wan_bad()`
- `viz/index.html`: `markPersistentWanBad()`
- `bin/build_investigation.py`: `mark_sustained_wan()`

## Automatic Investigation Lifecycle

Automatic investigations use the same WAN bad thresholds and
`WAN_BAD_PERSISTENCE` rule. An isolated abnormal sample is not a confirmed
incident. A confirmed incident starts at the first anomalous sample in the run
and is confirmed at the sample where persistence is satisfied.

Lifecycle states:

- `active`: sustained degradation has been confirmed and recovery has not
  started.
- `recovering`: healthy persistence has been satisfied after the last anomalous
  sample, but the stable recovery window is not complete.
- `complete`: recovery stayed stable for the configured stable window.
- `none`: no sustained incident is present in the available evidence.

Recovery constants are centralized in `bin/health_model.py`:

- `RECOVERY_HEALTHY_PERSISTENCE = 2`
- `RECOVERY_STABLE_WINDOW_MINUTES = 15`

Recovery timestamps:

- `recovery_candidate_at`: first healthy sample after the last anomalous sample.
- `recovery_started_at`: set only after `RECOVERY_HEALTHY_PERSISTENCE` healthy
  samples.
- `recovered_at`: set only after the stable recovery window completes.

One healthy sample does not establish recovery. A new anomaly before completion
cancels the recovery candidate and keeps the same event active.

Automatic window semantics:

- `baseline`: available evidence strictly before `first_anomalous_at`; it may be
  unavailable and may be classified as already unstable.
- `degradation`: evidence associated with the selected sustained event beginning
  at `first_anomalous_at`.
- `recovery`: evidence beginning at `recovery_candidate_at`; unavailable until
  post-event evidence exists, provisional while recovering, complete only when
  `recovered_at` is set.

Automatic phase summaries distinguish representative behavior from isolated
maximum excursions:

- `typical_p95_ms` is the selected target class representative p95 for the phase
  and is used for first-pass operator comparison.
- sustained-bad sample and bucket counts explain why a phase is classified as
  degradation.
- `max_p95_ms` remains visible separately as the maximum excursion.
- A stable baseline can have a higher isolated maximum than the degradation
  phase without being worse operationally, because persistence and affected
  bucket counts drive degradation classification.

Legacy `periods.before`, `periods.during`, and `periods.after` remain in the
artifact for compatibility, but automatic mode derives them from baseline,
degradation, and recovery instead of arbitrary CLI timestamps.

## Turbulence Sample

Turbulence is noisy WAN degradation that has enough raw bad evidence to be
notable but does not meet the sustained persistence rule.

The current turbulence threshold is:

- `TURBULENCE_MIN_RAW_BAD = 4`
- no sustained bad sample in the bucket
- maximum raw bad run is below `WAN_BAD_PERSISTENCE`

Turbulence is informational. It is not the same as sustained degradation.

Implementation sources:

- `bin/transform_latest.py`: `classify_buckets()`
- `viz/index.html`: `classifyBuckets()` for target-group buckets and
  `buildCompositeWanBuckets()` for dashboard composite buckets
- `bin/build_investigation.py`: `classify_buckets()`

## WAN Target Groups

WAN evidence is separated into two target groups:

- Internet probes represent general WAN/path reachability.
- Resolver probes represent the path to configured DNS resolver endpoints.

They exist so resolver-path latency is not silently blended into general
internet/path latency. Both groups are WAN evidence, but they can support
different factual observations.

## LAN Evidence

LAN evidence is gateway probe evidence from `192.168.1.1`.

LAN evidence means the local gateway path was measured at the same time as WAN
targets. It can show whether local gateway p95 latency was elevated during a
WAN observation window.

LAN evidence does not, by itself, prove Wi-Fi failure, router failure, ISP
failure, or user impact. Prime Observer uses it only as factual evidence for
local elevation or local stability.

## WAN Bad Moment

Canonical definition:

A WAN bad moment is a 15-minute time bucket in which one or more WAN target
groups exhibit sustained degradation according to Prime Observer thresholds.

This definition includes both `internet_probe` and `resolver_probe` target
groups. A bucket with turbulence but no sustained degradation is a turbulence
bucket, not a WAN bad moment.

Implementation match:

- `bin/build_investigation.py` matches this composite definition in
  `wan_buckets`, where buckets are keyed by `target_class` and report
  sustained bad counts for internet and resolver probes.
- `viz/network_attribution.json`, generated by `bin/transform_latest.py`,
  matches this composite definition for target-group summaries and recent
  attribution evidence counts.
- `viz/index.html` renders the dashboard heatmap from composite WAN buckets
  built from both `internet_probe` and `resolver_probe` target-group buckets.
  LAN evidence remains separate and is shown only as gateway evidence in
  selected-bucket details, attribution, and the LAN chart.

Dashboard heatmap semantics:

- dark gray means at least one WAN target group had sustained degradation in
  that 15-minute bucket
- amber means one or more WAN target groups had turbulence without sustained
  degradation
- tooltip evidence reports internet probe counts, resolver probe counts,
  composite raw/sustained counts, factual raw threshold reasons, and LAN
  elevated sample counts for the same interval

## LAN Elevated State

LAN elevation is detected from gateway probe p95 latency.

Current threshold behavior:

- `LAN_BAD_P95 = 120 ms`
- recent/current attribution considers LAN bad when at least three recent LAN
  samples are elevated and elevated samples are more than 20% of recent LAN
  samples
- incident/window classification considers LAN bad over an incident interval
  with the same count and rate rule

LAN stable evidence currently means gateway samples exist in the interval and
none exceed the LAN p95 threshold.

Implementation sources:

- `bin/transform_latest.py`: `LAN_BAD_P95`,
  `compute_recent_attribution()`, and `lan_evidence_for_interval()`
- `viz/index.html`: `LAN_BAD_P95` and `computeAttribution()`
- `bin/build_investigation.py`: investigation summaries report
  `elevated_p95_count` but do not perform attribution decisions

## Attribution Assessment

Attribution assessment is factual classification of recent or window-level
evidence. It consumes:

- Internet probe WAN evidence
- Resolver probe WAN evidence
- LAN gateway evidence

Current recent-attribution outputs are:

- `No network issue detected`
- `Likely upstream (ISP / path)`
- `Likely local (LAN / Wi-Fi)`
- `Mixed evidence`
- `Inconclusive`
- `No recent data`

The export maps labels into machine statuses such as `no_issue_detected`,
`likely_upstream`, `likely_local`, `mixed_evidence`, and `inconclusive`.

Recent attribution uses the last 15 minutes:

- no recent LAN or WAN samples: `No recent data`, low confidence
- LAN bad plus WAN sustained degradation or turbulence: compare WAN evidence
  points against LAN elevated sample count
- LAN bad without WAN degradation or turbulence: likely local, high confidence
- WAN sustained degradation or turbulence without LAN bad: likely upstream,
  high confidence for sustained degradation and medium confidence for
  turbulence
- neither LAN nor WAN degraded: no issue, high confidence
- unresolved mixed evidence defaults to mixed/inconclusive wording

WAN evidence points currently combine sustained bad samples, sustained bad
bucket count weighted by persistence, and turbulence buckets.

Window attribution in `viz/network_attribution.json` uses sustained WAN
target-group incidents across the 24-hour observation window. Each incident is
classified against LAN evidence during the same interval:

- LAN bad: likely local
- LAN stable: likely upstream
- no LAN samples: inconclusive
- mixed or insufficient LAN samples: inconclusive

Window confidence is based on whether incidents consistently support one class
or contain mixed/inconclusive evidence.

## User Noticeability

User Noticeability is a dashboard-only deterministic score intended to estimate
whether current WAN conditions may be noticeable to users.

Current scoring inputs:

- last 15 minutes of composite WAN p95 latency from internet and resolver
  probe samples
- median p95
- 95th percentile p95
- count and fraction of p95 samples above `120 ms`
- count of p95 samples above `180 ms`
- consecutive elevated p95 samples

Current forecast inputs:

- last 60 minutes of composite WAN bad moment buckets
- last 60 minutes of composite WAN turbulence buckets
- latest composite WAN p95 spike streak above `120 ms`

Outputs:

- score from `0` to `100`
- label: `Probably masked`, `Low risk`, `Possibly noticeable`, or
  `Likely noticeable`
- forecast: `Stable`, `Watch`, `Risk`, or no-data state

Implementation source:

- `viz/index.html`: `getNoticeabilitySummary()`,
  `computeUserNoticeability()`, and `computeNoticeabilityForecast()`

LAN evidence and DNS context are not Noticeability inputs. Pattern Awareness
remains internet-probe historical context and is displayed separately from
Noticeability scoring.

## Investigation Window

An investigation window is the requested event interval supplied to
`bin/build_investigation.py`.

The context window is the requested event interval plus `--pad-minutes` before
and after the event. The default pad is 30 minutes.

Bucket generation:

- WAN samples in before, during, and after periods are marked for raw bad and
  sustained bad evidence.
- WAN buckets are 15-minute buckets keyed by target class.
- Buckets report raw bad count, sustained bad count, maximum raw bad run,
  turbulence, target hosts, max p95, max jitter, and max loss.

Event generation:

- one `requested_window` event is created from CLI input
- one `wan_bucket_observation` event is created for each bucket with raw bad,
  sustained bad, or turbulence evidence
- navigation and event-neighborhood metadata are deterministic and factual only

Relationship to WAN bad moments:

- Investigation buckets are aligned with the canonical composite WAN target
  group model.
- Investigation events include both sustained bad buckets and turbulence/raw
  observation buckets. A `wan_bucket_observation` is factual evidence; it is
  not always a canonical WAN bad moment.

## Pattern Awareness

Pattern Awareness compares current internet-probe p95 latency against
historical WAN p95 behavior for the same hour of day.

Current implementation uses `baseline_p95`, `baseline_delta_pct`, and
`baseline_sample_count` from `bin/transform_latest.py` and renders confidence
from `baseline_sample_count` in `viz/index.html`.

Pattern Awareness answers "is this unusual?" It is contextual evidence, not an
alert and not attribution.

## DNS Security Context

DNS context comes only from generated `viz/nextdns_summary.json`. It is optional
summary evidence and is not part of WAN bad moment, LAN elevation, attribution,
or noticeability calculations.

Prime Observer must continue functioning when DNS context is missing, stale,
invalid, or unavailable.
