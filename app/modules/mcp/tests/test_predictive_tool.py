import json

from app.models import User


def _make_user(db_session, org, email):
    from app.models.user import Role

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email,
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()
    return user


def _mcp_resource(app) -> str:
    base = (app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/mcp"


def _mint_oauth_token(client, db_session, org, user, login_as_fn, app=None) -> str:
    from flask import current_app
    from app.modules.oauth_provider.models import OAuthClient

    oauth_client = OAuthClient.register(
        client_name="MCP Test Client",
        redirect_uris="http://localhost/callback",
    )

    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    resource = _mcp_resource(app if app is not None else current_app)

    login_as_fn(client, user)

    resp = client.post(
        "/oauth/authorize",
        data={
            "client_id": oauth_client.client_id,
            "redirect_uri": "http://localhost/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp:read",
            "resource": resource,
            "decision": "allow",
        },
        follow_redirects=False,
    )
    location = resp.headers["Location"]
    import urllib.parse
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    auth_code = params["code"][0]

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "http://localhost/callback",
            "client_id": oauth_client.client_id,
            "code_verifier": verifier,
            "resource": resource,
        },
    )
    return resp.get_json()["access_token"]


def _clear_cached_identity():
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _mcp_call(client, token: str, tool_name: str, arguments: dict) -> dict:
    _clear_cached_identity()
    bearer_client = client.application.test_client()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = bearer_client.post(
        "/mcp",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.get_json()


def _mcp_tools_list(client, token: str) -> list:
    _clear_cached_identity()
    bearer_client = client.application.test_client()
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = bearer_client.post(
        "/mcp",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.get_json()["result"]["tools"]



def test_get_predictive_insights_tools_list(client, db_session, make_org, login_as):
    """get_predictive_insights appears in tools/list."""
    org = make_org("predictive")
    user = _make_user(db_session, org, "predictive@example.com")
    token = _mint_oauth_token(client, db_session, org, user, login_as)

    tools = _mcp_tools_list(client, token)
    tool_names = {t["name"] for t in tools}
    assert "get_predictive_insights" in tool_names



def test_get_predictive_insights_topic_financial(client, db_session, make_org, login_as):
    """get_predictive_insights with topic=financial returns ZOMBIE_PROJECT and COST_TO_SERVE."""
    org = make_org("predictive-financial")
    user = _make_user(db_session, org, "predictive-fin@example.com")

    # Create a zombie project
    from app.models.business_capabilities import BusinessCapability
    from app.models import ArchiMateElement
    from app.models.strategic import StrategicInitiative
    from app.models.cost_fact import CostFact
    from datetime import date, timedelta

    cap_element = ArchiMateElement(name="Cap", type="Capability", layer="strategy", organization_id=org.id)
    db_session.add(cap_element)
    db_session.flush()

    capability = BusinessCapability(name="Cap", organization_id=org.id, archimate_element_id=cap_element.id)
    db_session.add(capability)
    db_session.flush()

    wp = ArchiMateElement(name="WP", type="WorkPackage", layer="implementation", organization_id=org.id)
    db_session.add(wp)
    db_session.flush()
    from app.models import ArchiMateRelationship
    db_session.add(ArchiMateRelationship(source_id=wp.id, target_id=cap_element.id, type="Realization", organization_id=org.id))

    initiative = StrategicInitiative(
        name="Zombie Initiative",
        organization_id=org.id,
        target_completion_date=date.today() - timedelta(days=200),
        status="in_progress",
    )
    db_session.add(initiative)
    db_session.flush()

    from app.models.business_case import BusinessCase
    bc = BusinessCase(
        title="Zombie BC",
        organization_id=org.id,
        capability_id=capability.id,
        strategic_initiative_id=initiative.id,
        financial_benefit_annual=1000,
    )
    db_session.add(bc)
    db_session.flush()

    app = ArchiMateElement(name="App", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(app)
    db_session.flush()
    db_session.add(CostFact(
        organization_id=org.id, element_id=app.id, element_type="application",
        amount=2000, period_start=date.today() - timedelta(days=365), period_end=date.today(),
        kind="actual", currency="EUR", source="test"
    ))

    # Create cost to serve scenario
    svc = ArchiMateElement(name="Svc", type="ApplicationService", layer="application", organization_id=org.id)
    db_session.add(svc)
    db_session.flush()
    app2 = ArchiMateElement(name="App2", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(app2)
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=app2.id, target_id=svc.id, type="Realization", organization_id=org.id))
    db_session.add(CostFact(
        organization_id=org.id, element_id=app2.id, element_type="application",
        amount=100000, period_start=date.today() - timedelta(days=730), period_end=date.today() - timedelta(days=365),
        kind="actual", currency="EUR", source="test-period1"
    ))
    db_session.add(CostFact(
        organization_id=org.id, element_id=app2.id, element_type="application",
        amount=120000, period_start=date.today() - timedelta(days=365), period_end=date.today(),
        kind="actual", currency="EUR", source="test-period2"
    ))

    db_session.commit()

    token = _mint_oauth_token(client, db_session, org, user, login_as)
    result = _mcp_call(client, token, "get_predictive_insights", {"topic": "financial"})

    assert "error" not in result, result
    content = json.loads(result["result"]["content"][0]["text"])
    alerts = content["alerts"]
    types = {a["prediction_type"] for a in alerts}
    assert types == {"ZOMBIE_PROJECT", "COST_TO_SERVE"}



def test_get_predictive_insights_topic_hr(client, db_session, make_org, login_as):
    """get_predictive_insights with topic=hr returns BUS_FACTOR."""
    org = make_org("predictive-hr")
    user = _make_user(db_session, org, "predictive-hr@example.com")

    for i in range(6):
        from app.models import ArchiMateElement
        from app.models.application_portfolio import ApplicationComponent
        from app.models.application_owner import ApplicationOwner

        app_elem = ArchiMateElement(name=f"App {i}", type="ApplicationComponent", layer="application", organization_id=org.id)
        db_session.add(app_elem)
        db_session.flush()
        ac = ApplicationComponent(name=f"App {i}", organization_id=org.id, archimate_element_id=app_elem.id, criticality="critical")
        db_session.add(ac)
        db_session.flush()
        db_session.add(ApplicationOwner(
            organization_id=org.id, user_id=user.id, element_type="application", element_id=app_elem.id, ownership_type="primary", application_id=ac.id
        ))

    db_session.commit()

    token = _mint_oauth_token(client, db_session, org, user, login_as)
    result = _mcp_call(client, token, "get_predictive_insights", {"topic": "hr"})

    assert "error" not in result, result
    content = json.loads(result["result"]["content"][0]["text"])
    alerts = content["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["prediction_type"] == "BUS_FACTOR"



def test_get_predictive_insights_topic_security(client, db_session, make_org, login_as):
    """get_predictive_insights with topic=security returns DOMINO_EFFECT."""
    org = make_org("predictive-security")
    user = _make_user(db_session, org, "predictive-sec@example.com")

    from app.models import ArchiMateElement, ArchiMateRelationship

    internet_iface = ArchiMateElement(name="API", type="ApplicationInterface", layer="application", organization_id=org.id)
    internet_iface.acm_properties = {"exposure": "internet"}
    db_session.add(internet_iface)
    data_obj = ArchiMateElement(name="PII Data", type="DataObject", layer="application", organization_id=org.id)
    data_obj.acm_properties = {"classification": "personal data"}
    db_session.add(data_obj)
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=internet_iface.id, target_id=data_obj.id, type="Flow", organization_id=org.id))
    db_session.commit()

    token = _mint_oauth_token(client, db_session, org, user, login_as)
    result = _mcp_call(client, token, "get_predictive_insights", {"topic": "security"})

    assert "error" not in result, result
    content = json.loads(result["result"]["content"][0]["text"])
    alerts = content["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["prediction_type"] == "DOMINO_EFFECT"



def test_get_predictive_insights_topic_none(client, db_session, make_org, login_as):
    """get_predictive_insights with no topic returns top 5 by severity."""
    org = make_org("predictive-none")
    user = _make_user(db_session, org, "predictive-none@example.com")

    # Create one of each check type
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.business_capabilities import BusinessCapability
    from app.models.strategic import StrategicInitiative
    from app.models.business_case import BusinessCase
    from app.models.cost_fact import CostFact
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_owner import ApplicationOwner
    from datetime import date, timedelta

    # 1. Unfunded objective
    goal = ArchiMateElement(name="Goal", type="Goal", layer="motivation", organization_id=org.id)
    db_session.add(goal)

    # 2. Zombie project
    cap_element = ArchiMateElement(name="Cap", type="Capability", layer="strategy", organization_id=org.id)
    db_session.add(cap_element)
    db_session.flush()
    capability = BusinessCapability(name="Cap", organization_id=org.id, archimate_element_id=cap_element.id)
    db_session.add(capability)
    db_session.flush()
    wp = ArchiMateElement(name="WP", type="WorkPackage", layer="implementation", organization_id=org.id)
    db_session.add(wp)
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=wp.id, target_id=cap_element.id, type="Realization", organization_id=org.id))
    initiative = StrategicInitiative(name="Zombie Initiative", organization_id=org.id, target_completion_date=date.today() - timedelta(days=200), status="in_progress")
    db_session.add(initiative)
    db_session.flush()
    bc = BusinessCase(title="Zombie", organization_id=org.id, capability_id=capability.id, strategic_initiative_id=initiative.id, financial_benefit_annual=1000)
    db_session.add(bc)
    db_session.flush()
    app = ArchiMateElement(name="App", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(app)
    db_session.flush()
    db_session.add(CostFact(organization_id=org.id, element_id=app.id, element_type="application", amount=2000, period_start=date.today() - timedelta(days=365), period_end=date.today(), kind="actual", currency="EUR", source="test"))

    # 3. Bus factor
    for i in range(6):
        a_elem = ArchiMateElement(name=f"App{i}", type="ApplicationComponent", layer="application", organization_id=org.id)
        db_session.add(a_elem)
        db_session.flush()
        ac = ApplicationComponent(name=f"App{i}", organization_id=org.id, archimate_element_id=a_elem.id, criticality="critical")
        db_session.add(ac)
        db_session.flush()
        db_session.add(ApplicationOwner(organization_id=org.id, user_id=user.id, element_type="application", element_id=a_elem.id, ownership_type="primary", application_id=ac.id))

    # 4. Domino effect
    iface = ArchiMateElement(name="Iface", type="ApplicationInterface", layer="application", organization_id=org.id)
    iface.acm_properties = {"exposure": "internet"}
    db_session.add(iface)
    data = ArchiMateElement(name="Data", type="DataObject", layer="application", organization_id=org.id)
    data.acm_properties = {"classification": "personal data"}
    db_session.add(data)
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=iface.id, target_id=data.id, type="Flow", organization_id=org.id))

    # 5. Cost to serve
    svc = ArchiMateElement(name="Svc", type="ApplicationService", layer="application", organization_id=org.id)
    db_session.add(svc)
    db_session.flush()
    app2 = ArchiMateElement(name="App2", type="ApplicationComponent", layer="application", organization_id=org.id)
    db_session.add(app2)
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=app2.id, target_id=svc.id, type="Realization", organization_id=org.id))
    db_session.add(CostFact(organization_id=org.id, element_id=app2.id, element_type="application", amount=100000, period_start=date.today() - timedelta(days=730), period_end=date.today() - timedelta(days=365), kind="actual", currency="EUR", source="test-period1"))
    db_session.add(CostFact(organization_id=org.id, element_id=app2.id, element_type="application", amount=120000, period_start=date.today() - timedelta(days=365), period_end=date.today(), kind="actual", currency="EUR", source="test-period2"))

    db_session.commit()

    token = _mint_oauth_token(client, db_session, org, user, login_as)
    result = _mcp_call(client, token, "get_predictive_insights", {})

    assert "error" not in result, result
    content = json.loads(result["result"]["content"][0]["text"])
    alerts = content["alerts"]
    assert len(alerts) <= 5
    # Should have HIGH severity first (ZOMBIE_PROJECT, DOMINO_EFFECT)
    high_alerts = [a for a in alerts if a["severity"] == "HIGH"]
    assert len(high_alerts) >= 2



