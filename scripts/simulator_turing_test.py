#!/usr/bin/env python3
"""Can a classifier tell simulated order flow from the real thing?

Every distributional comparison in this repo scores one statistic at a time, so
a simulator can look good on each margin and still be obviously fake in the
joint distribution. This is the discriminative version of the same question:
cut the real stream and each simulated stream into fixed windows, describe each
window with the same features, and train a classifier to separate them.

AUC is the readout. 0.5 means the two are indistinguishable to this feature set
and this classifier. 1.0 means a model with no access to the raw stream can pick
the fake out every time.

THE CLAIM UNDER TEST
--------------------
Adding the Hawkes term should move AUC DOWN, and the separation that survives
should no longer be carried by inter-arrival timing. If AUC falls but timing is
still the discriminating family, the Hawkes term made the marginal look right
without fixing the mechanism.

The split is chronological: the first 70% of each session trains and the last
30% tests, never a random split. Windows adjacent in time share book state, so
a random split leaks a window's neighbours into training and inflates AUC
toward 1 regardless of how good the simulator is.

Usage:
    python scripts/simulator_turing_test.py
    python scripts/simulator_turing_test.py --session INTC_2024-04-01
"""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import sessions
from queue_reactive import QUEUES, _sim_spread, average_event_sizes, normalise

WINDOW = 200
TRAIN_FRACTION = 0.7
KIND_NAMES = ("add", "cancel", "exec")
EVENT_TO_KIND = {1: 0, 2: 1, 4: 2}

# feature families, used to attribute the separation to a mechanism rather than
# to an individual column
FAMILY_OF = {
    "interarrival": "timing",
    "kind_share": "event mix",
    "transition": "event mix",
    "queue": "book shape",
    "spread": "book shape",
    "ofi": "order flow",
    "trades": "order flow",
}


def family(name: str) -> str:
    for prefix, group in FAMILY_OF.items():
        if name.startswith(prefix):
            return group
    raise KeyError(f"no feature family for {name!r}")


def canonical_real(records, aes: dict[int, float]) -> pd.DataFrame:
    """Real events in the same shape as a simulated path, queues in AES units."""
    events = records.events
    kind = events["event_type"].map(EVENT_TO_KIND)
    frame = pd.DataFrame({"time": events["time"].to_numpy(),
                          "kind": kind.to_numpy()})
    for q in QUEUES:
        frame[f"q{q}"] = normalise(events[f"q{q}"].to_numpy(), aes[q])
    return frame.dropna().reset_index(drop=True)


def canonical_sim(path: pd.DataFrame) -> pd.DataFrame:
    """Simulated path, with reference-price moves dropped.

    A price move is not an order event and has no counterpart in the real event
    stream, so leaving it in would hand the classifier a free tell that says
    nothing about order flow.
    """
    keep = path[path["kind"] != 3].reset_index(drop=True)
    columns = ["time", "kind"] + [f"q{q}" for q in QUEUES]
    return keep[columns].reset_index(drop=True)


def window_features(block: pd.DataFrame) -> dict[str, float]:
    time = block["time"].to_numpy()
    kind = block["kind"].to_numpy().astype(int)
    gaps = np.diff(time)
    gaps = gaps[gaps > 0]
    out: dict[str, float] = {}

    # timing: log gaps, because the distribution spans microseconds to seconds
    for q in (10, 25, 50, 75, 90):
        value = np.percentile(gaps, q) if gaps.size else np.nan
        out[f"interarrival_q{q}"] = float(np.log10(value)) if value and value > 0 else np.nan

    # event mix
    for index, name in enumerate(KIND_NAMES):
        out[f"kind_share_{name}"] = float(np.mean(kind == index))
    for a, first in enumerate(KIND_NAMES):
        for b, second in enumerate(KIND_NAMES):
            pair = np.sum((kind[:-1] == a) & (kind[1:] == b))
            out[f"transition_{first}_{second}"] = float(pair) / max(kind.size - 1, 1)

    # book shape
    l1 = np.r_[block["q1"].to_numpy(), block["q-1"].to_numpy()]
    l2 = np.r_[block["q2"].to_numpy(), block["q-2"].to_numpy()]
    for q in (25, 50, 75):
        out[f"queue_l1_q{q}"] = float(np.percentile(l1, q))
        out[f"queue_l2_q{q}"] = float(np.percentile(l2, q))
    spread = _sim_spread(block)
    for q in (25, 50, 75):
        out[f"spread_q{q}"] = float(np.percentile(spread, q))

    # order flow: best-level OFI over the window, in the Cont-Kukanov-Stoikov
    # transition form applied to the two touch queues
    ask = block["q1"].to_numpy()
    bid = block["q-1"].to_numpy()
    ofi = np.diff(bid) - np.diff(ask)
    out["ofi_sum"] = float(np.sum(ofi))
    out["ofi_abs"] = float(np.sum(np.abs(ofi)))
    out["trades_count"] = float(np.sum(kind == 2))
    return out


