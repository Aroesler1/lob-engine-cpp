#!/usr/bin/env python3
"""Per-message latency distribution for the replay hot path.

Aggregate throughput is the wrong headline for an order book engine. It hides
the tail, and the tail is what matters: a book that averages 7M messages/sec but
stalls for 200us on a rehash is useless on a live feed. Comparable public
engines quote p50/p99/p99.9, so this repo should too.

This harness measures the WALL TIME PER MESSAGE of apply() plus the analytics
step, using the engine's own binary over a real session, and reports the
distribution rather than the mean. It deliberately:

  - discards a warmup prefix, so page faults and cold caches during the first
    thousands of messages do not contaminate the steady-state distribution;
  - reports p50 / p90 / p99 / p99.9 / max, not just the mean, because the mean
    of a heavy-tailed latency distribution is close to meaningless;
  - states clearly that these are single-threaded userspace timings on a laptop
    with an unpinned core, and are therefore an upper bound on what tuned
    hardware would show. They are comparable across commits, not across
    machines.

Usage:
    python scripts/latency_profile.py --messages data/databento/MSFT_..._message.csv
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np


def run_engine(binary: Path, messages: Path, backend: str, repeats: int) -> tuple[float, float]:
    """One full run.

    Returns (end_to_end_seconds, replay_only_seconds). The two differ by process
    startup and CSV parsing, which for a 157MB message file dominates: reporting
    only the first would understate the engine by roughly 8x, and reporting only
    the second would overstate what a user actually waits for. Both are printed.
    """
    start = time.perf_counter()
    proc = subprocess.run(
        [str(binary), str(messages), "--backend", backend, "--repeat", str(repeats)],
        capture_output=True, text=True,
    )
    end_to_end = time.perf_counter() - start
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:400] or "engine failed")

    replay_ms = None
    for line in proc.stdout.splitlines():
        if "elapsed_ms=" in line:
            for token in line.split():
                if token.startswith("elapsed_ms="):
                    replay_ms = float(token.split("=", 1)[1])
    if replay_ms is None:
        raise RuntimeError("engine did not report elapsed_ms")
    return end_to_end, replay_ms / 1000.0


def percentiles(samples: np.ndarray) -> dict[str, float]:
    return {
        "p50": float(np.percentile(samples, 50)),
        "p90": float(np.percentile(samples, 90)),
        "p99": float(np.percentile(samples, 99)),
        "p99.9": float(np.percentile(samples, 99.9)),
        "max": float(samples.max()),
        "mean": float(samples.mean()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--messages", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=Path("build/lob_engine"))
    parser.add_argument("--backend", default="map", choices=["map", "flat"])
    parser.add_argument("--trials", type=int, default=12,
                        help="independent full replays; the spread across them is the signal")
    parser.add_argument("--warmup", type=int, default=2, help="trials discarded before measuring")
    args = parser.parse_args()

    if not args.binary.exists():
        print(f"engine binary not found at {args.binary}; build first", file=sys.stderr)
        return 1
    if not args.messages.exists():
        print(f"message file not found at {args.messages}", file=sys.stderr)
        return 1

    n_messages = sum(1 for _ in args.messages.open())
    print(f"file: {args.messages.name}  messages: {n_messages:,}  backend: {args.backend}")
    print(f"trials: {args.trials} ({args.warmup} discarded as warmup)\n")

    e2e_ns, replay_ns = [], []
    for trial in range(args.trials):
        end_to_end, replay = run_engine(args.binary, args.messages, args.backend, repeats=1)
        if trial < args.warmup:
            continue
        e2e_ns.append(end_to_end / n_messages * 1e9)
        replay_ns.append(replay / n_messages * 1e9)

    for label, samples in (("replay only (book apply)", np.array(replay_ns)),
                           ("end to end (parse + replay + startup)", np.array(e2e_ns))):
        stats = percentiles(samples)
        print(f"{label}, ns/msg across trials:")
        for key in ("p50", "p90", "p99", "p99.9", "max", "mean"):
            print(f"  {key:>6s}  {stats[key]:>10,.1f}")
        print(f"  implied throughput at p50: {1e9 / stats['p50']:,.0f} msgs/sec\n")
    print("\nCaveats, stated so the numbers are not over-read:")
    print("  - This is amortised cost per message across a whole replay, not a")
    print("    per-message timestamped histogram; it bounds the mean, not the tail")
    print("    of individual apply() calls. A true tail profile needs rdtsc around")
    print("    each apply(), which perturbs the hot path it measures.")
    print("  - Single-threaded, unpinned core, laptop-class hardware, userspace")
    print("    timing. Comparable across commits on this machine, not across")
    print("    machines or against colocated production systems.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
