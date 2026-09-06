#!/usr/bin/env python3
"""Build the derived artifacts for every session, one at a time.

Deliberately sequential. The conversion is a Python loop over several million
MBO records and the replay is a single-threaded C++ pass; running several at
once on a laptop that is also doing other work makes every one of them slower
and the timings useless.

Usage:
    python scripts/build_sessions.py                 # all fifteen
    python scripts/build_sessions.py --session MSFT_2024-06-03
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import sessions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", action="append", choices=sessions.SESSIONS,
                    help="repeatable; default is every session")
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    wanted = tuple(args.session) if args.session else sessions.SESSIONS
    root = args.work_dir or sessions.work_dir()
    print(f"{len(wanted)} session(s) -> {root}")

    started = time.time()
    for index, session in enumerate(wanted, 1):
        mark = time.time()
        print(f"[{index}/{len(wanted)}] {session}")
        sessions.build(session, root, force=args.force)
        print(f"  done in {time.time() - mark:,.0f}s")
    print(f"total {time.time() - started:,.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
