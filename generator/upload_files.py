"""Pick which local files to upload to the landing volume and stage them in the volume's layout.

Daily files are chosen by 1-based day number counted from simulation.start_date. Reference files are always
included. Nothing under data/labels/ is ever staged: labels stay out of the landing volume.

Run (normally via upload.sh): uv run python -m generator.upload_files <first_day> <last_day> <staging_dir>
"""

import shutil
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from generator.common import DATA_DIR, load_config
from generator.simulate import SOURCES

REFERENCE_FILES = {
    "formulary": "formulary.csv",
    "shortages": "shortages.csv",
    "units": "units.csv",
    "cabinets": "cabinets.csv",
    "cabinet_par": "cabinet_par.csv",
    "dispense_units": "dispense_units.csv",
    "nadac_prices": "nadac_subset.json",
}


def daily_files(data_dir: Path, start: date, first: int, last: int) -> list[tuple[Path, str]]:
    """(local path, path inside the landing volume) for every source's file on days first..last."""
    if not 1 <= first <= last:
        raise ValueError(f"bad day range {first}..{last}")
    out = []
    for source in SOURCES:
        for d in range(first, last + 1):
            day = (start + timedelta(days=d - 1)).isoformat()
            path = data_dir / "landing" / source / f"{source}_{day}.csv"
            if not path.exists():
                raise FileNotFoundError(f"{path} missing. Run generator.simulate first.")
            out.append((path, f"{source}/{path.name}"))
    return out


def reference_files(data_dir: Path) -> list[tuple[Path, str]]:
    out = []
    for table, name in REFERENCE_FILES.items():
        path = data_dir / "reference" / name
        if not path.exists():
            raise FileNotFoundError(f"{path} missing. Run generator.build_formulary and generator.simulate first.")
        out.append((path, f"reference/{table}/{name}"))
    return out


def stage(files: list[tuple[Path, str]], staging: Path) -> Counter:
    counts: Counter = Counter()
    for src, rel in files:
        if "labels" in src.parts:
            raise ValueError(f"refusing to stage a label file: {src}")
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        counts[rel.split("/")[0]] += 1
    return counts


def main() -> None:
    first, last, staging = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    start = date.fromisoformat(load_config()["simulation"]["start_date"])
    daily = stage(daily_files(DATA_DIR, start, first, last), staging / "daily")
    ref = stage(reference_files(DATA_DIR), staging / "reference")
    last_day = start + timedelta(days=last - 1)
    print(f"staged days {first}-{last} ({start + timedelta(days=first - 1)} to {last_day}): {dict(daily)}")
    print(f"staged reference files: {sum(ref.values())}")


if __name__ == "__main__":
    main()