def test_get_predictive_insights_unknown_topic(client, db_session, make_org, login_as):
    """get_predictive_insights with unknown topic returns error."""
    org = make_org("predictive-unknown")
    user = _make_user(db_session, org, "predictive-unk@example.com")
    db_session.commit()

    token = _mint_oauth_token(client, db_session, org, user, login_as)
    result = _mcp_call(client, token, "get_predictive_insights", {"topic": "invalid"})

    assert "error" not in result, result
    content = json.loads(result["result"]["content"][0]["text"])
    assert content["success"] is False
    assert "Unknown topic" in content["error"]



def test_get_predictive_insights_organisation_isolation(client, db_session, make_org, login_as):
    """Organisation A's alerts never include organisation B's elements."""
    org_a = make_org("iso-a")
    org_b = make_org("iso-b")
    user_a = _make_user(db_session, org_a, "usera@example.com")
    user_b = _make_user(db_session, org_b, "userb@example.com")

    # Create zombie project in org B
    from app.models.business_capabilities import BusinessCapability
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.strategic import StrategicInitiative
    from app.models.business_case import BusinessCase
    from app.models.cost_fact import CostFact
    from datetime import date, timedelta

    cap_element_b = ArchiMateElement(name="Cap B", type="Capability", layer="strategy", organization_id=org_b.id)
    db_session.add(cap_element_b)
    db_session.flush()
    cap_b = BusinessCapability(name="Cap B", organization_id=org_b.id, archimate_element_id=cap_element_b.id)
    db_session.add(cap_b)
    db_session.flush()
    wp_b = ArchiMateElement(name="WP B", type="WorkPackage", layer="implementation", organization_id=org_b.id)
    db_session.add(wp_b)
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=wp_b.id, target_id=cap_element_b.id, type="Realization", organization_id=org_b.id))
    initiative_b = StrategicInitiative(name="Zombie Initiative B", organization_id=org_b.id, target_completion_date=date.today() - timedelta(days=200), status="in_progress")
    db_session.add(initiative_b)
    db_session.flush()
    bc_b = BusinessCase(title="Zombie B", organization_id=org_b.id, capability_id=cap_b.id, strategic_initiative_id=initiative_b.id, financial_benefit_annual=1000)
    db_session.add(bc_b)
    db_session.flush()
    app_b = ArchiMateElement(name="App B", type="ApplicationComponent", layer="application", organization_id=org_b.id)
    db_session.add(app_b)
    db_session.flush()
    db_session.add(CostFact(organization_id=org_b.id, element_id=app_b.id, element_type="application", amount=2000, period_start=date.today() - timedelta(days=365), period_end=date.today(), kind="actual", currency="EUR", source="test"))

    # Create healthy project in org A
    cap_element_a = ArchiMateElement(name="Cap A", type="Capability", layer="strategy", organization_id=org_a.id)
    db_session.add(cap_element_a)
    db_session.flush()
    cap_a = BusinessCapability(name="Cap A", organization_id=org_a.id, archimate_element_id=cap_element_a.id)
    db_session.add(cap_a)
    db_session.flush()
    wp_a = ArchiMateElement(name="WP A", type="WorkPackage", layer="implementation", organization_id=org_a.id)
    db_session.add(wp_a)
    db_session.flush()
    db_session.add(ArchiMateRelationship(source_id=wp_a.id, target_id=cap_element_a.id, type="Realization", organization_id=org_a.id))
    initiative_a = StrategicInitiative(name="Healthy Initiative A", organization_id=org_a.id, target_completion_date=date.today() + timedelta(days=180), status="in_progress")
    db_session.add(initiative_a)
    db_session.flush()
    bc_a = BusinessCase(title="Healthy A", organization_id=org_a.id, capability_id=cap_a.id, strategic_initiative_id=initiative_a.id, financial_benefit_annual=5000)
    db_session.add(bc_a)
    db_session.flush()
    app_a = ArchiMateElement(name="App A", type="ApplicationComponent", layer="application", organization_id=org_a.id)
    db_session.add(app_a)
    db_session.flush()
    db_session.add(CostFact(organization_id=org_a.id, element_id=app_a.id, element_type="application", amount=1000, period_start=date.today() - timedelta(days=365), period_end=date.today(), kind="actual", currency="EUR", source="test"))

    db_session.commit()

    # Run checks for org A
    token_a = _mint_oauth_token(client, db_session, org_a, user_a, login_as)
    result_a = _mcp_call(client, token_a, "get_predictive_insights", {"topic": "financial"})
    content_a = json.loads(result_a["result"]["content"][0]["text"])
    alerts_a = content_a["alerts"]
    zombie_alerts = [a for a in alerts_a if a["prediction_type"] == "ZOMBIE_PROJECT"]
    assert len(zombie_alerts) == 0

    # Run checks for org B
    token_b = _mint_oauth_token(client, db_session, org_b, user_b, login_as)
    result_b = _mcp_call(client, token_b, "get_predictive_insights", {"topic": "financial"})
    content_b = json.loads(result_b["result"]["content"][0]["text"])
    alerts_b = content_b["alerts"]
    zombie_alerts_b = [a for a in alerts_b if a["prediction_type"] == "ZOMBIE_PROJECT"]
    assert len(zombie_alerts_b) == 1
    assert "Zombie B" in zombie_alerts_b[0]["target_element"]
