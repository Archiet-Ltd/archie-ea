"""The store-agreement check runs in CI on a seeded database and gates the release."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CI = REPO / ".github" / "workflows" / "ci.yml"


def _jobs():
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def test_ci_names_the_store_agreement_job():
    job = _jobs()["store-agreement"]
    assert job["name"] == "Store agreement (screens answer one number)"
    assert "postgres" in job["services"]


def test_job_seeds_the_demo_company_and_runs_the_gate():
    steps = "\n".join(str(s.get("run", "")) for s in _jobs()["store-agreement"]["steps"])
    for needed in ("init-db", "schema-upgrade", "seed-demo-company",
                   "--gate store-agreement"):
        assert needed in steps
    assert steps.index("seed-demo-company") < steps.index("--gate store-agreement")


def test_release_image_waits_on_it():
    needs = _jobs()["release-image"]["needs"]
    assert "store-agreement" in needs


def test_gate_is_registered_with_a_ratchet_baseline():
    import json
    base = json.loads((REPO / "verification_baseline.json").read_text(encoding="utf-8"))
    assert "store_agreement" in base["ratchets"]
    assert "--gate" in "".join(
        str(s.get("run", "")) for s in _jobs()["store-agreement"]["steps"])
