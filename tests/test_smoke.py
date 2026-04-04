from pathlib import Path


def test_workspace_scaffold_exists():
    assert Path("README.md").exists()
