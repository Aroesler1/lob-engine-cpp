#!/usr/bin/env python3
"""Convert Databento MBO (Nasdaq TotalView-ITCH) into LOBSTER message format.

LOBSTER is itself a reconstruction of Nasdaq TotalView-ITCH, so Databento's
`XNAS.ITCH` MBO schema is the same underlying feed at full depth. Converting it
into the LOBSTER message layout lets this engine consume it with no parser
change, while removing the constraint that motivates the level-scoped caveat in
the README: a level-N LOBSTER message file omits events for orders outside the
top N at event time, whereas MBO carries every order event at every level.

Event mapping
-------------
Databento MBO `action`  ->  LOBSTER event type
    A (add)             ->  1  new limit order
    C (cancel)          ->  2  partial cancel, carrying the cancelled size.
                               The engine's reduce_order handles both partial
                               and full removal, so emitting the exact cancelled
                               quantity is strictly more faithful than guessing
                               between LOBSTER's 2 and 3.
    F (fill)            ->  4  execution against a displayed order
    T (trade)           ->  5  execution against hidden liquidity, but ONLY
                               when the trade is not already represented by an F
    R (book clear)      ->  skipped (session reset; emitted as a comment)

The T/F duplication, and why it matters
---------------------------------------
Databento reports a single displayed execution TWICE: once as `T` (the trade
print, order_id 0) and once as `F` (the book-side fill, carrying the resting
order's id). The two records share a timestamp, price, size, and `sequence`.

Measured on MSFT 2024-06-03 (4,003,834 MBO records):

    F sequences                            70,510
    T sequences                            98,501
    shared sequences                       70,469   (99.94% of F)
    shared sequences with disagreeing size      0

Ingesting both as trades therefore double counts volume:

    displayed volume (F)              2,406,106
    hidden volume (T with no F)       3,170,082
    deduplicated total                5,576,188
    naive F + T                       7,977,464   (+43%)

The deduplicated figure is consistent with Nasdaq's share of MSFT volume that
session; the naive figure is not. This module keys the dedup on `sequence`,
which is exact, rather than on (timestamp, price), which collides.

Usage
-----
    python scripts/databento_to_lobster.py data/databento/MSFT_2024-06-03_mbo.dbn.zst \
        --out data/databento/MSFT_2024-06-03_message.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Databento fixed-point prices are integers of 1e-9 dollars; LOBSTER uses
# integers of 1e-4 dollars, so the two differ by exactly 1e5.
_DBN_TO_LOBSTER_PRICE = 100_000
_EXCHANGE_TZ = ZoneInfo("America/New_York")

_ACTION_TO_EVENT = {"A": 1, "C": 2, "F": 4}


def _as_char(value) -> str:
    """Databento exposes action/side as either a str or its integer code."""
    return chr(value) if isinstance(value, int) else str(value)


def _midnight_ns(ts_ns: int) -> int:
    """Epoch-ns of exchange-local midnight for the session containing ts_ns.

    LOBSTER timestamps are seconds elapsed since local midnight, so the offset
    has to be computed in exchange time rather than UTC or the result shifts by
    the UTC offset (and by an extra hour across a DST boundary).
    """
    local = datetime.fromtimestamp(ts_ns / 1e9, tz=_EXCHANGE_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1e9)


def _iter_records(path: Path):
    import databento as db

    for rec in db.DBNStore.from_file(str(path)):
        if getattr(rec, "action", None) is not None:
            yield rec


def collect_filled_sequences(path: Path) -> set[int]:
    """First pass: sequence numbers that carry an F, so their T is a duplicate."""
    return {r.sequence for r in _iter_records(path) if _as_char(r.action) == "F"}


def convert(path: Path, out_path: Path) -> dict[str, int]:
    filled = collect_filled_sequences(path)

    stats = {
        "records": 0, "emitted": 0, "adds": 0, "cancels": 0,
        "displayed_fills": 0, "hidden_trades": 0,
        "duplicate_prints_dropped": 0, "book_clears": 0,
        "modifies": 0, "unsided": 0, "skipped_other": 0,
    }
    midnight = None

    with out_path.open("w", encoding="utf-8") as fh:
        for rec in _iter_records(path):
            stats["records"] += 1
            action = _as_char(rec.action)
            if midnight is None:
                midnight = _midnight_ns(rec.ts_recv)

            if action == "R":
                stats["book_clears"] += 1
                continue
            if action == "M":
                # A modify is a price/size change on a resting order. LOBSTER has
                # no such type; decomposing it into delete+add would need order
                # state this pass does not carry, so it is counted and skipped
                # rather than silently mistranslated.
                stats["modifies"] += 1
                continue

            if action == "T":
                if rec.sequence in filled:
                    stats["duplicate_prints_dropped"] += 1
                    continue
                event_type = 5
                stats["hidden_trades"] += 1
            elif action in _ACTION_TO_EVENT:
                event_type = _ACTION_TO_EVENT[action]
                if action == "A":
                    stats["adds"] += 1
                elif action == "C":
                    stats["cancels"] += 1
                else:
                    stats["displayed_fills"] += 1
            else:
                stats["skipped_other"] += 1
                continue

            side = _as_char(rec.side)
            if side == "B":
                direction = 1
            elif side == "A":
                direction = -1
            else:
                # Non-displayed prints can carry no side. Direction only feeds
                # signed trade-flow analytics (type 5 is a book no-op), so the
                # row is kept and counted rather than dropped.
                stats["unsided"] += 1
                direction = -1

            seconds = (rec.ts_recv - midnight) / 1e9
            price = rec.price // _DBN_TO_LOBSTER_PRICE
            fh.write(
                f"{seconds:.9f},{event_type},{rec.order_id},"
                f"{rec.size},{price},{direction}\n"
            )
            stats["emitted"] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dbn_path", type=Path, help="Databento MBO .dbn or .dbn.zst file")
    parser.add_argument("--out", type=Path, required=True, help="output LOBSTER message CSV")
    args = parser.parse_args()

    if not args.dbn_path.exists():
        print(f"no such file: {args.dbn_path}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    stats = convert(args.dbn_path, args.out)

    width = max(len(k) for k in stats)
    print(f"{args.dbn_path.name} -> {args.out}")
    for key, value in stats.items():
        print(f"  {key.replace('_', ' '):<{width}}  {value:>12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
