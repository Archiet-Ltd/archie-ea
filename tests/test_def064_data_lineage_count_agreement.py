"""DEF-064, Capgemini dry-run: /architecture/data-architecture's "Flows"
tile counted DataLineage rows only, while /architecture/data-lineage's
"Recorded lineage edges" counts those same rows PLUS ArchiMateRelationship
edges between DataObjects (drawn via the composer/import, not through this
page) — two different definitions of "how much lineage exists" answering
differently. The dashboard now counts both, same as the lineage page.
"""

import re

import pytest


@pytest.mark.usefixtures("db_session")
def test_dashboard_flow_count_includes_relationship_edges(app, db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User

    org = make_org("def064-data-lineage")
    with tenant_ctx(org.id):
        obj_a = ArchiMateElement(name="ZZ-VERIFY Data Object A", type="DataObject", organization_id=org.id)
        obj_b = ArchiMateElement(name="ZZ-VERIFY Data Object B", type="DataObject", organization_id=org.id)
        db_session.add_all([obj_a, obj_b])
        db_session.commit()
        # A relationship edge between two DataObjects, drawn elsewhere (e.g.
        # the composer) -- never inserted via DataLineage.
        db_session.add(ArchiMateRelationship(type="Association", source_id=obj_a.id, target_id=obj_b.id))
        db_session.commit()

        user = User(email=f"def064-{org.id}@example.com", organization_id=org.id,
                    enterprise_role="enterprise_architect", confirmed=True)
        db_session.add(user)
        db_session.commit()

        from tests.test_ba_tenant_and_authz import _login
        c = app.test_client()
        with app.app_context():
            _login(c, user.id)

            lineage_resp = c.get("/architecture/data-lineage")
            assert lineage_resp.status_code == 200
            lineage_html = lineage_resp.get_data(as_text=True)
            m = re.search(
                r'data-testid="lineage-edge-count-value">\s*(\d+)', lineage_html
            )
            assert m is not None
            lineage_page_count = int(m.group(1))
            assert lineage_page_count >= 1

            # DataLineage rows = 0 here (only a bare ArchiMateRelationship
            # edge was created), so the dashboard's count must include that
            # edge to agree with the lineage page's count of 1.
            from app.models import DataLineage
            assert DataLineage.query.count() == 0

            dashboard_resp = c.get("/architecture/data-architecture")
            assert dashboard_resp.status_code == 200
            dashboard_html = dashboard_resp.get_data(as_text=True)
            idx = dashboard_html.find("Data Lineage Flows")
            assert idx != -1
            card_snippet = dashboard_html[idx:idx + 400]
            m2 = re.search(r'card-title"[^>]*>\s*(\d+)\s*<', card_snippet)
            assert m2 is not None, card_snippet
            dashboard_count = int(m2.group(1))
            # Before the fix this was 0 (DataLineage rows only); it must now
            # agree with the lineage page's fuller count.
            assert dashboard_count == lineage_page_count == 1
