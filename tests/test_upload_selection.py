"""Upload file selection. Uses a tiny fake data/ tree, no Databricks."""

from datetime import date
from pathlib import Path

import pytest

from generator.simulate import SOURCES
from generator.upload_files import REFERENCE_FILES, daily_files, reference_files, stage

START = date(2026, 3, 1)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    for source in SOURCES:
        for d in range(1, 6):
            p = tmp_path / "landing" / source / f"{source}_2026-03-{d:02d}.csv"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("h\n")
    for name in REFERENCE_FILES.values():
        (tmp_path / "reference").mkdir(exist_ok=True)
        (tmp_path / "reference" / name).write_text("h\n")
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "injected_labels.csv").write_text("user_id,pattern,start_date\n")
    return tmp_path


def test_daily_range_is_inclusive_and_per_source(data_dir: Path) -> None:
    files = daily_files(data_dir, START, 2, 4)
    assert len(files) == 3 * len(SOURCES)
    assert {rel for _, rel in files if rel.startswith("adc_events/")} == {
        f"adc_events/adc_events_2026-03-0{d}.csv" for d in (2, 3, 4)
    }


def test_missing_day_raises(data_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        daily_files(data_dir, START, 4, 6)


def test_bad_range_raises(data_dir: Path) -> None:
    with pytest.raises(ValueError):
        daily_files(data_dir, START, 0, 3)


def test_stage_layout_and_no_labels(data_dir: Path, tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    counts = stage(daily_files(data_dir, START, 1, 5) + reference_files(data_dir), staging)
    assert counts["reference"] == len(REFERENCE_FILES)
    assert all(counts[s] == 5 for s in SOURCES)
    assert (staging / "reference" / "formulary" / "formulary.csv").exists()
    assert not [p for p in staging.rglob("*") if "label" in p.name]


def test_stage_refuses_label_files(data_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="label"):
        stage([(data_dir / "labels" / "injected_labels.csv", "x/injected_labels.csv")], tmp_path / "s")
