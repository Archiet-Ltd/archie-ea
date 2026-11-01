"""The committed genome schema accepts what the generator builds and rejects a genome that lacks a required key."""
import copy
import json
import uuid
from pathlib import Path

import pytest

from app.modules.codegen.services import genome_validator


@pytest.fixture
def generated_genome(db_session, make_org, tenant_ctx):
    from app.models.application import ApplicationComponent
    from app.models.solution_models import Solution, SolutionArchiMateElement
    from app.modules.codegen.services.aabl_compiler import compile_genome

    org = make_org("genome")
    with tenant_ctx(org.id):
        suffix = uuid.uuid4().hex[:8]
        component = ApplicationComponent(
            name="Genome Schema Service %s" % suffix, organization_id=org.id,
            lifecycle_status="operational", description="Fixture for the genome schema test.",
        )
        db_session.add(component)
        db_session.flush()
        solution = Solution(
            name="Genome schema solution %s" % suffix, description="Fixture.",
            organization_id=org.id, governance_status="draft", adm_phase="C", has_acm_domains=True,
        )
        db_session.add(solution)
        db_session.flush()
        db_session.add(SolutionArchiMateElement(
            solution_id=solution.id, layer_type="application",
            element_id=component.archimate_element_id, element_table="archimate_elements",
            element_name=component.name, element_role="primary",
        ))
        db_session.flush()
        return compile_genome(solution.id, language="python-fastapi")


REQUIRED_KEYS = ("genome_version", "solution_id", "generated_at", "problem", "modules", "infrastructure", "security")


def test_generator_genome_validates(generated_genome):
    assert genome_validator.validate_genome(generated_genome) == []


@pytest.mark.parametrize("template", sorted(
    (Path(genome_validator.__file__).parents[1] / "genome_templates").glob("*.yaml")), ids=lambda p: p.stem)
def test_shipped_templates_validate(template):
    import yaml

    genome = yaml.safe_load(template.read_text())
    assert genome_validator.validate_genome(genome) == []


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_genome_missing_a_required_key_fails(generated_genome, key):
    broken = copy.deepcopy(generated_genome)
    del broken[key]
    errors = genome_validator.validate_genome(broken)
    assert errors and any(key in e for e in errors)


def test_module_without_aggregate_root_fails(generated_genome):
    broken = copy.deepcopy(generated_genome)
    next(iter(broken["modules"].values())).pop("aggregate_root")
    assert any("aggregate_root" in e for e in genome_validator.validate_genome(broken))


def test_unknown_field_key_fails(generated_genome):
    broken = copy.deepcopy(generated_genome)
    module = next(iter(broken["modules"].values()))
    next(iter(module["fields"].values()))[0]["surprise"] = True
    assert any("surprise" in e for e in genome_validator.validate_genome(broken))


def test_missing_schema_file_gives_a_sentence_and_a_next_step(generated_genome, monkeypatch, tmp_path):
    monkeypatch.setattr(genome_validator, "_SCHEMA", None)
    monkeypatch.setattr(genome_validator, "_SCHEMA_PATH", tmp_path / "absent.json")
    errors = genome_validator.validate_genome(generated_genome)
    assert errors == [genome_validator.SCHEMA_MISSING_MESSAGE]
    assert "Quick Generate" in errors[0] and "Errno" not in errors[0]
