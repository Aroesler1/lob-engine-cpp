#!/usr/bin/env python3
"""Pull one symbol-day of Databento XNAS.ITCH into data/databento/.

Costs money against a metered entitlement, so the default is a DRY RUN: it
prints `metadata.get_cost` for every requested schema and downloads nothing.
Pass --confirm to actually fetch.

The window is the full Nasdaq extended session, 04:00-20:00 exchange time,
which is what the MSFT 2024-06-03 extract covers (its LOBSTER seconds run
14400.013 to 71999.959, i.e. 04:00:00 to 20:00:00 ET). Expressing it in
exchange time rather than UTC keeps it correct across a DST boundary.

Usage:
    python scripts/fetch_databento_session.py --symbol INTC --date 2024-08-02
    python scripts/fetch_databento_session.py --symbol INTC --date 2024-08-02 --confirm
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

_EXCHANGE_TZ = ZoneInfo("America/New_York")
_DATASET = "XNAS.ITCH"
_SESSION_OPEN = time(4, 0)
_SESSION_CLOSE = time(20, 0)

# filename stem -> databento schema id
_SCHEMA_SUFFIX = {"mbo": "mbo", "mbp-10": "mbp10"}


def session_window(date_str: str) -> tuple[datetime, datetime]:
    """UTC-aware bounds of the extended session on `date_str` in exchange time."""
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    start = datetime.combine(day, _SESSION_OPEN, tzinfo=_EXCHANGE_TZ)
    end = datetime.combine(day, _SESSION_CLOSE, tzinfo=_EXCHANGE_TZ)
    return start, end


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD, exchange-local")
    ap.add_argument("--schemas", nargs="+", default=["mbo", "mbp-10"],
                    choices=sorted(_SCHEMA_SUFFIX))
    ap.add_argument("--out-dir", type=Path, default=Path("data/databento"))
    ap.add_argument("--confirm", action="store_true",
                    help="actually download; without it this only prices the request")
    args = ap.parse_args()

    if not os.environ.get("DATABENTO_API_KEY"):
        print("DATABENTO_API_KEY is not set", file=sys.stderr)
        return 1

    import databento as db

    client = db.Historical()
    start, end = session_window(args.date)

    print(f"{args.symbol} {args.date}  {_DATASET}  "
          f"{start:%H:%M}-{end:%H:%M} {start.tzname()}\n")

    total = 0.0
    plan = []
    for schema in args.schemas:
        cost = client.metadata.get_cost(
            dataset=_DATASET, symbols=[args.symbol], schema=schema,
            start=start, end=end, stype_in="raw_symbol", mode="historical-streaming",
        )
        total += cost
        out = args.out_dir / f"{args.symbol}_{args.date}_{_SCHEMA_SUFFIX[schema]}.dbn.zst"
        plan.append((schema, out))
        print(f"  {schema:<7} ${cost:>9.4f}  -> {out}")
    print(f"  {'total':<7} ${total:>9.4f}")

    if not args.confirm:
        print("\ndry run; nothing downloaded. re-run with --confirm to fetch.")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print()
    for schema, out in plan:
        if out.exists():
            print(f"  {schema:<7} already present, skipping ({out})")
            continue
        client.timeseries.get_range(
            dataset=_DATASET, symbols=[args.symbol], schema=schema,
            start=start, end=end, stype_in="raw_symbol", path=str(out),
        )
        print(f"  {schema:<7} {out.stat().st_size / 1e6:>9.1f} MB  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
