#!/usr/bin/env python3
"""Check published aggregate integrity and reproduce headline arithmetic offline.

No vendor SDK, raw data, fitting, network access or third-party Python package
is needed. A matching hash is an integrity check, not raw-data reconstruction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = {
    *(f"MSFT_2024-{date}" for date in ("02-01", "04-01", "06-03", "10-01", "12-02")),
    *(f"INTC_2024-{date}" for date in ("02-01", "04-01", "08-02", "10-01", "12-02")),
    *(f"AAPL_2024-{date}" for date in ("02-01", "04-01", "06-03", "08-01", "10-01")),
}


def rows(root: Path, path: str) -> list[dict[str, str]]:
    with (root / path).open(newline="") as stream:
        return list(csv.DictReader(stream))


def verify_manifest(root: Path) -> int:
    manifest = rows(root, "report/evidence_manifest.csv")
    paths = [row["path"] for row in manifest]
    if len(paths) != 80 or len(set(paths)) != len(paths):
        raise ValueError("expected eighty unique audited aggregate paths")
    for item in manifest:
        path = Path(item["path"])
        if path.is_absolute() or ".." in path.parts or path.parts[0] != "report":
            raise ValueError("manifest path must remain inside report/")
        content = (root / path).read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"aggregate hash differs: {path}")
        if len(rows(root, str(path))) != int(item["rows"]):
            raise ValueError(f"aggregate row count differs: {path}")
    return len(paths)


def per_session(root: Path, path: str) -> dict[str, dict[str, str]]:
    data = rows(root, path)
    keyed = {row["session"]: row for row in data}
    if len(keyed) != len(data) or set(keyed) != SESSIONS:
        raise ValueError(f"expected exactly the fifteen frozen sessions: {path}")
    return keyed


def number(row: dict[str, str], column: str) -> float:
    value = float(row[column])
    if not math.isfinite(value):
        raise ValueError(f"nonfinite headline input: {column}")
    return value


def comparison(root: Path, path: str, statistic: str) -> float:
    matched = [row for row in rows(root, path) if row["statistic"] == statistic]
    if len(matched) != 1:
        raise ValueError(f"expected one {statistic} row in {path}")
    row = matched[0]
    real = number(row, "real")
    if real <= 0:
        raise ValueError("comparison denominator must be positive")
    return number(row, "simulated") / real


def compute_claims(root: Path) -> list[dict]:
    queue = per_session(root, "report/queue_position/summary.csv")
    # Check independent front/back columns before trusting the reported delta.
    for row in queue.values():
        expected = number(row, "front_edge_ticks") - number(row, "back_edge_ticks")
        if not math.isclose(expected, number(row, "front_minus_back_ticks"), abs_tol=1e-12):
            raise ValueError("queue edge arithmetic differs")
    edge = [number(row, "front_minus_back_ticks") for row in queue.values()]

    latency = rows(root, "report/latency/latency_curve.csv")
    keys = {(row["session"], number(row, "latency_s")) for row in latency}
    delays = {0.0, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1}
    if len(keys) != len(latency) or keys != {(s, d) for s in SESSIONS for d in delays}:
        raise ValueError("latency grid is not fifteen sessions by six delays")
    losses = sum(all(number(row, "net_ticks") < 0 for row in latency
                     if row["session"] == session) for session in SESSIONS)
    gap_matches, worse_queues = 0, 0
    for session in sorted(SESSIONS):
        base = f"report/hawkes/compare_base_{session}.csv"
        hawkes = f"report/hawkes/compare_{session}.csv"
        gap = comparison(root, hawkes, "median inter-arrival (ms)")
        gap_matches += 0.5 <= gap <= 2.0
        before = comparison(root, base, "mean queue at Q1 (AES)")
        after = comparison(root, hawkes, "mean queue at Q1 (AES)")
        worse_queues += abs(math.log(after)) > abs(math.log(before))

    bench = [row for row in rows(root, "report/hawkes/lob_bench.csv")
             if row["statistic"] == "log_inter_arrival_time"]
    if len(bench) != 15 or {row["session"] for row in bench} != SESSIONS:
        raise ValueError("LOB-Bench timing sample differs")
    auc = per_session(root, "report/turing/auc.csv")
    ofi = per_session(root, "report/ofi_sweep.csv")
    metrics = [
        ("queue_absolute_front_back_below_0.05_tick", sum(abs(v) < .05 for v in edge), "sessions"),
        ("queue_front_edge_greater_than_back", sum(v > 0 for v in edge), "sessions"),
        ("latency_negative_net_at_every_delay", losses, "sessions"),
        ("hawkes_median_gap_within_factor_two", gap_matches, "sessions"),
        ("hawkes_queue_ratio_further_from_one", worse_queues, "sessions"),
        ("hawkes_lob_bench_timing_improves", sum(number(r, "hawkes_l1") < number(r, "base_l1") for r in bench), "sessions"),
        ("lob_bench_timing_mean_base_l1", statistics.mean(number(r, "base_l1") for r in bench), "L1 distance"),
        ("lob_bench_timing_mean_hawkes_l1", statistics.mean(number(r, "hawkes_l1") for r in bench), "L1 distance"),
        ("hawkes_classifier_minimum_auc", min(number(r, "auc_hawkes") for r in auc.values()), "AUC"),
        ("ofi_h10_pca_contemporaneous_beats_l1", sum(number(r, "contemp_h10_pca") > number(r, "contemp_h10_l1") for r in ofi.values()), "sessions"),
        ("ofi_h100_l1_predictive_beats_pca", sum(number(r, "pred_h100_l1") > number(r, "pred_h100_pca") for r in ofi.values()), "sessions"),
    ]
    return [dict(metric=name, value=value, n_sessions=15, units=unit,
                 scope="three_names_fifteen_symbol_days_2024_descriptive")
            for name, value, unit in metrics]


def claim_csv(claims: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(claims[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(claims)
    return buffer.getvalue().encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify published evidence (default)")
    mode.add_argument("--write", action="store_true", help="write derived headline arithmetic")
    args = parser.parse_args()
    count = verify_manifest(ROOT)
    claims = compute_claims(ROOT)
    expected = claim_csv(claims)
    path = ROOT / "report/evidence/claims.csv"
    if args.write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(expected)
    elif not path.exists() or path.read_bytes() != expected:
        raise ValueError("published headline arithmetic differs")
    print(f"Verified {count} aggregate files and {len(claims)} claims, offline.")
    for row in claims:
        print(f"{row['metric']}: {row['value']} ({row['units']}; n=15)")
    print("Hashes verify integrity, not raw reconstruction or causal conclusions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
