# HANDOFF.md

## Current State

- Branch: `main`
- Current release: `v0.9.0`
- Latest tag: `v0.9.0`
- `HEAD` is ahead of the `v0.9.0` release tag and includes later lifecycle work

Prime Observer currently ships:

- deterministic health modeling over local telemetry
- observation-backed attribution and episode semantics
- automatic current-event investigation generation
- immutable completed-event investigation history
- manual requested-window investigation generation and viewing
- optional NextDNS summary context
- optional Cloudflare Radar Internet Conditions context

## Recent Completed Work

Repository-backed recent milestones:

- `v0.8.0`: Observation domain foundation
- `v0.8.1`: Bucket selection alignment
- `v0.8.2`: Dashboard operator polish
- `v0.9.0`: Internet Conditions external context
- post-`v0.9.0` uncommitted work: event-aligned automatic investigation
  lifecycle and hardened completed-event history

Recent commits before `v0.9.0` show this sequence:

- added Internet Conditions context
- refreshed it in the scheduled workflow
- enriched the Internet Conditions artifact and dashboard presentation
- prepared the `v0.9.0` release

Next conceptual milestone:

- define Environmental Context architecture before evaluating additional
  providers

## Current Architecture State

Current artifact flow:

- telemetry history in `data/bakeoff_YYYYMMDD.csv`
- `bin/transform_latest.py` generates dashboard, observation, mutable current
  investigation, write-once completed snapshot, and investigation catalog
  artifacts
- `bin/build_investigation.py` generates manual requested-window evidence
  artifacts
- optional fetchers generate DNS and Internet Conditions summaries
- `viz/index.html` and `viz/investigate.html` consume generated local files

Current projection state:

- `viz/latest.csv` remains the dashboard telemetry input
- `viz/network_attribution.json` remains the backward-compatible attribution
  export
- `viz/observations.json` is the repository-described authoritative Observation
  projection for deterministic semantics Prime Observer owns
- `viz/investigation.json` is the mutable current investigation artifact
- `viz/investigations/<event-id>.json` contains immutable completed-event
  snapshots published atomically and never overwritten
- `viz/investigation_catalog.json` is a generated projection over valid
  snapshots and any preserved invalid snapshot metadata

## Active Watch Period

The repository currently says to live with `v0.9.0` for several days before
expanding functionality.

The current uncommitted work should be completed by hardening Investigation
History before committing. After that, the next planned capability is direct
links/bookmarks for historical investigations. Event comparison and recurrence
or similarity detection remain future work.

Generated JSON and CSV artifacts remain canonical. No database is needed at the
current local scale; any future PostgreSQL or Supabase work should be an optional
artifact consumer/index rather than a replacement for canonical artifacts.

Watch items currently named in the repository:

- stable-but-noticeable false negatives
- noisy-but-masked false positives
- whether DNS Security adds context or clutter
- whether Pattern confidence feels trustworthy
- whether turbulence is informative or distracting
- whether attribution confidence matches real experience
- whether the compact Connection card and refocused WAN Health Summary improve
  scanning
- whether investigation navigation and nearby-event discovery improve evidence
  review without implying correlation

## Resume Checklist

When resuming work:

1. Confirm the current tag, branch, and working tree state.
2. Read `README.md`, `AGENTS.md`, `ROADMAP.md`, and `DECISIONS.md`.
3. Read the specific docs and code for the area being changed.
4. Preserve the repository’s architecture boundaries and terminology.
5. Mark any unsupported assumption as `Needs Matthew Review`.

## Needs Matthew Review

- What milestone should follow the current `v0.9.0` observation period.
- Whether the repository wants a standing handoff file updated each release or
  only when work pauses midstream.
