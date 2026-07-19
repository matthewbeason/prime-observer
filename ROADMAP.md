# ROADMAP.md

## Purpose

This file records milestone-level project progression and the current repository
staging point. It is not a speculative feature list.

If a next step is not explicit in the repository, it is marked:

`Needs Matthew Review`

## Completed Milestones

Repository tags and release labels show this progression:

- `v0.1.0`: Initial release
- `v0.3.1`: Rename from `net-bakeoff` to Prime Observer
- `v0.3.12`: Attribution accuracy improvements
- `v0.3.13`: Baseline pattern awareness
- `v0.3.14`: WAN health refocus
- `v0.5.0`: Historical investigation workflow
- `v0.6.0`: Investigation workflow improvements
- `v0.7.2`: Dashboard and mobile observability refresh
- `v0.8.0`: Observation domain foundation
- `v0.8.1`: Bucket selection alignment
- `v0.8.2`: Dashboard operator polish
- `v0.9.0`: Internet Conditions external context

## Current Milestone

Current repository state is the `v0.9.0` release.

The repository explicitly says to live with `v0.9.0` for several days before
expanding functionality.

Uncommitted post-release work adds event-aligned automatic investigation
lifecycle and completed-event history. The current implementation milestone is
to harden that history before committing: atomic write-once snapshots, explicit
invalid snapshot reporting, catalog metadata, renderer recovery behavior, and
aligned tests/docs.

Current watch period:

- observe whether noticeability misses stable-but-noticeable problems
- observe whether turbulence or pattern confidence create misleading signals
- observe whether DNS Security and Internet Conditions add useful context
- observe whether the current dashboard scanning and investigation workflow
  improve operator understanding

## Next Logical Milestone

After Investigation History hardening, the next planned capability is direct
links/bookmarks for historical investigations.

Before implementing additional external providers, the next conceptual
architecture step is to clarify Environmental Context boundaries and evaluate
Environmental Context evidence domains and candidate providers against them.

Future provider evaluation may consider domains such as:

- Internet infrastructure
- ISP infrastructure
- Power infrastructure
- Weather
- Regional hazards
- regional service disruptions

APS may be considered only as a future candidate for the Power Infrastructure
evidence domain. It is not an approved implementation commitment.

## Deferred Or Explicitly Avoided Areas

The repository explicitly says not to expand into these areas yet:

- raw DNS logs
- domain lists as a product expansion
- device-level DNS analytics
- alerts or notifications
- LLM explanations
- weather correlation
- power outage correlation
- ISP status correlation
- major `viz/index.html` refactor
- database-backed storage replacing canonical artifacts
- event comparison before history is hardened
- recurrence or similarity detection before history is hardened

If a future database becomes useful, it should be an optional search/index
projection that consumes canonical JSON/CSV artifacts. It should not replace the
artifact evidence model.
