# Prime Observer

**Network Observability & Experience Engine**

Prime Observer is a lightweight, local-first network telemetry system designed to measure *real user experience* across internet providers — not just speed tests.

It was built to answer a simple question:

> "Which connection actually feels better to use?"

Instead of focusing on raw bandwidth, Prime Observer measures latency behavior, instability, and real-world “bad moments” over time.

---

## Purpose

Prime Observer compares upstream providers (e.g., Fiber vs 5G) by observing:

- WAN latency behavior (p95)
- Jitter and instability
- Frequency of “bad moments”
- LAN vs WAN correlation
- Real experience over time (not burst tests)

The goal is **decision clarity**, not benchmarking vanity metrics.

---

## Key Concepts

**Bad Moment**  
A time bucket where latency crosses calibrated thresholds that would impact real usage (video calls, streaming, interactive apps).

**Experience over Speed**  
Fast connections can feel worse if unstable. Prime Observer measures *consistency*.

**Local First**  
Runs entirely on your machine. No cloud. No external dependency required.

---

## Components

| Component | Purpose |
|----------|---------|
| `collector` | Captures latency telemetry (LAN + WAN targets) |
| `transform` | Aggregates, buckets, and computes metrics |
| `viz` | Local web UI displaying experience analytics |
| `phase.txt` | Indicates current upstream provider |
| `LaunchAgents` | Automates continuous collection |

---

## Metrics Observed

- WAN p95 latency (median & 95th)
- Jitter (95th)
- Bad moment rate
- Bad minutes per hour
- LAN ↔ WAN correlation
- ISP comparison summary
- Heatmap of instability over time

---

## Requirements

- macOS / Linux
- Python 3.x
- `launchctl` (macOS automation)
- Basic shell environment

No database required. Uses flat CSV telemetry.

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/matthewbeason/prime-observer.git
cd prime-observer
