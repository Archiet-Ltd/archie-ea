"""The AI chat analytics view counts only the caller's own organisation.

The audit log carries no organisation of its own (ownership is the user), so
the analytics queries join the user and filter on the caller's organisation.
Before that, an administrator of one organisation saw another organisation's
message counts and usage by domain.
"""

import uuid


def _admin(db_session, org):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"an-{uuid.uuid4().hex[:8]}@example.test",
        first_name="An",
        last_name="Admin",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    db_session.add(user)
    db_session.flush()
    return user


def test_analytics_never_counts_another_organisations_messages(
    app, db_session, make_org, client, login_as
):
    from app.models.ai_chat_audit_log import AIChatAuditLog, AuditEventType
    from app.models.user import User

    org_a, org_b = make_org("analytics-a"), make_org("analytics-b")
    admin_a, admin_b = _admin(db_session, org_a), _admin(db_session, org_b)
    event = list(AuditEventType)[0]
    for _ in range(3):
        db_session.add(AIChatAuditLog(
            event_type=event, user_id=admin_b.id, user_name="B",
            domain="other-organisation-domain", message="m",
        ))
    db_session.add(AIChatAuditLog(
        event_type=event, user_id=admin_a.id, user_name="A",
        domain="own-domain", message="m",
    ))
    db_session.commit()
    admin_a_id = admin_a.id
    db_session.expunge_all()

    login_as(client, db_session.get(User, admin_a_id))
    rule = next(r.rule for r in app.url_map.iter_rules() if r.endpoint.endswith("admin_analytics_data"))
    response = client.get(rule)
    body = response.get_json()

    assert response.status_code == 200
    assert body["total_messages"] == 1
    assert [d["domain"] for d in body["usage_by_domain"]] == ["own-domain"]
    assert "other-organisation-domain" not in response.get_data(as_text=True)
