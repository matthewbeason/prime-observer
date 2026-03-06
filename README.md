# Prime Observer

**Network Observability & Experience Engine**

Prime Observer is a lightweight, local-first network telemetry system
designed to measure *real user experience* across internet providers ---
not just speed tests.

It was built to answer a simple question:

> "Which connection actually feels better to use?"

Instead of focusing on raw bandwidth, Prime Observer measures latency
behavior, instability, and real-world "bad moments" over time.

------------------------------------------------------------------------

# Purpose

Prime Observer compares upstream providers (e.g., Fiber vs 5G) by
observing:

-   WAN latency behavior (p95)
-   Jitter and instability
-   Frequency of "bad moments"
-   LAN vs WAN correlation
-   Real experience over time (not burst tests)

The goal is **decision clarity**, not benchmarking vanity metrics.

------------------------------------------------------------------------

# Philosophy

### Bad Moment

A time bucket where latency crosses calibrated thresholds that would
impact real usage:

-   video calls
-   streaming
-   interactive applications
-   remote work

These moments represent *real degradation in experience*.

------------------------------------------------------------------------

### Experience Over Speed

A connection that is fast but unstable often **feels worse** than a
slower but stable connection.

Prime Observer prioritizes:

-   consistency
-   jitter control
-   latency distribution

over peak bandwidth.

------------------------------------------------------------------------

### Local First

Prime Observer runs entirely on your machine.

No:

-   cloud backend
-   SaaS platform
-   telemetry sharing

All data stays local.

------------------------------------------------------------------------

# Architecture

Collector (30s) \| v data/bakeoff_YYYYMMDD.csv \| v Transform (60s) \| v
viz/latest.csv \| v Local HTTP Server \| v D3 Dashboard

The system continuously gathers telemetry, aggregates metrics, and
renders experience analytics locally.

------------------------------------------------------------------------

# Components

  Component        Purpose
  ---------------- ------------------------------------------------
  `collector`      Captures latency telemetry (LAN + WAN targets)
  `transform`      Aggregates, buckets, and computes metrics
  `viz`            Local web UI displaying experience analytics
  `phase.txt`      Indicates current upstream provider
  `LaunchAgents`   Automates continuous collection

------------------------------------------------------------------------

# Metrics Observed

Prime Observer computes experience metrics including:

-   WAN p95 latency (median & 95th)
-   Jitter (95th percentile)
-   Bad moment rate
-   Bad minutes per hour
-   LAN ↔ WAN correlation
-   Provider comparison summary
-   Heatmap of instability over time

These metrics focus on **user experience quality**, not bandwidth
benchmarks.

------------------------------------------------------------------------

# Repository Structure

prime-observer/ │ ├── bin/ │ ├── run_collector.sh │ ├──
transform_latest.py │ └── restart_net_bakeoff.sh │ ├── data/ \# Raw
telemetry (generated) ├── logs/ \# Service logs (generated) │ ├── viz/ │
├── index.html │ ├── d3.min.js │ └── latest.csv \# Current dataset
(generated) │ ├── phase.txt \# Current ISP phase └── README.md

Generated files are ignored via `.gitignore`.

------------------------------------------------------------------------

# Requirements

-   macOS or Linux
-   Python 3.x
-   basic shell environment

Optional:

-   `launchctl` for automated collection (macOS)

No database required.\
Telemetry is stored as **flat CSV files**.

------------------------------------------------------------------------

# Quick Start

## Clone the repository

``` bash
git clone https://github.com/matthewbeason/prime-observer.git
cd prime-observer
```

------------------------------------------------------------------------

# Run the Dashboard

Serve the visualization locally:

``` bash
python3 -m http.server 8000 --directory viz
```

Then open:

http://localhost:8000

------------------------------------------------------------------------

# Data Pipeline

## Collector

Runs periodically and gathers latency samples.

Outputs:

data/bakeoff_YYYYMMDD.csv

Data includes:

-   timestamp
-   LAN latency
-   WAN latency
-   jitter
-   traceroute metrics

------------------------------------------------------------------------

## Transform

Aggregates raw telemetry and produces:

viz/latest.csv

This dataset powers the dashboard visualizations.

------------------------------------------------------------------------

## Visualization

The D3 dashboard renders:

-   WAN latency timeline
-   LAN latency comparison
-   instability heatmap
-   experience summary metrics

The UI auto-refreshes to reflect new telemetry.

------------------------------------------------------------------------

# Automation (macOS)

Prime Observer can run continuously using `launchd`.

Example agents:

com.mbeason.net-bakeoff.collector\
com.mbeason.net-bakeoff.transform\
com.mbeason.net-bakeoff.http

Typical schedule:

  Job           Frequency
  ------------- ------------------
  collector     every 30 seconds
  transform     every 60 seconds
  http server   persistent

------------------------------------------------------------------------

# Restart Services

A helper script is included:

bin/restart_net_bakeoff.sh

Restart all services:

``` bash
./bin/restart_net_bakeoff.sh
```

------------------------------------------------------------------------

# Example Use Case

1.  Run Prime Observer on **Fiber** for several days
2.  Switch `phase.txt` to **TMOBILE**
3.  Collect telemetry for another period
4.  Compare:

-   stability
-   latency consistency
-   bad moment frequency

The system reveals which connection **feels better in practice**.

------------------------------------------------------------------------

# Why This Exists

Most network tools measure:

speed

Prime Observer measures:

experience

That distinction matters.

------------------------------------------------------------------------

# Roadmap

Future enhancements:

-   Provider overlay comparison
-   Connection quality score
-   Automatic phase switching
-   Long-term trend analysis
-   Exportable reports
-   Multi-node observation

------------------------------------------------------------------------

# License

MIT License
