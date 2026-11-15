"""The kind of an application record: the rule, the one helper every count reads, and the plain names."""
import subprocess
import uuid
from collections import Counter

from app import db
from app.models import ArchiMateRelationship
from app.models.application_portfolio import ApplicationComponent
from app.services.application_kind import is_application_expression, kind_of, parts_of


def _row(org, name, **fields):
    row = ApplicationComponent(name=name, organization_id=org.id, **fields)
    db.session.add(row)
    db.session.flush()
    return row


def test_no_two_element_types_share_a_plain_language_name():
    from app.models.archimate_element_types import PLAIN_LANGUAGE_NAMES

    shared = [name for name, count in Counter(PLAIN_LANGUAGE_NAMES.values()).items() if count > 1]
    assert shared == []
    assert PLAIN_LANGUAGE_NAMES["Application"] == "Application"
    assert PLAIN_LANGUAGE_NAMES["ApplicationComponent"] == "Application component"


def test_the_rule_settles_parts_repositories_and_a_persons_choice(db_session, make_org, tenant_ctx):
    org = make_org("kind-rule")
    whole = _row(org, "Alerts gateway and consumers")
    part = _row(org, "Alerts Gateway")
    aggregated = _row(org, "Alerts Consumers")
    repo = _row(org, "billing-repo", repository_type="GitHub")
    typed_repo = _row(org, "archiet-go", component_type="repository")
    billing = _row(org, "Billing")
    chosen = _row(org, "Chosen", application_kind="application", repository_type="GitHub")
    for kind, target in (("composition", part), ("aggregation", aggregated)):
        db_session.add(ArchiMateRelationship(type=kind, source_id=whole.archimate_element_id,
                                             target_id=target.archimate_element_id, organization_id=org.id))
    db_session.add(ArchiMateRelationship(type="serving", source_id=billing.archimate_element_id,
                                         target_id=whole.archimate_element_id, organization_id=org.id))
    db_session.flush()
    with tenant_ctx(org.id):
        kinds = {row.name: kind_of(row)["kind"] for row in (whole, part, aggregated, repo, typed_repo, billing, chosen)}
        listed = db.session.execute(
            db.select(ApplicationComponent.name).where(
                ApplicationComponent.organization_id == org.id, is_application_expression(ApplicationComponent))
        ).scalars().all()
        assert kinds == {"Alerts gateway and consumers": "application", "Alerts Gateway": "part",
                         "Alerts Consumers": "part", "billing-repo": "not_software", "archiet-go": "not_software",
                         "Billing": "application", "Chosen": "application"}
        assert sorted(listed) == ["Alerts gateway and consumers", "Billing", "Chosen"]
        assert [name for _, name in parts_of(whole)] == ["Alerts Consumers", "Alerts Gateway"]
        assert kind_of(part)["reason"] == "Part of Alerts gateway and consumers"


# Every place that counts applications or owner coverage: each must read the one helper.
COUNTING_FILES = (
    "app/modules/dashboard/services/overview_figures.py",
    "app/modules/applications/routes/list_views.py",
    "app/modules/applications/routes/coverage_routes.py",
    "app/modules/dashboard/routes/dashboard_views.py",
    "app/modules/intelligence/services/query_catalogue.py",
)


def test_every_count_of_applications_and_owner_coverage_reads_the_one_helper():
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
    for path in COUNTING_FILES:
        text = open("%s/%s" % (root, path)).read()
        assert "is_application_expression" in text or "_active_applications" in text, path
    figures = open("%s/app/modules/dashboard/services/overview_figures.py" % root).read()
    assert "is_application_expression(A)" in figures.split("def _active_applications")[1].split("def left_out")[0]
