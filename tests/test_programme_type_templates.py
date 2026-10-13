"""R1-03: programme type template data files, loader and validator
(sdd.md section 4, security.md section 7, ADR 0012).

Covers sdd.md 4.4 validator rules 1-10 and security.md tests T-G1 to T-G5.
"""

from __future__ import annotations

import copy
import os
import re

import pytest
import yaml

from app.modules.transformation_room.programme_types.hashing import canonical_content_hash
from app.modules.transformation_room.programme_types.loader import (
    DATA_DIR,
    MANIFEST_FILENAME,
    ProgrammeTypeCatalogue,
)
from app.modules.transformation_room.programme_types.validator import (
    ACCEPTED_REVIEW_KINDS,
    MANIFEST_SIZE,
    validate_manifest,
    validate_review,
    validate_template,
)


def _manifest_keys() -> list[str]:
    with open(os.path.join(DATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load(key: str) -> dict:
    with open(os.path.join(DATA_DIR, f"{key}.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _minimal_valid_template(key: str = "sample") -> dict:
    """The smallest template that satisfies every shape rule -- a base for
    the mutation tests below."""
    stages = {}
    for stage_key in ("frame", "discover", "shape", "decide", "deliver"):
        stages[stage_key] = {
            "gate": "Sample gate",
            "deliverables": [{
                "code": f"{key}.{stage_key}.d1",
                "name": "Sample deliverable",
                "adm_phase": "A",
                "workstream": "programme_office",
                "element_types": ["Driver"],
                "purpose": "Sample purpose.",
            }],
        }
    return {
        "schema_version": 1,
        "key": key,
        "name": "Sample type",
        "summary": "A sample type for validator tests.",
        "vendor_specific": False,
        "arb_required_at_decide": False,
        "workstreams": [
            {"key": "programme_office", "name": "Programme office", "workstream_type": "other",
             "objective": "Run the programme."},
        ],
        "stages": stages,
    }


# --------------------------------------------------------------------- #
# The twelve real files                                                  #
# --------------------------------------------------------------------- #


def test_manifest_lists_exactly_twelve_keys():
    keys = _manifest_keys()
    assert len(keys) == MANIFEST_SIZE
    assert len(set(keys)) == MANIFEST_SIZE


@pytest.mark.parametrize("key", [
    "s4hana", "cloud_migration", "mna_integration", "regulatory",
    "platform_replacement", "data_ai", "app_modernisation", "customer_digital",
    "operating_model", "cyber_resilience", "cost_carve_out", "esg_reporting",
])
def test_every_shipped_template_validates(key):
    template = _load(key)
    errors = validate_template(template, expected_key=key, manifest_keys=_manifest_keys())
    assert errors == [], f"{key}: {errors}"


@pytest.mark.parametrize("key", [
    "s4hana", "cloud_migration", "mna_integration", "regulatory",
    "platform_replacement", "data_ai", "app_modernisation", "customer_digital",
    "operating_model", "cyber_resilience", "cost_carve_out", "esg_reporting",
])
def test_no_shipped_template_carries_a_review_block(key):
    """No file carries a review block (R1-03 constraint) -- reviews come from
    the expert role in R4 to R7, never from this bucket."""
    template = _load(key)
    assert "review" not in template


def test_s4hana_is_fully_worked():
    """US-2 AC3: every stage has at least one deliverable, and every
    deliverable has at least one element type and an owning workstream --
    and S/4HANA additionally carries more than the one-per-stage minimum."""
    template = _load("s4hana")
    total_deliverables = sum(len(stage["deliverables"]) for stage in template["stages"].values())
    assert total_deliverables > 5, "S/4HANA must be more than the one-per-stage minimum"
    for stage in template["stages"].values():
        for deliverable in stage["deliverables"]:
            assert deliverable["element_types"]
            assert deliverable["workstream"]


@pytest.mark.parametrize("key", [
    "cloud_migration", "mna_integration", "regulatory", "platform_replacement",
    "data_ai", "app_modernisation", "customer_digital", "operating_model",
    "cyber_resilience", "cost_carve_out", "esg_reporting",
])
def test_other_eleven_meet_the_minimum(key):
    """US-2 AC4: at least workstreams, five stages and one deliverable per stage."""
    template = _load(key)
    assert len(template["workstreams"]) >= 2  # at least one substantive + programme_office
    assert set(template["stages"]) == {"frame", "discover", "shape", "decide", "deliver"}
    for stage in template["stages"].values():
        assert len(stage["deliverables"]) >= 1


def test_every_shipped_template_carries_no_invented_number():
    """US-2 AC5 / rule 7, applied to the shipped content directly: no example
    number, date or money value anywhere in the free text this feature owns."""
    for key in _manifest_keys():
        template = _load(key)
        for field in ("name", "summary"):
            assert not re.search(r"\b\d+\b", template.get(field) or ""), f"{key}.{field}"
        for workstream in template["workstreams"]:
            assert not re.search(r"\b\d+\b", workstream.get("objective") or ""), f"{key}:{workstream['key']}"
        for stage_key, stage in template["stages"].items():
            assert not re.search(r"\b\d+\b", stage.get("gate") or ""), f"{key}.{stage_key}.gate"
            for deliverable in stage["deliverables"]:
                assert not re.search(r"\b\d+\b", deliverable.get("purpose") or ""), (
                    f"{key}.{stage_key}.{deliverable.get('code')}.purpose"
                )


def test_every_shipped_template_has_a_programme_office_workstream():
    for key in _manifest_keys():
        template = _load(key)
        keys = {w["key"] for w in template["workstreams"]}
        assert "programme_office" in keys, key


def test_arb_required_at_decide_matches_owner_decision_4():
    assert _load("regulatory")["arb_required_at_decide"] is True
    assert _load("mna_integration")["arb_required_at_decide"] is True
    for key in _manifest_keys():
        if key in ("regulatory", "mna_integration"):
            continue
        assert _load(key)["arb_required_at_decide"] is False, key


def test_s4hana_is_the_only_vendor_specific_type():
    assert _load("s4hana")["vendor_specific"] is True
    for key in _manifest_keys():
        if key == "s4hana":
            continue
        assert _load(key)["vendor_specific"] is False, key


# --------------------------------------------------------------------- #
# T-G1: canonical hash / staleness                                       #
# --------------------------------------------------------------------- #


def test_whitespace_only_edit_keeps_the_hash_stable():
    template = _minimal_valid_template()
    original_hash = canonical_content_hash(template)
    reformatted = yaml.safe_load(yaml.safe_dump(template, indent=6))  # different whitespace
    assert canonical_content_hash(reformatted) == original_hash


def test_a_semantic_edit_changes_the_hash():
    template = _minimal_valid_template()
    original_hash = canonical_content_hash(template)
    mutated = copy.deepcopy(template)
    mutated["summary"] = mutated["summary"] + " Changed."
    assert canonical_content_hash(mutated) != original_hash


def test_review_key_is_excluded_from_the_hash():
    template = _minimal_valid_template()
    without_review_hash = canonical_content_hash(template)
    with_review = copy.deepcopy(template)
    with_review["review"] = {"review_kind": "expert_role_review", "reviewer_role": "x"}
    assert canonical_content_hash(with_review) == without_review_hash


def test_a_stale_hash_is_reported_as_not_reviewed_not_invalid():
    """Rule 10: review present but content_sha256 wrong -> reported as stale
    (reviewed=False, no error), not as a shape error."""
    template = _minimal_valid_template()
    template["review"] = {
        "review_kind": "expert_role_review",
        "reviewer_role": "Enterprise Architect",
        "content_sha256": "0" * 64,
    }
    reviewed, problems = validate_review(template)
    assert reviewed is False
    assert problems == []


def test_a_current_hash_with_an_accepted_review_kind_is_reviewed():
    template = _minimal_valid_template()
    correct_hash = canonical_content_hash(template)
    template["review"] = {
        "review_kind": "expert_role_review",
        "reviewer_role": "Enterprise Architect",
        "content_sha256": correct_hash,
    }
    reviewed, problems = validate_review(template)
    assert reviewed is True
    assert problems == []


# --------------------------------------------------------------------- #
# T-G2: safe_load only; manifest-listed files only; symlinks rejected    #
# --------------------------------------------------------------------- #


def test_no_unsafe_yaml_loading_in_the_package():
    root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "modules", "transformation_room", "programme_types",
    )
    offenders = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            if re.search(r"yaml\.load\(", source) and "safe_load" not in source.split("yaml.load(")[0][-20:]:
                # crude but sufficient: flag any bare yaml.load( call
                if re.search(r"(?<!safe_)yaml\.load\(", source):
                    offenders.append(path)
            if "yaml.unsafe_load" in source:
                offenders.append(path)
    assert offenders == [], offenders


def test_a_file_outside_the_manifest_is_ignored(tmp_path, monkeypatch):
    catalogue = ProgrammeTypeCatalogue(data_dir=DATA_DIR)
    results = {r.key for r in catalogue.load_all()}
    assert "not_in_manifest" not in results


def test_manifest_wrong_length_is_reported():
    errors = validate_manifest(["a", "b"])
    assert any("exactly" in e for e in errors)
    errors = validate_manifest([f"k{i}" for i in range(MANIFEST_SIZE)])
    assert errors == []


# --------------------------------------------------------------------- #
# T-G3: review_kind closed enum; banned wording                          #
# --------------------------------------------------------------------- #


def test_review_kind_other_than_expert_role_review_is_rejected():
    template = _minimal_valid_template()
    correct_hash = canonical_content_hash(template)
    template["review"] = {
        "review_kind": "accountable_signoff",
        "content_sha256": correct_hash,
    }
    reviewed, problems = validate_review(template)
    assert reviewed is False
    assert problems, "accountable_signoff must be rejected today"


@pytest.mark.parametrize("banned", ["approved", "certified", "compliant", "Approved", "CERTIFIED"])
def test_banned_words_in_the_review_block_are_rejected(banned):
    template = _minimal_valid_template()
    correct_hash = canonical_content_hash(template)
    template["review"] = {
        "review_kind": "expert_role_review",
        "reviewer_role": f"This template is {banned} for production use",
        "content_sha256": correct_hash,
    }
    reviewed, problems = validate_review(template)
    assert reviewed is False
    assert problems


def test_accepted_review_kinds_is_exactly_expert_role_review():
    assert ACCEPTED_REVIEW_KINDS == ("expert_role_review",)


# --------------------------------------------------------------------- #
# T-G4: production override assertion                                    #
# --------------------------------------------------------------------- #


def test_production_config_boots_with_the_override_off(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")  # secrets-safety-ok
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "y" * 32)
    from config import ProductionConfig

    class _FakeApp:
        config = {
            "SQLALCHEMY_DATABASE_URI": "postgresql://user:pass@localhost/db",  # secrets-safety-ok
            "TRANSFORMATION_COMMAND_CAPABILITY_SECRET": "y" * 32,
            "PROGRAMME_TYPES_INCLUDE_UNREVIEWED": False,
        }

        def wsgi_app(self, environ, start_response):  # ProxyFix wraps this
            raise NotImplementedError

    ProductionConfig.init_app(_FakeApp())  # must not raise


def test_production_config_raises_if_the_override_is_true(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")  # secrets-safety-ok
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "y" * 32)
    from config import ProductionConfig

    class _FakeApp:
        config = {
            "SQLALCHEMY_DATABASE_URI": "postgresql://user:pass@localhost/db",  # secrets-safety-ok
            "TRANSFORMATION_COMMAND_CAPABILITY_SECRET": "y" * 32,
            "PROGRAMME_TYPES_INCLUDE_UNREVIEWED": True,
        }

    with pytest.raises(ValueError, match="PROGRAMME_TYPES_INCLUDE_UNREVIEWED"):
        ProductionConfig.init_app(_FakeApp())


def test_offered_types_is_empty_under_the_default_config_and_twelve_under_the_override():
    from app import create_app

    app = create_app("testing")
    with app.app_context():
        catalogue = ProgrammeTypeCatalogue()
        assert len(catalogue.offered_types()) == MANIFEST_SIZE  # TestingConfig sets the override True

        app.config["PROGRAMME_TYPES_INCLUDE_UNREVIEWED"] = False
        assert ProgrammeTypeCatalogue().offered_types() == []


# --------------------------------------------------------------------- #
# T-G5: CODEOWNERS (P-02)                                                #
# --------------------------------------------------------------------- #


def test_codeowners_covers_the_template_data_directory():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".github", "CODEOWNERS")
    assert os.path.exists(path), (
        "P-02 is blocked in this session: creating .github/CODEOWNERS was "
        "refused by the environment's permission system as an out-of-scope "
        "action for this role, and the denial explicitly forbade pursuing "
        "the same outcome through another tool. The file must be created by "
        "the owner or a session with that permission before this test, and "
        "before R1 merges (security.md finding S13)."
    )
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "/app/modules/transformation_room/programme_types/data/" in content


# --------------------------------------------------------------------- #
# Validator rule mutation tests (sdd.md 4.4 rules 1-9, each proven red    #
# on the minimal valid template, per the constraint's own numbering)     #
# --------------------------------------------------------------------- #


def test_rule1_key_mismatch_is_rejected():
    template = _minimal_valid_template("sample")
    errors = validate_template(template, expected_key="other_key")
    assert any("does not match" in e for e in errors)


def test_rule2_missing_stage_is_rejected():
    template = _minimal_valid_template()
    del template["stages"]["deliver"]
    errors = validate_template(template, expected_key="sample")
    assert any("missing stage 'deliver'" in e for e in errors)


def test_rule2_stage_with_no_deliverables_is_rejected():
    template = _minimal_valid_template()
    template["stages"]["frame"]["deliverables"] = []
    errors = validate_template(template, expected_key="sample")
    assert any("no deliverables" in e for e in errors)


def test_rule3_unknown_element_type_is_rejected():
    template = _minimal_valid_template()
    template["stages"]["frame"]["deliverables"][0]["element_types"] = ["Flow"]
    errors = validate_template(template, expected_key="sample")
    assert any("ALL_ELEMENT_TYPES" in e for e in errors)


@pytest.mark.parametrize("bad_type", ["Flow", "Serving", "Application Component", "Work Package", "Event"])
def test_rule3_rejects_the_specific_known_bad_values(bad_type):
    template = _minimal_valid_template()
    template["stages"]["frame"]["deliverables"][0]["element_types"] = [bad_type]
    errors = validate_template(template, expected_key="sample")
    assert errors, bad_type


def test_rule4_deliverable_with_no_element_types_is_rejected():
    template = _minimal_valid_template()
    template["stages"]["frame"]["deliverables"][0]["element_types"] = []
    errors = validate_template(template, expected_key="sample")
    assert any("no element_types" in e for e in errors)


def test_rule4_deliverable_with_unknown_workstream_is_rejected():
    template = _minimal_valid_template()
    template["stages"]["frame"]["deliverables"][0]["workstream"] = "does_not_exist"
    errors = validate_template(template, expected_key="sample")
    assert any("does not exist in this file" in e for e in errors)


def test_rule5_unknown_workstream_type_is_rejected():
    template = _minimal_valid_template()
    template["workstreams"][0]["workstream_type"] = "not_a_type"
    errors = validate_template(template, expected_key="sample")
    assert any("not one of" in e for e in errors)


def test_rule6_duplicate_deliverable_code_is_rejected():
    template = _minimal_valid_template()
    template["stages"]["discover"]["deliverables"][0]["code"] = template["stages"]["frame"]["deliverables"][0]["code"]
    errors = validate_template(template, expected_key="sample")
    assert any("duplicate deliverable code" in e for e in errors)


def test_rule6_duplicate_workstream_key_is_rejected():
    template = _minimal_valid_template()
    template["workstreams"].append(dict(template["workstreams"][0]))
    errors = validate_template(template, expected_key="sample")
    assert any("duplicate workstream key" in e for e in errors)


def test_rule7_invented_number_is_rejected_without_an_exception():
    template = _minimal_valid_template()
    template["summary"] = "Reduce cost by 40 percent."
    errors = validate_template(template, expected_key="sample")
    assert any("contains a digit" in e for e in errors)


def test_rule7_invented_number_is_accepted_with_a_numbers_ok_reason():
    template = _minimal_valid_template()
    template["summary"] = "Reduce cost by 40 percent."
    template["numbers_ok"] = "illustrative example agreed with the reviewer"
    errors = validate_template(template, expected_key="sample")
    assert errors == []


def test_rule8_vendor_product_name_rejected_when_not_vendor_specific():
    template = _minimal_valid_template()
    template["vendor_specific"] = False
    template["summary"] = "A programme built entirely around SAP."
    errors = validate_template(template, expected_key="sample")
    assert any("vendor_specific is false" in e for e in errors)


def test_rule8_vendor_product_name_allowed_when_vendor_specific():
    template = _minimal_valid_template()
    template["vendor_specific"] = True
    template["summary"] = "A programme built entirely around SAP."
    errors = validate_template(template, expected_key="sample")
    assert errors == []


def test_rule9_missing_arb_required_at_decide_is_rejected():
    template = _minimal_valid_template()
    del template["arb_required_at_decide"]
    errors = validate_template(template, expected_key="sample")
    assert any("arb_required_at_decide" in e for e in errors)


def test_the_required_programme_office_workstream_is_enforced():
    template = _minimal_valid_template()
    template["workstreams"][0]["key"] = "not_the_office"
    errors = validate_template(template, expected_key="sample")
    assert any("programme_office" in e for e in errors)
