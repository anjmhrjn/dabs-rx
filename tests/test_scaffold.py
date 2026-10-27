"""Placeholder so `uv run pytest` passes before real tests exist. Remove in CP2."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_files_present() -> None:
    for rel in ("databricks.yml", "resources/schemas.yml", "resources/volumes.yml"):
        assert (ROOT / rel).is_file(), rel
