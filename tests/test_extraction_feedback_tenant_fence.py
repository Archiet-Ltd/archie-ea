"""One organisation's learned extraction corrections must not reach another organisation.

``ExtractionFeedback`` had no organisation. The AI chat prompt builder took the ten most-applied
learned rules across every organisation and injected them into whoever was chatting, so a
correction such as ``Name: 'Acme Ledger' should be 'Finance Core'`` taught by one customer
appeared in another customer's prompt. The tenant is derived from the user who made the
correction; a row with no known user stays hidden and is reported, never guessed.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"XF {label} {suffix}", slug=f"xf-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:6]
    user = User(
        first_name="XF", last_name=f"User-{suffix}", email=f"xf-{suffix}@example.test",
        password="test-password-123", confirmed=True, organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _rule(db_session, org, user, marker, *, pattern):
    from app.modules.architecture.services.feedback_learning_service import ExtractionFeedback

    extra = {"organization_id": org.id} if hasattr(ExtractionFeedback, "organization_id") else {}
    row = ExtractionFeedback(
        user_id=user.id, pattern_key=pattern, correction_type="name", applied_count=5,
        learned_rule={"name_mapping": {"from": f"{marker}-old", "to": f"{marker}-new", "context": "ctx"}},
        **extra,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _in_org(app, org_id, fn):
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return fn()


def test_the_chat_prompt_gets_only_the_callers_organisations_learned_rules(app, db_session):
    from app.modules.ai_chat.services.portfolio_context import PortfolioContextBuilder

    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    _rule(db_session, org_a, _user(db_session, org_a), "ALPHA", pattern="name:x:a")
    _rule(db_session, org_b, _user(db_session, org_b), "BRAVO", pattern="name:x:b")
    db_session.commit()
    org_a_id = org_a.id
    db_session.expunge_all()

    rules = _in_org(app, org_a_id, lambda: PortfolioContextBuilder()._learned_rules_summary())

    text = " ".join(rules)
    assert "ALPHA-new" in text
    assert "BRAVO" not in text


def test_the_extraction_patterns_are_limited_to_the_callers_organisation(app, db_session):
    from app.modules.architecture.services.feedback_learning_service import FeedbackLearningService

    org_a, org_b = _org(db_session, "pa"), _org(db_session, "pb")
    _rule(db_session, org_a, _user(db_session, org_a), "ALPHA", pattern="name:node:zeta")
    _rule(db_session, org_b, _user(db_session, org_b), "BRAVO", pattern="name:node:zeta")
    db_session.commit()
    org_a_id = org_a.id
    db_session.expunge_all()
    elements = [{"type": "node", "name": "Zeta thing"}]

    patterns = _in_org(app, org_a_id, lambda: FeedbackLearningService()._get_relevant_patterns(elements))

    rules = " ".join(str(p["learned_rule"]) for p in patterns)
    assert "ALPHA" in rules
    assert "BRAVO" not in rules


def test_a_recorded_correction_is_stamped_with_the_callers_organisation(app, db_session):
    from app.modules.architecture.services.feedback_learning_service import (
        ExtractionFeedback,
        FeedbackLearningService,
    )

    org = _org(db_session, "stamp")
    user = _user(db_session, org)
    db_session.commit()
    org_id, user_id = org.id, user.id

    def record():
        return FeedbackLearningService().record_correction(
            original_element={"name": "Old", "type": "node"},
            corrected_element={"name": "New", "type": "node"},
            user_id=user_id,
        )

    feedback_id = _in_org(app, org_id, record)

    assert db_session.get(ExtractionFeedback, feedback_id).organization_id == org_id


def test_the_backfill_derives_the_tenant_from_the_user_and_never_guesses(app):
    """Runs the derivation against a scratch schema, so no real table is touched."""
    from sqlalchemy import text

    from app import db
    from app.commands import backfill_layer_tenancy as backfill

    assert "extraction_feedback" in backfill._PROVENANCE_ONLY
    schema = "tmp_xf_" + uuid.uuid4().hex[:8]
    with app.app_context():
        with db.engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text(f"CREATE SCHEMA {schema}"))
                conn.execute(text(f"SET LOCAL search_path TO {schema}"))
                conn.execute(text("CREATE TABLE users (id int, organization_id int)"))
                conn.execute(text("CREATE TABLE extraction_feedback (id int, user_id int, organization_id int)"))
                conn.execute(text("INSERT INTO users VALUES (1, 10), (2, NULL)"))
                conn.execute(text(
                    "INSERT INTO extraction_feedback VALUES (100, 1, NULL), (101, 2, NULL), (102, NULL, NULL), (103, 99, NULL)"
                ))

                conn.execute(text(backfill._DERIVABLE_ORG["extraction_feedback"]))

                rows = dict(conn.execute(text("SELECT id, organization_id FROM extraction_feedback")).all())
            finally:
                trans.rollback()

    assert rows == {100: 10, 101: None, 102: None, 103: None}
