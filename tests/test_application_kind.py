"""The kind of an application record: the rule, the one helper every count reads, and the plain names."""
import subprocess
import uuid
from collections import Counter

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
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
    promoted = _row(org, "archiet-api", component_type="repository", legacy_application_owner="Dana Reed")
    for imported in (typed_repo, promoted):
        db_session.get(ArchiMateElement, imported.archimate_element_id).source_kind = "connector"
    billing = _row(org, "Billing")
    chosen = _row(org, "Chosen", application_kind="application", repository_type="GitHub")
    by_hand = _row(org, "typed-in-repo", component_type="repository")
    by_hand_chosen = _row(org, "typed-in-chosen", component_type="repository", application_kind="application")
    for kind, target in (("composition", part), ("aggregation", aggregated)):
        db_session.add(ArchiMateRelationship(type=kind, source_id=whole.archimate_element_id,
                                             target_id=target.archimate_element_id, organization_id=org.id))
    db_session.add(ArchiMateRelationship(type="serving", source_id=billing.archimate_element_id,
                                         target_id=whole.archimate_element_id, organization_id=org.id))
    db_session.flush()
    with tenant_ctx(org.id):
        kinds = {row.name: kind_of(row)["kind"] for row in (whole, part, aggregated, repo, typed_repo, promoted, billing, chosen,
                                                                  by_hand, by_hand_chosen)}
        listed = db.session.execute(
            db.select(ApplicationComponent.name).where(
                ApplicationComponent.organization_id == org.id, is_application_expression(ApplicationComponent))
        ).scalars().all()
        assert kinds == {"Alerts gateway and consumers": "application", "Alerts Gateway": "part",
                         "Alerts Consumers": "part", "billing-repo": "not_software", "archiet-go": "not_software",
                         "archiet-api": "application", "Billing": "application", "Chosen": "application",
                         "typed-in-repo": "not_software", "typed-in-chosen": "application"}
        assert sorted(listed) == ["Alerts gateway and consumers", "Billing", "Chosen", "archiet-api",
                                  "typed-in-chosen"]
        assert [name for _, name in parts_of(whole)] == ["Alerts Consumers", "Alerts Gateway"]
        assert kind_of(part)["reason"] == "Part of Alerts gateway and consumers"


def test_a_row_with_no_stored_kind_is_read_by_the_rule_never_blank(db_session, make_org, tenant_ctx):
    org = make_org("kind-null")
    billing = _row(org, "Billing")
    repo = _row(org, "billing-repo", repository_type="GitHub")
    db_session.flush()
    db_session.execute(
        db.text(
            "UPDATE application_components SET application_kind = NULL, application_kind_set_by_id = NULL,"
            " application_kind_set_at = NULL WHERE id IN (:a, :b)"
        ),
        {"a": billing.id, "b": repo.id},
    )
    db_session.expire_all()
    with tenant_ctx(org.id):
        for row, kind in ((billing, "application"), (repo, "not_software")):
            found = kind_of(row)
            assert row.application_kind is None
            assert found["kind"] == kind
            assert found["label"] and found["reason"]


# A count or list of applications that is not narrowed by the one helper counts parts and repositories as
# applications. Each exception below says why that place has to see every record.
HELPERS = ("is_application_expression", "applications_query", "_active_applications", "kind_expression")
COUNT_OR_LIST = (
    r"ApplicationComponent\.query\.(count|all)\(\)|ApplicationComponent\.query\.limit\(|"
    r"(query|select)\((db\.)?func\.count\((ApplicationComponent|A)\.id\)\)"
)
ALLOWED = {
    "app/application_mgmt/import_routes.py": "import: matches rows of a file against every stored record",
    "app/modules/applications/routes/import_export_routes.py": "import and export: every record, whatever its kind",
    "app/modules/applications/routes/import_sophisticated_routes.py": "import: matches rows against every stored record",
    "app/modules/import_batch/services/import_orchestrator.py": "import: matches rows against every stored record",
    "app/application_mgmt/export_routes.py": "export: a complete copy of every record",
    "app/modules/capabilities/routes/export_routes.py": "export: a complete copy of every record",
    "app/application_mgmt/consolidation_routes.py": "merge work: a duplicate may be any record",
    "app/services/consolidation_service.py": "merge work: a duplicate may be any record",
    "app/modules/duplicate_detection/": "duplicate finding compares every record, whatever its kind",
    "app/services/simple_duplicate_service.py": "duplicate finding compares every record, whatever its kind",
    "app/modules/applications/services/application_similarity_service.py": "similarity compares every record",
    "app/services/document_entity_matching_service.py": "matches a document's names against every record",
    "app/modules/applications/routes/capability_tagging_routes.py": "tagging works through every record to be tagged",
    "app/services/roadmap_sync.py": "keeps every record's roadmap row in step",
    "app/services/core/eager_loading.py": "a loading helper, not a figure",
    "app/commands/acm_commands.py": "command line report of every stored record",
    "app/modules/dashboard/services/overview_figures.py": "the counts of the helper itself: each filters by the kind",
    "app/modules/dashboard/routes/dashboard_views.py": "a function nothing calls",
}


def test_every_count_or_list_of_applications_goes_through_the_one_helper():
    found = subprocess.run(["git", "grep", "-nE", COUNT_OR_LIST, "--", "app"], capture_output=True, text=True,
                           check=True).stdout.splitlines()
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
    sources, unfiltered = {}, []
    for hit in found:
        path, number, _ = hit.split(":", 2)
        if any(path.startswith(allowed) for allowed in ALLOWED) or "/tests/" in path:
            continue
        lines = sources.setdefault(path, open("%s/%s" % (root, path)).read().splitlines())
        statement = "\n".join(lines[int(number) - 1:int(number) + 5])
        if not any(helper in statement for helper in HELPERS):
            unfiltered.append("%s:%s" % (path, number))
    assert unfiltered == [], "counts or lists of applications that do not use the helper"
    assert all(reason.strip() for reason in ALLOWED.values())
