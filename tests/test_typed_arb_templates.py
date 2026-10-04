"""Render-level contracts for the typed ARB governance workspace templates.

These tests exist because the expensive failures in this area are not logic
bugs, they are *rendering* bugs that look plausible:

* a second page header or breadcrumb creeping in when a partial is reused;
* a raw Tailwind colour family reappearing in a status treatment;
* a failed read rendering ``0`` counts, which a reader cannot distinguish from
  a measured zero;
* ``historical_unverified`` — a locked state — offering a decide or condition
  control because the template inferred authority instead of reading
  ``allowed_actions``;
* an absent value rendering as blank or ``None`` instead of an em dash.

They render the templates directly against hand-built read-model mappings that
match ``app.modules.transformation_room.arb_read_models`` exactly, so they run
without seeding the whole ARB graph and still fail if a template drifts from
the contract.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from flask import render_template


# The families DESIGN.md bans outright, plus the four the typed ARB blueprint
# additionally forbids because semantic tokens exist for every state it needs.
BANNED_COLOUR_FAMILIES = (
    "gray", "grey", "slate", "zinc", "neutral", "stone", "blue", "red",
    "amber", "emerald", "orange", "purple",
)

_COLOUR_RE = re.compile(
    r"\b(?:bg|text|border|ring|from|to|via|divide|outline|decoration|shadow|accent|fill|stroke)-"
    r"(?:" + "|".join(BANNED_COLOUR_FAMILIES) + r")-\d{2,3}\b"
)

_AWARE = datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc)
# ARBSubmissionEvidenceSnapshot.captured_at is NAIVE while every other typed
# timestamp is tz-aware. The templates must render both without touching
# tzinfo, so the fixtures deliberately mix them.
_NAIVE = datetime(2026, 8, 19, 14, 5)


def _identity(**overrides):
    base = {
        "review_item_id": 7,
        "review_cycle_id": 11,
        "review_number": "ARB-2026-0042",
        "cycle_number": 1,
        "subject_type": "decision_brief",
        "subject_id": 3,
        "predecessor_cycle_id": None,
        "successor_cycle_id": None,
    }
    base.update(overrides)
    return base


def _no_actions():
    return {
        "can_decide": False,
        "decision_denial_reason": None,
        "decision_outcomes": [],
        "conditions": {},
    }


def available_review(**overrides):
    """A `state="available"` review with one pending condition."""
    review = {
        "state": "available",
        "reason": None,
        "identity": _identity(),
        "subject": {
            "type": "decision_brief",
            "label": "Decision Brief",
            "icon": "file-check",
            "title": "Consolidate the three payroll systems",
            "canonical_url": "/solutions/programmes/1/workstreams/2/decision",
        },
        "evidence": {
            "evidence_type": "decision_brief_version",
            "evidence_id": 55,
            "version": 3,
            "schema_version": None,
            "policy_version": "policy-v2",
            "captured_by_display": "Ada Lovelace",
            "captured_at": _NAIVE,
            "content_hash": "a" * 64,
            "hash_state": "verified",
            "hash_reason": None,
            "sections": [
                {"key": "objective", "label": "Objective", "value": "Reduce payroll platforms to one."},
                {"key": "unknowns", "label": "Unknowns", "value": None},
            ],
        },
        "decision": {
            "event": {
                "decision_event_id": 91,
                "outcome": "approved_with_conditions",
                "from_state": "under_review",
                "to_state": "approved_with_conditions",
                "rationale": "Proceed once the data-retention evidence lands.",
                "actor_display": "Grace Hopper",
                "recorded_at": _AWARE,
            },
            # Deliberately DIFFERENT from the recorded event: the projection has
            # moved on. The template must show both, never relabel the event.
            "projection": {
                "status": "approved",
                "terminal_outcome": "approved_with_conditions",
                "closed_at": _AWARE,
                "condition_projection_revision": 2,
                "review_status": "approved",
                "condition_count": 1,
                "blocking_condition_count": 0,
            },
            "recorded_historical_outcome": None,
        },
        "conditions": [
            {
                "condition_id": 21,
                "anchor": "condition-21",
                "condition_number": 1,
                "description": "Publish the agreed data-retention schedule.",
                "category": None,
                "due_date": None,
                "blocks_execution": True,
                "status": "pending",
                "revision": 0,
                "responsible_display": None,
                "verified_by_display": None,
                "verified_at": None,
                "evidence": None,
                "waiver": None,
                "events": [],
            }
        ],
        "history": [
            {
                "kind": "submission",
                "event_id": 5,
                "event_type": "submitted",
                "from_state": None,
                "to_state": None,
                "actor_display": "Ada Lovelace",
                "recorded_at": _AWARE,
                "object_ids": {"review_cycle_id": 11, "review_item_id": 7},
            }
        ],
        "allowed_actions": {
            "can_decide": True,
            "decision_denial_reason": None,
            "decision_outcomes": [
                "approved",
                "approved_with_conditions",
                "returned_for_evidence",
                "returned_for_options",
                "rejected",
            ],
            "conditions": {
                21: {
                    "can_capture_evidence": True,
                    "can_submit_evidence": True,
                    "can_verify": False,
                    "can_waive": True,
                    "capture_denial_reason": None,
                    "submit_denial_reason": None,
                    "verify_denial_reason": "This condition is not in a state that allows this action.",
                    "waive_denial_reason": None,
                }
            },
        },
        "command_keys": {
            "decision": "key-decision",
            "condition:21:capture": "key-capture",
            "condition:21:submit": "key-submit",
            "condition:21:waive": "key-waive",
        },
    }
    review.update(overrides)
    return review


def historical_review():
    return {
        "state": "historical_unverified",
        "reason": "arb_migration_snapshot_missing",
        "identity": _identity(subject_type="solution", successor_cycle_id=12),
        "subject": {
            "type": "solution",
            "label": "Solution",
            "icon": "layout-grid",
            "title": "Legacy CRM consolidation",
            "canonical_url": "/solutions/3?tab=governance",
        },
        "evidence": {
            "evidence_type": None,
            "evidence_id": None,
            "version": None,
            "schema_version": None,
            "policy_version": None,
            "captured_by_display": None,
            "captured_at": None,
            "content_hash": None,
            "hash_state": "unavailable",
            "sections": [],
            "legacy_source_type": "solution_arb_review",
            "legacy_source_id": 404,
            "migration_gap_reason": "arb_migration_snapshot_missing",
        },
        "decision": {
            "event": None,
            "projection": {
                "status": "historical_unverified",
                "terminal_outcome": "approved",
                "closed_at": _AWARE,
                "condition_projection_revision": 0,
                "review_status": "approved",
                "condition_count": 0,
                "blocking_condition_count": 0,
            },
            "recorded_historical_outcome": "approved",
        },
        "conditions": [],
        "history": [],
        "allowed_actions": _no_actions(),
        "command_keys": {},
    }


def failed_review():
    return {
        "state": "failed",
        "reason": "arb_review_read_failed",
        "identity": {
            "review_item_id": None,
            "review_cycle_id": None,
            "review_number": None,
            "cycle_number": None,
            "subject_type": None,
            "subject_id": None,
            "predecessor_cycle_id": None,
            "successor_cycle_id": None,
        },
        "subject": {"type": None, "label": None, "icon": None, "title": None, "canonical_url": None},
        "evidence": None,
        "decision": None,
        "conditions": [],
        "history": [],
        "allowed_actions": _no_actions(),
        "command_keys": {},
    }


def _filter_options():
    return {
        "state": [
            {"value": "open", "label": "Open"},
            {"value": "decided", "label": "Decided"},
            {"value": "historical", "label": "Historical"},
        ],
        "subject_type": [
            {"value": "decision_brief", "label": "Decision Brief"},
            {"value": "solution", "label": "Solution"},
            {"value": "architecture_model", "label": "Architecture Model"},
            {"value": "adr", "label": "ADR"},
        ],
    }


def available_queue():
    return {
        "state": "available",
        "reason": None,
        "filters": {"state": "open", "subject_type": None, "q": None, "page": 1},
        "filter_options": _filter_options(),
        "items": [
            {
                "review_item_id": 7,
                "review_cycle_id": 11,
                "review_number": "ARB-2026-0042",
                "cycle_number": 1,
                "subject_type": "decision_brief",
                "subject_id": 3,
                "subject_label": "Decision Brief",
                "subject_icon": "file-check",
                "subject_title": "Consolidate the three payroll systems",
                "canonical_url": "/solutions/programmes/1/workstreams/2/decision",
                "projection_status": "under_review",
                "opened_at": _AWARE,
                "submitted_at": _AWARE,
                # Deliberately absent: must render as an em dash, never blank.
                "submitter_display": None,
                "required_action_label": "Decision required",
                "required_action_anchor": "#decision",
                "is_historical_unverified": False,
            }
        ],
        "page": 1,
        "page_size": 25,
        "total_items": 1,
        "total_pages": 1,
    }


def failed_queue():
    return {
        "state": "failed",
        "reason": "arb_queue_read_failed",
        "filters": {"state": None, "subject_type": None, "q": None, "page": 1},
        "filter_options": _filter_options(),
        "items": [],
        "page": None,
        "page_size": None,
        "total_items": None,
        "total_pages": None,
    }


def empty_queue(**filters):
    applied = {"state": None, "subject_type": None, "q": None, "page": 1}
    applied.update(filters)
    return {
        "state": "empty",
        "reason": "arb_queue_empty",
        "filters": applied,
        "filter_options": _filter_options(),
        "items": [],
        "page": 1,
        "page_size": 25,
        "total_items": 0,
        "total_pages": 0,
    }


def _render(app, template, **context):
    with app.test_request_context("/arb/"):
        return render_template(template, **context)


def _render_review_body(app, review):
    return _render(
        app,
        {
            "available": "arb/partials/_typed_review_workspace.html",
            "historical_unverified": "arb/partials/_typed_review_historical.html",
            "legacy_generic": "arb/partials/_typed_review_legacy_generic.html",
            "failed": "arb/partials/_typed_review_failed.html",
        }[review["state"]],
        review=review,
        decision_action_url="/arb/reviews/7/decision",
    )


def _assert_no_raw_colours(body):
    found = sorted(set(_COLOUR_RE.findall(body)))
    assert not found, f"raw Tailwind colour families leaked into the markup: {found}"


# --------------------------------------------------------------------------
# One header / one breadcrumb / one <h1>
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "review_factory",
    [available_review, historical_review, failed_review],
    ids=["available", "historical_unverified", "failed"],
)
def test_typed_partials_render_no_header_breadcrumb_or_h1(app, review_factory):
    """The outer page owns the header. A partial that grows one duplicates it."""
    body = _render_review_body(app, review_factory())

    assert "<h1" not in body
    assert 'aria-label="Breadcrumb"' not in body
    assert "{% extends" not in body


def test_typed_queue_partial_renders_no_header_breadcrumb_or_h1(app):
    body = _render(app, "arb/partials/_typed_queue.html",
                   queue=available_queue(), queue_action_url="/arb/")

    assert "<h1" not in body
    assert 'aria-label="Breadcrumb"' not in body


def test_review_and_queue_templates_render_one_header_and_no_legacy_dispatch(app):
    """The templates must not render more than one wrapper/breadcrumb/<h1>, and
    the typed/legacy dispatch this once guarded has been removed entirely: the
    typed workspace and typed queue are now the only code path, so there is no
    second branch left to duplicate the header, and the legacy partials these
    templates used to fall through into no longer exist to be referenced."""
    source_review = app.jinja_env.loader.get_source(app.jinja_env, "arb/review_detail.html")[0]
    source_queue = app.jinja_env.loader.get_source(app.jinja_env, "arb/dashboard.html")[0]

    strip_comments = re.compile(r"\{#.*?#\}", re.DOTALL)
    for source in (source_review, source_queue):
        code = strip_comments.sub("", source)
        assert code.count("page_header(") == 1
        assert "{% if _typed %}" not in code and "{% if not _typed %}" not in code

    # The legacy dispatch and its partials are gone, not merely unreached.
    assert "arb/partials/_legacy_review_detail.html" not in source_review
    assert "arb/partials/_legacy_dashboard.html" not in source_queue


# --------------------------------------------------------------------------
# Semantic tokens only
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "review_factory",
    [available_review, historical_review, failed_review],
    ids=["available", "historical_unverified", "failed"],
)
def test_review_partials_use_semantic_tokens_only(app, review_factory):
    _assert_no_raw_colours(_render_review_body(app, review_factory()))


@pytest.mark.parametrize(
    "queue_factory", [available_queue, failed_queue, empty_queue],
    ids=["available", "failed", "empty"],
)
def test_queue_partial_uses_semantic_tokens_only(app, queue_factory):
    _assert_no_raw_colours(
        _render(app, "arb/partials/_typed_queue.html",
                queue=queue_factory(), queue_action_url="/arb/")
    )


def test_approved_with_conditions_is_never_rendered_green(app):
    """Execution is still blocked, so the conditional outcome must not carry the
    success token even though its text says approved."""
    review = available_review()
    review["decision"]["projection"]["status"] = "approved_with_conditions"
    review["decision"]["projection"]["blocking_condition_count"] = 1
    body = _render_review_body(app, review)

    conditional = re.findall(r'class="([^"]*)"[^>]*>\s*Approved with conditions', body)
    assert conditional, "the conditional outcome badge did not render"
    for classes in conditional:
        assert "bg-success" not in classes
        assert "bg-warning/10" in classes
    assert "Execution remains blocked" in body


def test_typed_decision_form_uses_the_server_adapter_field_name(app):
    """A native form submit must deliver the outcome the adapter consumes."""
    body = _render_review_body(app, available_review())

    assert 'name="decision" value="returned_for_evidence"' in body
    assert 'name="outcome"' not in body


# --------------------------------------------------------------------------
# A failed read shows no zeros
# --------------------------------------------------------------------------

def test_failed_queue_renders_alert_and_no_zero_counts(app):
    """A 0 here is indistinguishable from a measured zero. The read model
    returns None for every count on failure and the template must show none."""
    body = _render(app, "arb/partials/_typed_queue.html",
                   queue=failed_queue(), queue_action_url="/arb/")

    assert "The review ledger could not be read. Retry." in body
    assert 'role="alert"' in body
    assert "reviews" not in body.split("Reason:")[0].replace("ARB", "")  # no count line
    assert not re.search(r">\s*0\s*<", body)
    assert "Page" not in body
    # No queue rows and no filter form: nothing was read, so nothing is offered.
    assert "Open review" not in body


def test_failed_review_renders_alert_and_no_fabricated_detail(app):
    body = _render_review_body(app, failed_review())

    assert "This review could not be read. Retry." in body
    assert 'role="alert"' in body
    assert not re.search(r">\s*0\s*<", body)
    for control in ("Approve", "Reject", "Submit evidence", "Grant waiver", "Verify evidence"):
        assert control not in body


# --------------------------------------------------------------------------
# historical_unverified is a locked state
# --------------------------------------------------------------------------

def test_historical_unverified_renders_no_mutation_controls(app):
    body = _render_review_body(app, historical_review())

    assert "Historical review" in body
    assert "evidence snapshot could not be verified" in body
    assert "Legacy provenance" in body
    assert "arb_migration_snapshot_missing" in body
    assert "solution_arb_review" in body

    # The recorded legacy outcome text legitimately contains the word
    # "Approved", so match control *markup*, not prose.
    for control in (
        "Approve",
        "Approve with conditions",
        "Return for evidence",
        "Reject",
        "Submit evidence",
        "Verify evidence",
        "Grant waiver",
        "Retry submission",
        "Reopen",
    ):
        assert f">{control}" not in body, (
            f"{control!r} must not be offered on a locked historical cycle"
        )
    assert "<button" not in body

    assert "data-modal-open" not in body
    assert "<form" not in body


def test_historical_unverified_never_shows_a_verified_posture(app):
    """The legacy outcome text says "approved". It must not be rendered green
    and must not claim verification."""
    body = _render_review_body(app, historical_review())

    approved = re.findall(r'class="([^"]*)"[^>]*>\s*Approved', body)
    for classes in approved:
        assert "bg-success" not in classes
        assert "text-success" not in classes
    assert "Hash verified" not in body
    assert "Not verified here." in body


# --------------------------------------------------------------------------
# Absent values render as an em dash
# --------------------------------------------------------------------------

def test_absent_values_render_an_em_dash_not_blank_or_none(app):
    review = available_review()
    body = _render_review_body(app, review)

    assert "—" in body
    assert ">None<" not in body
    # `category` and `due_date` are both None on the seeded condition.
    assert body.count("—") >= 2


def test_absent_queue_submitter_renders_an_em_dash(app):
    body = _render(app, "arb/partials/_typed_queue.html",
                   queue=available_queue(), queue_action_url="/arb/")

    assert "—" in body
    assert ">None<" not in body


def test_missing_canonical_url_does_not_render_a_dead_link(app):
    """The read model legitimately returns None here for a decision brief."""
    review = available_review()
    review["subject"]["canonical_url"] = None
    body = _render_review_body(app, review)

    assert "Open subject" not in body
    assert "No canonical subject link is recorded" in body


# --------------------------------------------------------------------------
# Recorded event and current projection stay distinct
# --------------------------------------------------------------------------

def test_recorded_decision_and_projection_are_rendered_separately(app):
    body = _render_review_body(app, available_review())

    assert "Recorded decision" in body
    assert "Current projection" in body
    # The event says approved_with_conditions; the projection says approved.
    assert "Approved with conditions" in body
    assert body.index("Recorded decision") < body.index("Current projection")


# --------------------------------------------------------------------------
# Condition cards: no fabricated progress, and the capture/submit recovery path
# --------------------------------------------------------------------------

def test_conditions_show_a_count_not_a_percentage_progress_bar(app):
    body = _render_review_body(app, available_review())

    assert "0 of 1 resolved" in body
    assert "%" not in re.sub(r"\bhover:[^\s\"]+", "", body).split("Conditions")[-1][:600]


def test_condition_capture_failure_offers_resubmit_not_recapture(app):
    """Capture does not advance the condition. Re-running capture after a failed
    lifecycle submit silently creates a SECOND evidence record, so the recovery
    control must re-post the captured id to /submit."""
    body = _render_review_body(app, available_review())

    assert "Evidence captured, not submitted" in body
    assert "data-arb-captured-region" in body
    assert "data-arb-captured-evidence-id" in body
    assert 'data-submit-url-template="/arb/api/conditions/21/evidence/__EVIDENCE_ID__/submit"' in body
    assert "Retry submission" in body
    # The recovery region is inert until the JS reveals it, and uses `hidden`
    # rather than x-show so it is correct before Alpine hydrates.
    assert "x-show" not in body.split("data-arb-captured-region")[1].split("</div>")[0]


def test_condition_forms_send_no_server_derived_fields(app):
    """Lane 3 strictly allow-lists the bodies: any of these returns 400."""
    body = _render_review_body(app, available_review())

    for forbidden in (
        "organization_id",
        "content_hash",
        "source_checksum",
        "freshness_status",
        "freshness_rule_version",
        "condition_revision",
        "decided_by_id",
        "actor_id",
    ):
        assert f'name="{forbidden}"' not in body


def test_condition_controls_follow_allowed_actions_only(app):
    body = _render_review_body(app, available_review())

    # can_capture_evidence and can_waive are True; can_verify is False.
    assert "Submit evidence for condition 1" in body
    assert "Grant waiver for condition 1" in body
    assert "Verify evidence for condition 1" not in body


def test_unauthorised_reader_sees_the_denial_reason_and_no_decision_controls(app):
    review = available_review()
    review["allowed_actions"] = _no_actions()
    review["allowed_actions"]["decision_denial_reason"] = (
        "You submitted this review. A separate authorised decision maker must "
        "record the outcome."
    )
    review["command_keys"] = {}
    body = _render_review_body(app, review)

    assert "A separate authorised decision maker must record the outcome." in body
    assert 'data-modal-open="arb-decision-approved"' not in body
    assert 'name="outcome"' not in body


def test_return_for_options_is_offered_only_for_a_decision_brief(app):
    brief = _render_review_body(app, available_review())
    assert "Return for options" in brief

    solution = available_review()
    solution["identity"]["subject_type"] = "solution"
    solution["subject"]["label"] = "Solution"
    solution["allowed_actions"]["decision_outcomes"] = [
        "approved", "approved_with_conditions", "returned_for_evidence", "rejected",
    ]
    assert "Return for options" not in _render_review_body(app, solution)


# --------------------------------------------------------------------------
# Queue accessibility and empty states
# --------------------------------------------------------------------------

def test_queue_desktop_table_uses_real_column_headers(app):
    body = _render(app, "arb/partials/_typed_queue.html",
                   queue=available_queue(), queue_action_url="/arb/")

    assert body.count('<th scope="col"') == 6
    assert "<caption" in body


def test_queue_mobile_cards_repeat_the_labels(app):
    body = _render(app, "arb/partials/_typed_queue.html",
                   queue=available_queue(), queue_action_url="/arb/")

    mobile = body.split('md:hidden')[1]
    for label in ("Review", "Subject", "Required action", "Submitted"):
        assert label in mobile


def test_unfiltered_and_filtered_empty_states_differ(app):
    unfiltered = _render(app, "arb/partials/_typed_queue.html",
                         queue=empty_queue(), queue_action_url="/arb/")
    filtered = _render(app, "arb/partials/_typed_queue.html",
                       queue=empty_queue(q="payroll"), queue_action_url="/arb/")

    assert "No typed ARB reviews yet." in unfiltered
    assert "Clear filters" in unfiltered  # the filter row's own control
    assert "No reviews match these filters." in filtered
    assert "No typed ARB reviews yet." not in filtered


def test_queue_offers_no_generic_create_review_control(app):
    """A typed review begins from a real subject; it cannot be drafted here."""
    body = _render(app, "arb/partials/_typed_queue.html",
                   queue=empty_queue(), queue_action_url="/arb/")

    assert "New Review" not in body
    assert "create-arb-review" not in body


# --------------------------------------------------------------------------
# Mixed timestamp awareness must not raise
# --------------------------------------------------------------------------

def test_naive_and_aware_timestamps_both_render(app):
    """ARBSubmissionEvidenceSnapshot.captured_at is naive; the rest are aware.
    A template that localised or subtracted from `now` would raise here."""
    review = available_review()
    review["evidence"]["captured_at"] = _NAIVE
    review["decision"]["event"]["recorded_at"] = _AWARE + timedelta(days=1)
    body = _render_review_body(app, review)

    assert _NAIVE.isoformat() in body
    assert "19 Aug 2026" in body


# --------------------------------------------------------------------------
# Alpine / modal discipline
# --------------------------------------------------------------------------

def _tag_at(body, start):
    """Return the whole opening tag starting at `start`, respecting quotes.

    A naive scan to the next ">" ends the tag early on an expression like
    ``x-show="rows.length > 1"`` — the same bug scripts/check_ui_contract.py
    documents for its own button scanner, and it made this test report a
    violation that did not exist.
    """
    i = start + 1
    quote = None
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ">":
            return body[start:i + 1]
        i += 1
    raise AssertionError("unterminated tag")


def test_every_x_show_carries_x_cloak_and_modals_use_hidden(app):
    body = _render_review_body(app, available_review())

    seen_x_show = 0
    for match in re.finditer(r"\bx-show=", body):
        tag = _tag_at(body, body.rindex("<", 0, match.start()))
        seen_x_show += 1
        assert "x-cloak" in tag, f"x-show without x-cloak: {tag[:200]}"
    assert seen_x_show, "the fixture no longer exercises any x-show element"

    seen_modals = 0
    for match in re.finditer(r'<div\s+id="arb-[^"]+"', body):
        tag = _tag_at(body, match.start())
        seen_modals += 1
        assert "x-cloak" not in tag
        assert "hidden" in tag
    assert seen_modals, "the fixture no longer renders any modal root"


def test_no_hand_rolled_dialog_or_native_confirm(app):
    body = _render_review_body(app, available_review())

    assert "confirm(" not in body
    assert "alert(" not in body
    assert 'role="dialog"' in body  # the platform modal macro supplied it
    assert "onclick=" not in body


def test_icon_only_controls_have_specific_labels(app):
    body = _render_review_body(app, available_review())

    assert 'aria-label="Copy evidence hash"' in body
    assert "Remove" in body  # the repeatable condition row's remove control
    assert ':aria-label="\'Remove condition \' + (index + 1)"' in body


def test_nested_snapshot_sections_render_without_dumping_raw_json(app):
    """Real dossiers store mappings and lists (Solution `checks`/`artifacts`,
    Architecture Model citations). The renderer recurses through them, so a
    broken self-reference in the macro would 500 the whole review page."""
    review = available_review()
    review["evidence"]["sections"] = [
        {"key": "checks", "label": "Server checks",
         "value": {"archimate_valid": True, "blockers": ["missing owner", "stale citation"]}},
        {"key": "artifacts", "label": "Artefacts", "value": []},
        {"key": "warnings", "label": "Warnings", "value": None},
    ]
    body = _render_review_body(app, review)

    assert "Server checks" in body
    assert "missing owner" in body
    assert "stale citation" in body
    # An empty list and a null are both "not recorded", not a fabricated zero.
    assert body.count("—") >= 2
    assert "{'archimate_valid'" not in body  # not a raw Python dump
