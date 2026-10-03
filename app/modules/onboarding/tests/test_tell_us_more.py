"""Tell us more: the service layer (save/skip/resume) and the routes,
against the shared db_session fixture. See onboarding-redesign-v3 §3."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org):
    from app.models.user import Role, User

    # Full-page renders (the hub and each section extend layouts/base.html,
    # whose nav macro reads current_user.role) need a seeded Role row, the
    # same gap test_routes.py's own pre-existing full-page-render tests hit
    # on a freshly created schema. Role.insert_roles() is the app's own,
    # idempotent seeding path — reused here rather than hand-building a row.
    if Role.query.filter_by(default=True).first() is None:
        Role.insert_roles()

    user = User(
        email=f"tum-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Tell",
        last_name="UsMore",
        confirmed=True,
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _logged_in(db_session, make_org, client, login_as, name):
    org = make_org(name)
    user = _make_user(db_session, org)
    login_as(client, user)
    return org, user


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


def test_read_progress_is_empty_for_a_new_org(app, db_session, make_org):
    from app.modules.onboarding.services import tell_us_more

    org = make_org("tum-new")
    progress = tell_us_more.read_progress(org)

    assert progress == {"sections": {}, "answers": {}}


def test_save_section_persists_answers_and_marks_saved(app, db_session, make_org):
    from app.modules.onboarding.services import tell_us_more

    org = make_org("tum-save")
    tell_us_more.save_section(org, "how_you_work", {
        "frameworks_in_use": ["agile-methodology", "devops-practices", "not_a_real_framework"],
    })

    progress = tell_us_more.read_progress(org)
    assert progress["sections"]["how_you_work"]["status"] == "saved"
    assert progress["answers"]["how_you_work"]["frameworks_in_use"] == [
        "agile-methodology", "devops-practices",
    ], "an option outside the known list must be dropped, not stored"


def test_skip_section_marks_skipped_and_saves_no_answers(app, db_session, make_org):
    from app.modules.onboarding.services import tell_us_more

    org = make_org("tum-skip")
    tell_us_more.skip_section(org, "how_you_work")

    progress = tell_us_more.read_progress(org)
    assert progress["sections"]["how_you_work"]["status"] == "skipped"
    assert "how_you_work" not in progress["answers"]


def test_skip_never_downgrades_an_already_saved_section(app, db_session, make_org):
    from app.modules.onboarding.services import tell_us_more

    org = make_org("tum-no-downgrade")
    tell_us_more.save_section(org, "how_you_work", {"frameworks_in_use": ["agile-methodology"]})
    tell_us_more.skip_section(org, "how_you_work")

    progress = tell_us_more.read_progress(org)
    assert progress["sections"]["how_you_work"]["status"] == "saved"
    assert progress["answers"]["how_you_work"]["frameworks_in_use"] == ["agile-methodology"]


def test_section_statuses_lists_all_five_in_order(app, db_session, make_org):
    from app.modules.onboarding.services import tell_us_more

    org = make_org("tum-statuses")
    tell_us_more.save_section(org, "team", {"people": [{"name": "Jo", "does": "Sales"}]})

    statuses = tell_us_more.section_statuses(org)
    assert list(statuses.keys()) == [s["key"] for s in tell_us_more.SECTIONS]
    assert statuses["team"] == "saved"
    assert statuses["compliance"] == "not_started"


def test_clean_answers_caps_people_list_length_and_field_length(app, db_session, make_org):
    from app.modules.onboarding.services import tell_us_more

    long_name = "x" * 500
    many_people = [{"name": f"P{i}", "does": "role"} for i in range(50)]

    cleaned = tell_us_more.clean_answers("team", {"people": many_people})
    assert len(cleaned["people"]) == tell_us_more._MAX_PEOPLE

    cleaned_text = tell_us_more.clean_answers("whats_changing", {"transformation_other": long_name})
    assert len(cleaned_text["transformation_other"]) == tell_us_more._MAX_TEXT


def test_clean_answers_status_list_drops_unknown_standard_and_status(app, db_session, make_org):
    from app.modules.onboarding.services import tell_us_more

    cleaned = tell_us_more.clean_answers("compliance", {
        "standards": {"gdpr": "partial", "made_up_standard": "full", "iso27001": "not_a_status"},
    })
    assert cleaned["standards"] == {"gdpr": "partial"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_hub_lists_all_five_sections_with_unlock_lines(app, db_session, make_org, client, login_as):
    _logged_in(db_session, make_org, client, login_as, "hub")

    resp = client.get("/onboarding/tell-us-more")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Jinja's autoescape renders an apostrophe in text content as `&#39;`.
    for title in ("How you work", "What you must comply with", "What you&#39;re changing", "How you build", "Who&#39;s on the team"):
        assert title in html
    assert "Removes" in html and "Accountability" in html, "the team section's unlock line must render"


def test_unknown_section_key_is_a_404(app, db_session, make_org, client, login_as):
    _logged_in(db_session, make_org, client, login_as, "unknown-section")

    resp = client.get("/onboarding/tell-us-more/not-a-real-section")

    assert resp.status_code == 404


@pytest.mark.parametrize("section_key", [
    "how_you_work", "compliance", "whats_changing", "how_you_build", "team",
])
def test_every_section_renders(app, db_session, make_org, client, login_as, section_key):
    _logged_in(db_session, make_org, client, login_as, f"render-{section_key}")

    resp = client.get(f"/onboarding/tell-us-more/{section_key}")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "Save and continue" in resp.get_data(as_text=True)
    assert "Skip" in resp.get_data(as_text=True)


def test_save_and_continue_persists_and_returns_the_hub(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "save-continue")

    resp = client.post(
        "/onboarding/tell-us-more/how_you_work",
        json={"action": "save", "answers": {
            "frameworks_in_use": ["agile-methodology", "okr-objectives-key-results"],
        }},
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["data"]["next"].endswith("/onboarding/tell-us-more")

    from app.modules.onboarding.services import tell_us_more
    db_session.refresh(org)
    assert tell_us_more.answers_for(org, "how_you_work")["frameworks_in_use"] == [
        "agile-methodology", "okr-objectives-key-results",
    ]


def test_skip_advances_without_persisting_answers(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "skip-route")

    resp = client.post("/onboarding/tell-us-more/compliance", json={"action": "skip"})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    from app.modules.onboarding.services import tell_us_more
    db_session.refresh(org)
    assert tell_us_more.section_statuses(org)["compliance"] == "skipped"
    assert tell_us_more.answers_for(org, "compliance") == {}


def test_resuming_a_section_shows_the_previously_saved_answer(app, db_session, make_org, client, login_as):
    org, _ = _logged_in(db_session, make_org, client, login_as, "resume")

    client.post(
        "/onboarding/tell-us-more/team",
        json={"action": "save", "answers": {"people": [{"name": "Ada Lovelace", "does": "Engineering"}]}},
    )

    resp = client.get("/onboarding/tell-us-more/team")

    assert resp.status_code == 200
    assert "Ada Lovelace" in resp.get_data(as_text=True)


def test_invalid_action_is_a_400(app, db_session, make_org, client, login_as):
    _logged_in(db_session, make_org, client, login_as, "bad-action")

    resp = client.post("/onboarding/tell-us-more/how_you_work", json={"action": "bogus"})

    assert resp.status_code == 400


def test_gaps_screen_links_to_tell_us_more(app, db_session, make_org, client, login_as):
    _logged_in(db_session, make_org, client, login_as, "gaps-link")

    resp = client.get("/onboarding/gaps")

    assert resp.status_code == 200
    assert '/onboarding/tell-us-more' in resp.get_data(as_text=True)


def test_twin_screen_links_to_tell_us_more(app, db_session, make_org, client, login_as):
    _logged_in(db_session, make_org, client, login_as, "twin-link")

    resp = client.get("/onboarding/twin")

    assert resp.status_code == 200
    assert '/onboarding/tell-us-more' in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_another_organisation_cannot_read_a_saved_answer(app, db_session, make_org, client, login_as):
    org_a, _ = _logged_in(db_session, make_org, client, login_as, "tenant-a")
    client.post(
        "/onboarding/tell-us-more/team",
        json={"action": "save", "answers": {"people": [{"name": "Org A Secret Person", "does": "Founder"}]}},
    )

    # A second organisation, a second login on the same client.
    org_b, _ = _logged_in(db_session, make_org, client, login_as, "tenant-b")
    resp = client.get("/onboarding/tell-us-more/team")

    assert resp.status_code == 200
    assert "Org A Secret Person" not in resp.get_data(as_text=True)

    from app.modules.onboarding.services import tell_us_more
    assert tell_us_more.answers_for(org_b, "team") == {}


def test_another_organisation_cannot_write_over_the_first_organisations_answer(
    app, db_session, make_org, client, login_as
):
    org_a, _ = _logged_in(db_session, make_org, client, login_as, "tenant-write-a")
    client.post(
        "/onboarding/tell-us-more/how_you_work",
        json={"action": "save", "answers": {"frameworks_in_use": ["agile-methodology"]}},
    )

    org_b, _ = _logged_in(db_session, make_org, client, login_as, "tenant-write-b")
    client.post(
        "/onboarding/tell-us-more/how_you_work",
        json={"action": "save", "answers": {"frameworks_in_use": ["okr-objectives-key-results"]}},
    )

    from app.modules.onboarding.services import tell_us_more
    db_session.refresh(org_a)
    db_session.refresh(org_b)
    assert tell_us_more.answers_for(org_a, "how_you_work")["frameworks_in_use"] == [
        "agile-methodology",
    ], "organisation B's save must not have touched organisation A's row"
    assert tell_us_more.answers_for(org_b, "how_you_work")["frameworks_in_use"] == [
        "okr-objectives-key-results",
    ]
