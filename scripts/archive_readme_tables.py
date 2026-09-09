#!/usr/bin/env python3
"""Archive README tables as labelled transcriptions, not independent evidence."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def serialize(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def archive_tables(readme: bytes) -> dict[str, bytes]:
    tables, index, heading = {}, [], ""
    lines = readme.decode().splitlines()
    pos = 0
    while pos < len(lines):
        line = lines[pos]
        if line.startswith("#"):
            heading = line.lstrip("# ")
        if not line.startswith("|"):
            pos += 1
            continue
        table = []
        while pos < len(lines) and lines[pos].startswith("|"):
            table.append(dict(line_number=pos + 1, markdown_row=lines[pos]))
            pos += 1
        name = f"table_{len(index) + 1:02d}.csv"
        tables[name] = serialize(table)
        index.append(dict(path=f"report/readme_tables/{name}", section=heading,
                          source="README.md", provenance="readme_transcription_only",
                          source_sha256=hashlib.sha256(readme).hexdigest()))
    if not index:
        raise ValueError("README contains no tables")
    tables["index.csv"] = serialize(index)
    return tables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    tables = archive_tables((ROOT / "README.md").read_bytes())
    output = ROOT / "report/readme_tables"
    if args.write:
        output.mkdir(parents=True, exist_ok=True)
    for name, content in tables.items():
        path = output / name
        if args.write:
            path.write_bytes(content)
        elif not path.exists() or path.read_bytes() != content:
            raise ValueError(f"README transcription differs: {name}")
    print(f"Checked {len(tables)-1} README table transcriptions; no independent measurement implied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
