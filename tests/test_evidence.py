"""Offline evidence, missing-session and corruption regression checks."""
import csv
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_evidence import claim_csv, compute_claims, per_session, verify_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_published_headlines_recompute_from_aggregates():
    assert verify_manifest(ROOT) == 80
    content = claim_csv(compute_claims(ROOT))
    assert (ROOT / "report/evidence/claims.csv").read_bytes() == content


def test_queue_threshold_counts_both_directions_and_all_exceptions():
    claims = {row["metric"]: row["value"] for row in compute_claims(ROOT)}
    assert claims["queue_absolute_front_back_below_0.05_tick"] == 12
    assert claims["queue_front_edge_greater_than_back"] == 8


def test_modified_aggregate_fails_integrity_check(tmp_path):
    shutil.copytree(ROOT / "report", tmp_path / "report")
    path = tmp_path / "report/queue_position/summary.csv"
    path.write_bytes(path.read_bytes().replace(b"0.385790", b"0.005790"))
    with pytest.raises(ValueError, match="hash differs"):
        verify_manifest(tmp_path)


@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_or_duplicate_session_fails(tmp_path, duplicate):
    with (ROOT / "report/queue_position/summary.csv").open(newline="") as stream:
        data = list(csv.DictReader(stream))
    data = data + [data[0]] if duplicate else data[:-1]
    path = tmp_path / "summary.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)
    with pytest.raises(ValueError, match="fifteen frozen sessions"):
        per_session(tmp_path, "summary.csv")


def test_historical_transcriptions_are_labelled_and_match_readme():
    from archive_readme_tables import archive_tables
    for name, content in archive_tables((ROOT / "README.md").read_bytes()).items():
        assert (ROOT / "report/readme_tables" / name).read_bytes() == content
    assert b"readme_transcription_only" in content
