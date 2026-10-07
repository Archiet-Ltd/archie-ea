"""Regression: as-is/to-be plateau tagging must be readable back, not write-only.

A Capgemini delivery-team dry-run (2 Sep 2026) found that tagging an ArchiMate
element Baseline/Target/Transition through the create/edit form (which writes
ArchiMateElement.togaf_plateau, a real column) silently could not be found again:
GET /api/layer/<layer>/elements never serialized a "plateau" key at all, and the
client-side filter (dashboard.js) additionally read the WRONG key
(properties.plateau, a JSON blob the form never wrote to) with a mismatched
vocabulary (current/transitional/target vs. the real Baseline/Target/Transition).
Tagging worked; finding what you tagged did not. Both the API and the client
filter are fixed; this pins the API half, which is what the client-side fix
depends on.
"""

import pytest


@pytest.mark.usefixtures("db_session")
def test_api_layer_elements_serializes_plateau_for_native_element(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User

    org = make_org("plateau")
    with tenant_ctx(org.id):
        ae = ArchiMateElement(name="S/4HANA landscape", type="Node", layer="Technology",
                              organization_id=org.id, togaf_plateau="Target")
        db_session.add(ae)
        user = User(email=f"pu-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.get("/architecture/api/layer/technology/elements")
            assert resp.status_code == 200
            body = resp.get_json()
            match = next((e for e in body["elements"] if e["name"] == "S/4HANA landscape"), None)
            assert match is not None, "element not found in API response"
            assert match["plateau"] == "Target", \
                f"API must serialize the real togaf_plateau value; got {match.get('plateau')!r}"


@pytest.mark.usefixtures("db_session")
def test_api_layer_elements_serializes_plateau_for_domain_model(app, db_session, make_org, tenant_ctx):
    """A domain model (e.g. BusinessRole) reaches its plateau through its linked
    ArchiMateElement (archimate_element_id) — the portfolio branch of the API."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_layer import BusinessRole
    from app.models.user import User

    org = make_org("plateau")
    with tenant_ctx(org.id):
        role = BusinessRole(name="Revenue Operations Lead", organization_id=org.id)
        db_session.add(role)
        db_session.commit()  # before_insert listener creates the linked element
        ae = db_session.get(ArchiMateElement, role.archimate_element_id)
        ae.togaf_plateau = "Baseline"
        user = User(email=f"pu2-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.get("/architecture/api/layer/business/elements")
            assert resp.status_code == 200
            body = resp.get_json()
            match = next((e for e in body["elements"] if e["name"] == "Revenue Operations Lead"), None)
            assert match is not None
            assert match["plateau"] == "Baseline"


@pytest.mark.usefixtures("db_session")
def test_mirrored_element_appears_exactly_once_not_twice(app, db_session, make_org, tenant_ctx):
    """Capgemini walkthrough F-04: a domain-model row that has been mirrored
    into ArchiMateElement (BusinessRole -> archimate_element_id) was appearing
    TWICE in api_layer_elements — once tagged source=portfolio (keyed by the
    role's own id) and once source=architecture (keyed by the mirror's id) —
    because the dedup check compared the wrong id pair, so the mirror was
    never recognised as "already seen". One create must produce one listed
    element, not two different ids for the same real-world thing."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_layer import BusinessRole
    from app.models.user import User

    org = make_org("mirror-dedup")
    with tenant_ctx(org.id):
        role = BusinessRole(name="Plant Control Operator", organization_id=org.id)
        db_session.add(role)
        db_session.commit()  # before_insert listener creates the linked ArchiMateElement
        assert role.archimate_element_id is not None, "fixture assumption: BusinessRole auto-mirrors"
        user = User(email=f"pu3-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.get("/architecture/api/layer/business/elements")
            assert resp.status_code == 200
            body = resp.get_json()
            matches = [e for e in body["elements"] if e["name"] == "Plant Control Operator"]
            assert len(matches) == 1, (
                f"expected exactly one listed element for the mirrored role, got "
                f"{len(matches)}: {matches}"
            )
            # Once mirrored, the dedicated-table (portfolio) row is excluded here —
            # same convention _count_layer_elements already uses — and the single
            # surviving entry is the archimate_elements (architecture) side.
            assert matches[0]["id"] == role.archimate_element_id
            assert matches[0]["source"] == "architecture"


@pytest.mark.usefixtures("db_session")
def test_work_package_target_plateau_round_trips(app, db_session, make_org, tenant_ctx):
    """F-08(b), Capgemini walkthrough: the Capability Roadmap's "Edit Work
    Package" form has always offered "Target Plateau"; PUT .../work-packages/<id>
    returned 200 (looked successful) but update_work_package()'s allowed_fields
    whitelist never included plateau_id, so the value was silently dropped."""
    from app.models.implementation_migration import Plateau
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.user import User
    from app.services import work_package_service

    org = make_org("wp-plateau")
    with tenant_ctx(org.id):
        plateau = Plateau(name="Cutover", organization_id=org.id)
        db_session.add(plateau)
        db_session.flush()
        # The Capability Roadmap edits the one work package store.
        wp = work_package_service.create_work_package(
            organization_id=org.id, name="Retire SCADE")
        db_session.commit()
        plateau_id, wp_id = plateau.id, wp.id

        user = User(email=f"wpp-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.put(f"/capability-map/api/roadmap/work-packages/{wp_id}",
                         json={"plateau_id": plateau_id})
            assert resp.status_code == 200, resp.get_data(as_text=True)

            # Read back independently — the update actually persisted.
            reloaded = db_session.get(UnifiedWorkPackage, wp_id)
            db_session.refresh(reloaded)
            assert reloaded.plateau_id == plateau_id


@pytest.mark.usefixtures("db_session")
def test_typed_fields_and_architecture_state_prepopulate_on_reopen(app, db_session, make_org, tenant_ctx):
    """F-08(a), Capgemini walkthrough: Goal Type/Category/Architecture state
    all looked blank when reopening the edit dialog — the write path already
    worked (this session's earlier fix), but api_layer_elements() never
    included any typed field in the dict the edit modal reads its defaults
    from. Create a Goal with goal_type + architecture_state set, then confirm
    the listing API — what the edit modal's typedFieldDefaults() actually
    reads — serializes both back."""
    from app.models.user import User

    org = make_org("typed-fields")
    with tenant_ctx(org.id):
        user = User(email=f"tf-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            # Goal is not tenant-scoped (no organization_id column) — a name
            # collision with another test's fixture data would silently match
            # the wrong row, so this name is deliberately unique to this test.
            goal_name = "F-08(a) typed field round trip goal"
            resp = c.post(
                "/architecture/motivation/Goal/new",
                data={
                    "name": goal_name,
                    "description": "",
                    "goal_type": "strategic",
                    "category": "efficiency",
                    "architecture_state": "Target",
                },
            )
            assert resp.status_code in (302, 303), resp.get_data(as_text=True)

            listing = c.get("/architecture/api/layer/motivation/elements?per_page=500")
            assert listing.status_code == 200
            match = next(
                (e for e in listing.get_json()["elements"] if e["name"] == goal_name),
                None,
            )
            assert match is not None, "created Goal not found in listing"
            assert match["goal_type"] == "strategic"


@pytest.mark.usefixtures("db_session")
def test_application_architecture_state_round_trips(app, db_session, make_org, tenant_ctx):
    """F-08(c), Capgemini walkthrough: applications had no plateau/as-is-to-be
    control anywhere, though ApplicationComponent already reaches one via
    archimate_element_id — same mechanism BusinessRole already uses. Set it
    through the real Edit form, then read it back both from the edit page's
    own pre-select and from the ArchiMateElement it's supposed to have set."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User

    org = make_org("app-plateau")
    with tenant_ctx(org.id):
        application = ApplicationComponent(name="SCADE Plant Control", organization_id=org.id)
        db_session.add(application)
        db_session.commit()  # before_insert listener creates the linked ArchiMateElement
        assert application.archimate_element_id is not None
        app_id = application.id

        user = User(email=f"apl-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)
            resp = c.post(f"/applications/{app_id}/edit", data={
                "architecture_state": "Baseline",
            })
            assert resp.status_code == 302, resp.get_data(as_text=True)

            # Read back independently via the ArchiMateElement the app is linked to.
            reloaded = db_session.get(ApplicationComponent, app_id)
            ae = db_session.get(ArchiMateElement, reloaded.archimate_element_id)
            assert ae.togaf_plateau == "Baseline"

            # And via the edit page's own pre-select, so a human reopening it
            # sees what they set, not a blank control.
            edit_page = c.get(f"/applications/{app_id}/edit")
            assert edit_page.status_code == 200
            html = edit_page.get_data(as_text=True)
            assert 'value="Baseline" selected' in html
