"""CI service containers must use the quota-free postgres mirror."""
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
POSTGRES_IMAGE = "mirror.gcr.io/library/postgres:16"


def _workflow_files():
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _postgres_service_images():
    images = []
    for path in _workflow_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (data.get("jobs") or {}).items():
            for name, svc in ((job or {}).get("services") or {}).items():
                image = str((svc or {}).get("image", ""))
                if "postgres" in image.split(":")[0].split("/")[-1]:
                    images.append((path.name, job_name, name, image))
    return images


def test_ci_postgres_services_use_the_mirror():
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    ci_images = [
        (job, svc["image"])
        for job, body in ci["jobs"].items()
        for svc in ((body or {}).get("services") or {}).values()
        if svc.get("image", "").split("/")[-1].startswith("postgres")
    ]
    assert len(ci_images) >= 9, ci_images
    assert all(image == POSTGRES_IMAGE for _, image in ci_images), ci_images


def test_all_workflow_postgres_services_use_the_mirror():
    found = _postgres_service_images()
    assert len(found) >= 9, found
    assert all(i[3] == POSTGRES_IMAGE for i in found), found


def test_no_workflow_references_ecr_public():
    offenders = [p.name for p in _workflow_files() if "public.ecr.aws" in p.read_text(encoding="utf-8")]
    assert offenders == []
