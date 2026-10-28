"""Bundle variables that must agree with the generator config."""

from datetime import date, timedelta

import yaml

from generator.common import REPO_ROOT, load_config


def bundle_vars() -> dict:
    with (REPO_ROOT / "databricks.yml").open() as f:
        return {k: v["default"] for k, v in yaml.safe_load(f)["variables"].items()}


def test_window_matches_simulator() -> None:
    sim = load_config()["simulation"]
    v = bundle_vars()
    start = date.fromisoformat(sim["start_date"])
    assert v["window_start"] == start.isoformat()
    assert v["window_end"] == (start + timedelta(days=sim["days"])).isoformat()


def test_thresholds_present() -> None:
    assert int(bundle_vars()["waste_delay_minutes"]) > 0


def test_pipeline_gets_every_rx_setting_silver_reads() -> None:
    with (REPO_ROOT / "resources" / "pipeline.yml").open() as f:
        conf = yaml.safe_load(f)["resources"]["pipelines"]["rx_pipeline"]["configuration"]
    source = "".join(p.read_text() for p in (REPO_ROOT / "src" / "pipeline").glob("*.py"))
    import re

    used = set(re.findall(r'spark\.conf\.get\("(rx\.[a-z_]+)"\)', source))
    assert used <= set(conf), used - set(conf)