def windows(frame: pd.DataFrame, label: int) -> pd.DataFrame:
    n = len(frame) // WINDOW
    if n < 4:
        return pd.DataFrame()
    rows = []
    for w in range(n):
        block = frame.iloc[w * WINDOW:(w + 1) * WINDOW]
        features = window_features(block)
        features["label"] = label
        features["t0"] = float(block["time"].iloc[0])
        rows.append(features)
    return pd.DataFrame(rows)


def chronological_split(table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Train on the first 70% of each class BY TIME, test on the last 30%."""
    train = np.zeros(len(table), dtype=bool)
    for label, group in table.groupby("label"):
        cut = group["t0"].quantile(TRAIN_FRACTION)
        train[group.index[group["t0"] <= cut]] = True
    return train, ~train


def family_importance(model, x_test: pd.DataFrame, y_test, base: float, *,
                      seed: int = 0, repeats: int = 10) -> pd.DataFrame:
    """AUC lost when a whole feature family is permuted together.

    Single-feature permutation importance is useless here. The families are
    internally redundant (five inter-arrival quantiles all say much the same
    thing), so permuting one column leaves the others to carry it and every
    feature scores about zero even when the classifier is perfect. Permuting the
    family as a block is what answers "which mechanism gives the simulator away".
    """
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    rows = []
    for group in sorted({family(c) for c in x_test.columns}):
        columns = [c for c in x_test.columns if family(c) == group]
        drops = []
        for _ in range(repeats):
            shuffled = x_test.copy()
            order = rng.permutation(len(shuffled))
            shuffled[columns] = shuffled[columns].to_numpy()[order]
            drops.append(base - roc_auc_score(
                y_test, model.predict_proba(shuffled)[:, 1]))
        rows.append({"family": group, "importance": float(np.mean(drops))})
    return pd.DataFrame(rows).sort_values("importance", ascending=False).reset_index(drop=True)


def auc_on(real: pd.DataFrame, fake: pd.DataFrame, columns: list[str], *,
           seed: int = 0) -> float:
    """AUC from a restricted feature set.

    Full-feature AUC saturates at 1.0 against both simulators, which makes
    "did it get better" unanswerable from that number alone. Refitting on one
    family at a time is what separates "the timing tell was fixed" from "the
    classifier found something else instead".
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    table = pd.concat([real, fake], ignore_index=True)
    if table.empty or not columns:
        return float("nan")
    train, test = chronological_split(table)
    y_train, y_test = table.loc[train, "label"], table.loc[test, "label"]
    if y_train.nunique() < 2 or y_test.nunique() < 2:
        return float("nan")
    model = HistGradientBoostingClassifier(random_state=seed, max_iter=200)
    model.fit(table.loc[train, columns], y_train)
    return float(roc_auc_score(y_test, model.predict_proba(table.loc[test, columns])[:, 1]))


def discriminate(real: pd.DataFrame, fake: pd.DataFrame, *, seed: int = 0
                 ) -> tuple[float, pd.DataFrame]:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    table = pd.concat([real, fake], ignore_index=True)
    if table.empty or table["label"].nunique() < 2:
        return float("nan"), pd.DataFrame()
    train, test = chronological_split(table)
    columns = [c for c in table.columns if c not in ("label", "t0")]
    x_train, y_train = table.loc[train, columns], table.loc[train, "label"]
    x_test, y_test = table.loc[test, columns], table.loc[test, "label"]
    if y_train.nunique() < 2 or y_test.nunique() < 2:
        return float("nan"), pd.DataFrame()

    model = HistGradientBoostingClassifier(random_state=seed, max_iter=200)
    model.fit(x_train, y_train)
    score = float(roc_auc_score(y_test, model.predict_proba(x_test)[:, 1]))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        detail = family_importance(model, x_test, y_test, score, seed=seed)
    return score, detail


def by_family(detail: pd.DataFrame) -> pd.DataFrame:
    return detail


def run(session: str, root: Path | None = None, *, seed: int = 0) -> dict:
    from hawkes import build_records_from_cache

    cache = root or sessions.work_dir()
    records = build_records_from_cache(session, root)
    aes = average_event_sizes(records.events)
    real = windows(canonical_real(records, aes), label=0)

    out: dict = {"session": session, "real_windows": len(real)}
    for name in ("base", "hawkes"):
        path_file = cache / f"path_{name}_{session}.parquet"
        if not path_file.exists():
            out[f"auc_{name}"] = float("nan")
            continue
        path = pd.read_parquet(path_file)
        fake = windows(canonical_sim(path), label=1)
        score, detail = discriminate(real, fake, seed=seed)
        out[f"auc_{name}"] = score
        out[f"windows_{name}"] = len(fake)
        columns = [c for c in real.columns if c not in ("label", "t0")]
        timing = [c for c in columns if family(c) == "timing"]
        out[f"auc_timing_{name}"] = auc_on(real, fake, timing, seed=seed)
        out[f"auc_no_timing_{name}"] = auc_on(
            real, fake, [c for c in columns if family(c) != "timing"], seed=seed)
        families = by_family(detail)
        out[f"families_{name}"] = families
        if not families.empty:
            out[f"top_family_{name}"] = families.iloc[0]["family"]
    return out


def plot(summary: pd.DataFrame, families: pd.DataFrame, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
    order = list(summary["session"])
    y = np.arange(len(order))

    # left: AUC on the full feature set and on timing alone
    axes[0].scatter(summary["auc_base"], y, marker="o", s=34, label="base",
                    color="#8A9399")
    axes[0].scatter(summary["auc_hawkes"], y, marker="D", s=30, label="Hawkes",
                    color="#0E5B63")
    axes[0].scatter(summary["auc_timing_hawkes"], y, marker="x", s=34,
                    label="Hawkes, timing features only", color="#8A5411")
    axes[0].axvline(0.5, color="0.5", linewidth=0.8, linestyle="--")
    axes[0].set_yticks(y, order, fontsize=7)
    axes[0].set_xlim(0.45, 1.02)
    axes[0].set_xlabel("AUC (0.5 = indistinguishable)")
    axes[0].set_title("Can a classifier tell the simulator from the market?")
    axes[0].legend(fontsize=7, loc="lower left")
    axes[0].grid(alpha=0.25, linewidth=0.5, axis="x")

    # right: where the separation comes from, base against Hawkes
    pivot = families.pivot_table(index=["session", "model"], columns="family",
                                 values="importance").reset_index()
    groups = [c for c in pivot.columns if c not in ("session", "model")]
    width = 0.38
    for offset, model, hatch in ((-width / 2, "base", ""), (width / 2, "hawkes", "//")):
        block = pivot[pivot.model == model].set_index("session").reindex(order)
        bottom = np.zeros(len(order))
        for colour, group in zip(("#0E5B63", "#8A5411", "#566166", "#98A2A6"), groups):
            values = block[group].fillna(0).clip(lower=0).to_numpy()
            axes[1].barh(y + offset, values, height=width, left=bottom, color=colour,
                         hatch=hatch, edgecolor="white", linewidth=0.3,
                         label=group if model == "base" else None)
            bottom += values
    axes[1].set_yticks(y, ["" for _ in order])
    axes[1].set_xlabel("AUC lost when the family is permuted (solid = base, hatched = Hawkes)")
    axes[1].set_title("Which feature family gives the simulator away")
    axes[1].legend(fontsize=7, loc="lower right")
    axes[1].grid(alpha=0.25, linewidth=0.5, axis="x")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="append", choices=sessions.SESSIONS)
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=Path("report/turing"))
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    wanted = tuple(args.session) if args.session else sessions.SESSIONS
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows, families = [], []
    for session in wanted:
        result = run(session, args.work_dir, seed=args.seed)
        base, hawkes = result.get("auc_base"), result.get("auc_hawkes")
        if np.isfinite(base) and np.isfinite(hawkes):
            print(f"{session}: AUC base {base:.3f} -> hawkes {hawkes:.3f}   "
                  f"[timing only {result['auc_timing_base']:.3f} -> "
                  f"{result['auc_timing_hawkes']:.3f}; "
                  f"without timing {result['auc_no_timing_base']:.3f} -> "
                  f"{result['auc_no_timing_hawkes']:.3f}]")
        else:
            print(f"{session}: incomplete")
        for name in ("base", "hawkes"):
            detail = result.get(f"families_{name}")
            if isinstance(detail, pd.DataFrame) and not detail.empty:
                print(f"    {name:<6} separation carried by: "
                      + ", ".join(f"{r.family} {r.importance:.3f}"
                                  for r in detail.itertuples()))
                families.append(detail.assign(session=session, model=name))
        rows.append({k: v for k, v in result.items()
                     if not isinstance(v, pd.DataFrame)})

    summary = pd.DataFrame(rows)
    summary.to_csv(args.out_dir / "auc.csv", index=False)
    if families:
        table = pd.concat(families)
        table.to_csv(args.out_dir / "families.csv", index=False)
        plot(summary, table, args.out_dir / "turing.png")
    print()
    print(summary.to_string(index=False, float_format=lambda v: f"{v:0.3f}"))
    print(f"\nsaved -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
