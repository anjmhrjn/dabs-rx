"""Labels are off limits to the pipeline, scoring, and snapshot code. Only src/eval/ may read them."""

import re

import yaml

from generator.common import REPO_ROOT

FORBIDDEN = re.compile(
    r"eval_schema|eval-schema|injected_labels|labels[-_]path|data/labels|rx\.eval|schemas\.eval|volumes\.labels"
)
NO_LABELS_DIRS = ["src/pipeline", "src/scoring", "src/snapshot"]


def test_pipeline_scoring_and_snapshot_code_never_reference_labels() -> None:
    files = [p for d in NO_LABELS_DIRS for p in (REPO_ROOT / d).rglob("*.py")]
    assert files
    offenders = {str(p.relative_to(REPO_ROOT)): FORBIDDEN.findall(p.read_text()) for p in files}
    assert not {k: v for k, v in offenders.items() if v}


def test_only_evaluate_task_gets_eval_parameters() -> None:
    job = yaml.safe_load((REPO_ROOT / "resources" / "job.yml").read_text())["resources"]["jobs"]["rx_job"]
    for task in job["tasks"]:
        params = " ".join(task.get("spark_python_task", {}).get("parameters", []))
        if task["task_key"] == "evaluate":
            assert "eval" in params
        else:
            assert not FORBIDDEN.search(params), task["task_key"]
    keys = [t["task_key"] for t in job["tasks"]]
    assert keys == ["refresh_pipeline", "score_users", "snapshot_audit", "evaluate"]


def test_pipeline_config_has_no_eval_setting() -> None:
    conf = yaml.safe_load((REPO_ROOT / "resources" / "pipeline.yml").read_text())["resources"]["pipelines"][
        "rx_pipeline"
    ]
    assert not [k for k in conf["configuration"] if "eval" in k or "label" in k]
    assert not FORBIDDEN.search(str(conf["configuration"].values()))


def test_landing_upload_never_includes_labels() -> None:
    text = (REPO_ROOT / "generator" / "upload.sh").read_text() + (
        REPO_ROOT / "generator" / "upload_files.py"
    ).read_text()
    assert "refusing to stage a label file" in text
