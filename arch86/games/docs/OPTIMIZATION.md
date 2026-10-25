# Optimization evidence

A 5,000-game synthetic cached GameVault library was loaded on the development
host with a supplied x86-64 profile, SQLite statement tracing and tracemalloc.
No network, private server or actual game was involved.

| Measurement | Before batch/shallow-copy changes | Final representative run |
|---|---:|---:|
| SQLite statements | 10,002 | 6 |
| Peak traced Python allocations | 13.98 MiB | 10.99 MiB |
| Instrumented wall time | 0.451 s | 0.461 s |

Repeated final runs ranged roughly 0.40-0.49 seconds. This is evidence of fewer
queries and lower Python allocations, not a claimed elapsed-time speedup or a
real-VM benchmark. Queries now batch analysis/feedback/install/launch/identity
state; catalog merging copies only fields it modifies. Search is a linear
case-folded pass over loaded records; sorting follows filtering. No additional
framework, background daemon or database service was introduced.

Network metadata pages, library totals, archive output and file/body sizes are
bounded. Covers cache locally and load lazily. Downloads stream in 256 KiB chunks,
update progress at most twice per second, preserve FIFO creation order and use
validators to avoid mixing versions. Prefix/game operations remain explicit.
Archive inspection never recursively extracts nested files. Saving analysis for a
game computes a streaming full local-file SHA-256 to bind evidence; header-only
inspection itself does not need full extraction.

Further timing/performance tuning belongs on the actual 2 GiB and 4 GiB VMs after
acceptance. Graphics probes and solid archive scans retain timeout bounds; use
--offline for immediate cached browsing when a private network is unavailable.
