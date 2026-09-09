#!/usr/bin/env python3
"""Session registry and derived-artifact cache, shared by every analysis script.

The raw Databento extracts are licensed and live outside the repository, under
`$DATABENTO_RAW_DIR` in the vendor's own layout:

    $DATABENTO_RAW_DIR/<SYMBOL>/<YYYY-MM-DD>.<schema>.dbn.zst

No absolute path is written into this repository. A script that cannot find the
environment variable says so and exits rather than guessing at a location.

Derived artifacts (the LOBSTER message stream, the sequence sidecar, and a
compact book cache) are rebuilt on demand into `$LOB_WORK_DIR`, defaulting to
`data/databento/` which is gitignored. They are reproducible from the raw
extract, so they are cached rather than committed.

WHY THE BOOK CACHE IS DEPTH 3
-----------------------------
The engine can emit any depth, but ten levels of per-message book across fifteen
sessions is roughly 10 GB of CSV, and every consumer here reads it more than
once. Three levels a side is provably enough for everything in this repo:

* Queue position, latency and the Turing-test features need L1 and L2 only.
* The queue-reactive window is K = 3 queues a side at half-tick offsets from
  p_ref, so a modelled queue price is at most 3 ticks from p_ref. With a spread
  of s ticks the best quote sits at queue (s+1)/2, so the deepest modelled queue
  that can hold anything is level 2 (reached when s = 1, where Q1/Q2/Q3 are
  levels 0/1/2). Wider spreads push the best quote further out, which moves
  modelled queues INSIDE the spread where they are genuinely empty.

The cache is int32 for prices and sizes (LOBSTER prices are 1e-4 dollars, so
$4,180.00 is 41,800,000, comfortably inside int32) and float64 for timestamps,
which need full precision to keep nanosecond ordering.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

BOOK_DEPTH = 3
RTH_OPEN, RTH_CLOSE = 34_200, 57_600     # 09:30:00 and 16:00:00 as LOBSTER seconds
TICK = 100                               # a cent, in LOBSTER 1e-4 dollar units

# The sample, stated once so no script invents its own. Three names on selected
# 2024 days: this is not a population sample and nothing drawn from it
# generalises to "large-tick names" or "Nasdaq stocks".
SESSION_DATES: dict[str, tuple[str, ...]] = {
    "MSFT": ("2024-02-01", "2024-04-01", "2024-06-03", "2024-10-01", "2024-12-02"),
    "INTC": ("2024-02-01", "2024-04-01", "2024-08-02", "2024-10-01", "2024-12-02"),
    "AAPL": ("2024-02-01", "2024-04-01", "2024-06-03", "2024-08-01", "2024-10-01"),
}

# INTC 2024-08-02 is the session after Intel's Q2 report and dividend
# suspension. It is a single-name event day, kept because it is genuinely
# different, and flagged wherever it behaves differently from the rest.
EVENT_DAY = "INTC_2024-08-02"

SESSIONS: tuple[str, ...] = tuple(
    f"{sym}_{date}" for sym in SESSION_DATES for date in SESSION_DATES[sym])


def symbol_of(session: str) -> str:
    return session.split("_", 1)[0]


def date_of(session: str) -> str:
    return session.split("_", 1)[1]


def sessions_for(symbol: str) -> tuple[str, ...]:
    return tuple(s for s in SESSIONS if symbol_of(s) == symbol)


def raw_dir() -> Path:
    value = os.environ.get("DATABENTO_RAW_DIR")
    if not value:
        raise SystemExit(
            "DATABENTO_RAW_DIR is not set. It must point at the licensed extract "
            "tree, laid out as <SYMBOL>/<YYYY-MM-DD>.<schema>.dbn.zst. The raw "
            "data is not in this repository and no default path is assumed.")
    path = Path(value)
    if not path.is_dir():
        raise SystemExit(f"DATABENTO_RAW_DIR points at {path}, which is not a directory")
    return path


def work_dir() -> Path:
    path = Path(os.environ.get("LOB_WORK_DIR", "data/databento"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_path(session: str, schema: str = "mbo") -> Path:
    return raw_dir() / symbol_of(session) / f"{date_of(session)}.{schema}.dbn.zst"


@dataclass(frozen=True)
class Derived:
    """Paths to the reproducible per-session artifacts."""
    session: str
    messages: Path
    sequences: Path
    book: Path        # npz cache, not CSV

    def exists(self) -> bool:
        return self.messages.exists() and self.book.exists()


def derived(session: str, root: Path | None = None) -> Derived:
    root = root or work_dir()
    return Derived(
        session=session,
        messages=root / f"{session}_message.csv",
        sequences=root / f"{session}_seq.csv",
        book=root / f"{session}_book{BOOK_DEPTH}.npz",
    )


def _book_csv_to_npz(csv_path: Path, npz_path: Path, chunk: int = 500_000) -> int:
    """Compact the engine's book CSV into an int32 npz, then drop the CSV.

    Reading in chunks keeps peak memory bounded: the widest session here is
    around ten million rows, which would be several GB if the CSV were parsed in
    one go.
    """
    columns = ["timestamp"]
    for level in range(BOOK_DEPTH):
        columns += [f"bid_px_{level}", f"bid_sz_{level}",
                    f"ask_px_{level}", f"ask_sz_{level}"]

    parts: dict[str, list[np.ndarray]] = {c: [] for c in columns}
    for piece in pd.read_csv(csv_path, usecols=columns, chunksize=chunk):
        parts["timestamp"].append(piece["timestamp"].to_numpy(dtype=np.float64))
        for column in columns[1:]:
            # an absent level is an empty field; 0 is not a valid price or size
            # here, so it doubles as the "not present" sentinel
            values = piece[column].to_numpy(dtype=np.float64)
            parts[column].append(np.nan_to_num(values, nan=0.0).astype(np.int32))

    arrays = {c: np.concatenate(parts[c]) if parts[c] else np.empty(0) for c in columns}
    np.savez(npz_path, **arrays)
    return len(arrays["timestamp"])


def load_book(session: str, root: Path | None = None) -> dict[str, np.ndarray]:
    """Book cache as a dict of arrays. 0 in a price or size field means absent."""
    path = derived(session, root).book
    if not path.exists():
        raise SystemExit(f"no book cache for {session}; run scripts/build_sessions.py")
    with np.load(path) as handle:
        return {key: handle[key] for key in handle.files}


def load_messages(session: str, root: Path | None = None) -> pd.DataFrame:
    path = derived(session, root).messages
    if not path.exists():
        raise SystemExit(f"no message file for {session}; run scripts/build_sessions.py")
    return pd.read_csv(
        path, header=None,
        names=["time", "event_type", "order_id", "size", "price", "direction"])


def build(session: str, root: Path | None = None, *, engine: Path | None = None,
          force: bool = False, verbose: bool = True) -> Derived:
    """Convert one session's MBO to LOBSTER and cache its book. Idempotent."""
    root = root or work_dir()
    out = derived(session, root)
    if out.exists() and not force:
        if verbose:
            print(f"  {session}: cached")
        return out

    mbo = raw_path(session, "mbo")
    if not mbo.exists():
        raise SystemExit(f"missing raw extract: {mbo}")

    here = Path(__file__).resolve().parent
    engine = engine or here.parent / "build" / "lob_engine"
    if not engine.exists():
        raise SystemExit(f"engine binary not found at {engine}; build it first")

    if not out.messages.exists() or force:
        if verbose:
            print(f"  {session}: converting MBO -> LOBSTER")
        subprocess.run(
            [sys.executable, str(here / "databento_to_lobster.py"), str(mbo),
             "--out", str(out.messages), "--sequence-out", str(out.sequences)],
            check=True, stdout=subprocess.DEVNULL)

    if not out.book.exists() or force:
        if verbose:
            print(f"  {session}: replaying for a depth-{BOOK_DEPTH} book")
        book_csv = root / f"{session}_book{BOOK_DEPTH}.csv"
        subprocess.run(
            [str(engine), str(out.messages), "--backend", "map",
             "--depth", str(BOOK_DEPTH), "--book-out", str(book_csv)],
            check=True, stdout=subprocess.DEVNULL)
        rows = _book_csv_to_npz(book_csv, out.book)
        book_csv.unlink()
        if verbose:
            print(f"  {session}: cached {rows:,} book rows")
    return out


def rth_mask(times: np.ndarray) -> np.ndarray:
    return (times >= RTH_OPEN) & (times < RTH_CLOSE)
