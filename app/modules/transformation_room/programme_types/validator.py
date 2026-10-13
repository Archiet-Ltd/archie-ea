"""Programme type template validator (sdd.md 4.4, security.md section 7).

``validate_template`` checks the shape and content of one parsed template
against the existing registries (`ALL_ELEMENT_TYPES`, `WORKSTREAM_TYPES`,
`JOURNEY_STAGES`) and the "no invented data" rule. ``validate_manifest``
checks the manifest-level constraints (exactly twelve keys). ``validate_review``
checks the review block separately, since a missing or stale review is a
*reviewed* state (False), not a shape error added to the same list, per
sdd.md 4.4 rule 10 ("reported as 'review is stale', not as invalid, so M10
counts it as outstanding").

Run over every file by ``tests/test_programme_type_templates.py`` and by the
loader at load time (never raising into a request).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.models.archimate_element_types import ALL_ELEMENT_TYPES
from app.models.architecture_journey import JOURNEY_STAGES
from app.models.transformation_programme import WORKSTREAM_TYPES
from app.modules.transformation_room.programme_types.hashing import canonical_content_hash
from app.modules.transformation_room.programme_types.vendor_products import (
    VENDOR_PRODUCT_NAMES,
)

MANIFEST_SIZE = 12

# sdd.md 4.4 rule 7: a digit sequence in free text reads as a quantity, money
# or date. ADM phase letters (A-H, "Preliminary") are not digits, and the
# review block is checked separately -- neither needs an exception here.
# Word-bounded so a digit embedded in a product name (S/4HANA) does not trip
# the rule -- only a standalone numeric token (a plausible quantity, money
# amount or date/year) does.
_DIGIT_SEQUENCE = re.compile(r"\b\d+\b")

# security.md 7.5: the only accepted review_kind today.
ACCEPTED_REVIEW_KINDS = ("expert_role_review",)
_ALL_REVIEW_KINDS = ACCEPTED_REVIEW_KINDS + ("accountable_signoff",)
_BANNED_REVIEW_WORDS = ("approved", "certified", "compliant")


def _check_free_text(container: Mapping[str, Any], field: str, path: str, errors: list[str]) -> None:
    """Rule 7: *field* on *container* must carry no digit unless a sibling
    ``numbers_ok`` key on the same container gives a non-empty reason."""
    value = container.get(field)
    if not isinstance(value, str) or not _DIGIT_SEQUENCE.search(value):
        return
    reason = container.get("numbers_ok")
    if isinstance(reason, str) and reason.strip():
        return
    errors.append(
        f"{path}.{field}: contains a digit (quantity, money or date) with no "
        f"'numbers_ok: <reason>' exception on the same object"
    )


def validate_manifest(manifest_keys: list[Any]) -> list[str]:
    """Rule 1 (manifest half): the launch set is exactly twelve keys."""
    errors = []
    if len(manifest_keys) != MANIFEST_SIZE:
        errors.append(
            f"_manifest.yaml: must list exactly {MANIFEST_SIZE} keys, found {len(manifest_keys)}"
        )
    if len(set(manifest_keys)) != len(manifest_keys):
        errors.append("_manifest.yaml: duplicate keys")
    return errors


def validate_template(
    template: Mapping[str, Any], *, expected_key: str, manifest_keys: list[str] | None = None
) -> list[str]:
    """Shape and content errors for one parsed template file. Never raises."""
    errors: list[str] = []
    if not isinstance(template, Mapping):
        return [f"{expected_key}: file did not parse to a mapping"]

    key = template.get("key")
    if key != expected_key:
        errors.append(f"{expected_key}: key {key!r} does not match the file stem {expected_key!r}")
    if manifest_keys is not None and key not in manifest_keys:
        errors.append(f"{expected_key}: key is absent from _manifest.yaml")

    # Rule 7 on the top-level free-text fields.
    _check_free_text(template, "name", expected_key, errors)
    _check_free_text(template, "summary", expected_key, errors)

    # Rule 9: arb_required_at_decide missing or not boolean.
    if not isinstance(template.get("arb_required_at_decide"), bool):
        errors.append(f"{expected_key}: arb_required_at_decide is missing or not a boolean")

    # Rule 8: vendor_specific: false and a known vendor product name appears
    # anywhere in the file's free text (name, summary, and every workstream/
    # stage/deliverable text field below).
    vendor_specific = template.get("vendor_specific")
    all_text_blobs: list[str] = []
    if isinstance(template.get("name"), str):
        all_text_blobs.append(template["name"])
    if isinstance(template.get("summary"), str):
        all_text_blobs.append(template["summary"])

    # Workstreams: rules 5, 6 (workstream key dupes), rule 7.
    workstreams = template.get("workstreams")
    workstream_keys: list[str] = []
    if not isinstance(workstreams, list) or not workstreams:
        errors.append(f"{expected_key}: workstreams must be a non-empty list")
        workstreams = []
    for workstream in workstreams:
        if not isinstance(workstream, Mapping):
            errors.append(f"{expected_key}: a workstream entry is not a mapping")
            continue
        wkey = workstream.get("key")
        if wkey:
            workstream_keys.append(wkey)
        wtype = workstream.get("workstream_type")
        if wtype not in WORKSTREAM_TYPES:
            errors.append(
                f"{expected_key}.workstreams[{wkey}]: workstream_type {wtype!r} is not "
                f"one of {WORKSTREAM_TYPES}"
            )
        _check_free_text(workstream, "objective", f"{expected_key}.workstreams[{wkey}]", errors)
        if isinstance(workstream.get("objective"), str):
            all_text_blobs.append(workstream["objective"])
        if isinstance(workstream.get("name"), str):
            all_text_blobs.append(workstream["name"])
    if len(set(workstream_keys)) != len(workstream_keys):
        errors.append(f"{expected_key}: duplicate workstream key(s)")
    if "programme_office" not in workstream_keys:
        errors.append(f"{expected_key}: missing the required 'programme_office' workstream")

    # Stages: rule 2 (all five, in order, each with deliverables), rules 3/4/6/7.
    stages = template.get("stages")
    deliverable_codes: list[str] = []
    if not isinstance(stages, Mapping):
        errors.append(f"{expected_key}: stages must be a mapping")
        stages = {}
    for stage_key in JOURNEY_STAGES:
        stage = stages.get(stage_key)
        if stage is None:
            errors.append(f"{expected_key}.stages: missing stage {stage_key!r}")
            continue
        if not isinstance(stage, Mapping):
            errors.append(f"{expected_key}.stages.{stage_key}: not a mapping")
            continue
        _check_free_text(stage, "gate", f"{expected_key}.stages.{stage_key}", errors)
        if isinstance(stage.get("gate"), str):
            all_text_blobs.append(stage["gate"])
        deliverables = stage.get("deliverables")
        if not isinstance(deliverables, list) or not deliverables:
            errors.append(f"{expected_key}.stages.{stage_key}: no deliverables")
            deliverables = []
        for deliverable in deliverables:
            if not isinstance(deliverable, Mapping):
                errors.append(f"{expected_key}.stages.{stage_key}: a deliverable is not a mapping")
                continue
            code = deliverable.get("code")
            if code:
                deliverable_codes.append(code)
            path = f"{expected_key}.stages.{stage_key}.deliverables[{code}]"
            element_types = deliverable.get("element_types")
            if not isinstance(element_types, list) or not element_types:
                errors.append(f"{path}: no element_types")
                element_types = []
            for element_type in element_types:
                if element_type not in ALL_ELEMENT_TYPES:
                    errors.append(f"{path}: element_type {element_type!r} is not a key of ALL_ELEMENT_TYPES")
            deliverable_workstream = deliverable.get("workstream")
            if not deliverable_workstream:
                errors.append(f"{path}: no workstream")
            elif deliverable_workstream not in workstream_keys:
                errors.append(f"{path}: workstream {deliverable_workstream!r} does not exist in this file")
            _check_free_text(deliverable, "purpose", path, errors)
            if isinstance(deliverable.get("purpose"), str):
                all_text_blobs.append(deliverable["purpose"])
            if isinstance(deliverable.get("name"), str):
                all_text_blobs.append(deliverable["name"])
    if len(set(deliverable_codes)) != len(deliverable_codes):
        errors.append(f"{expected_key}: duplicate deliverable code(s)")

    if vendor_specific is False:
        haystack = " ".join(all_text_blobs).casefold()
        for product in VENDOR_PRODUCT_NAMES:
            if product.casefold() in haystack:
                errors.append(
                    f"{expected_key}: vendor_specific is false but the vendor product name "
                    f"{product!r} appears in the text"
                )
    elif not isinstance(vendor_specific, bool):
        errors.append(f"{expected_key}: vendor_specific is missing or not a boolean")

    return errors


def validate_review(template: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Whether *template* carries a current, accepted review.

    Returns ``(reviewed, problems)``. ``reviewed`` is False when the review is
    merely absent or stale -- that is the ordinary "not yet released" state,
    not a file defect. ``problems`` lists real defects in the review block
    itself (closed-enum violation, banned wording), which the shape validator
    above does not check.
    """
    problems: list[str] = []
    review = template.get("review")
    if review is None:
        return False, problems
    if not isinstance(review, Mapping):
        problems.append("review: must be a mapping")
        return False, problems

    review_kind = review.get("review_kind")
    if review_kind not in _ALL_REVIEW_KINDS:
        problems.append(f"review.review_kind: {review_kind!r} is not a recognised review kind")
    elif review_kind not in ACCEPTED_REVIEW_KINDS:
        problems.append(f"review.review_kind: {review_kind!r} is not accepted yet")

    for field, value in review.items():
        if isinstance(value, str):
            lowered = value.casefold()
            for banned in _BANNED_REVIEW_WORDS:
                if banned in lowered:
                    problems.append(f"review.{field}: must not claim {banned!r}")

    if problems:
        return False, problems

    recomputed = canonical_content_hash(template)
    stored = review.get("content_sha256")
    if stored != recomputed:
        # Stale, not invalid -- sdd.md 4.4 rule 10.
        return False, []

    return True, []


__all__ = [
    "MANIFEST_SIZE",
    "ACCEPTED_REVIEW_KINDS",
    "validate_manifest",
    "validate_template",
    "validate_review",
]
