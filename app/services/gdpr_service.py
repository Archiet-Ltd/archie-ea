"""Data-subject requests: scoping, search assignment, access and erasure.

One service for every data-subject request the platform records
(``GDPRRequest``). Three jobs:

* **Scope a request.** List the systems and processors that hold the personal
  data a request is about, read from the organisation's own model: each
  personal-data category (a ``DataEntity`` marked as holding personal data) is
  followed through its data object to the applications recorded as using it,
  using the canonical relationship walk (``IntelligenceQueryService``), then
  to the suppliers recorded against those applications (contracts, primary
  vendor product, the vendor named on the application record). Nothing
  without a recorded link is listed.
* **Assign and track the searches.** Each listed system or processor can be
  given to a person in the organisation to search, and marked done.
* **Fulfil access and erasure for data the platform itself holds about a
  user.** Erasure is reviewed before it runs, removes the user's personal
  fields, AI chat history and uploaded documents, and records what was
  removed, when and by whom in the audit trail and on the request.

Every read and write here takes the organisation explicitly and filters on
it; a request, a subject or an assignee from another organisation is refused.
"""

import copy
import logging
import os
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.gdpr_request import (
    NO_PLATFORM_ACCOUNT,
    REQUEST_TYPES,
    GDPRRequest,
)
from app.models.user import User

logger = logging.getLogger(__name__)

# The user's own personal fields (and the credentials that could still sign
# them in). Cleared by erasure; listed by name in the evidence, never by value.
PII_FIELDS = ("first_name", "last_name", "email", "password_hash", "external_id", "sso_provider")

# Shown for the AI-system leg of a trace until an AI-system register records
# which AI systems use a category. Never replaced by an inferred link.
AI_SYSTEM_NOT_RECORDED = "not recorded"


class DataSubjectRequestError(ValueError):
    """A request that cannot be carried out as asked (shown to the user)."""


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat(timespec="seconds") if value else None


class GDPRService:
    # ------------------------------------------------------------------ #
    #  Access                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def export_user_data(user_id):
        # tenant-scoping-ok: callers authorise first (self, platform admin, or a
        # request already fenced to the subject's organisation). User is not a
        # TenantMixin model.
        user = User.query.get(user_id)
        if not user:
            return None
        # Collect user profile
        user_data = {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "organization_id": user.organization_id,
            "created_at": getattr(user, "created_at", None),
            "deleted_at": getattr(user, "deleted_at", None),
        }
        return {
            "profile": user_data,
            "conversations": GDPRService._conversations_for(user.id),
            "uploaded_documents": GDPRService._uploads_for(user.organization_id, user.id),
        }

    @staticmethod
    def _conversations_for(user_id):
        threads = db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
            text(
                "SELECT id, title, created_at FROM conversation_threads "
                "WHERE user_id = :user_id ORDER BY created_at"
            ),
            {"user_id": user_id},
        ).all()
        out = []
        for thread in threads:
            messages = db.session.execute(  # tenant-filtered: scoped via parent FK (thread_id)
                text(
                    "SELECT role, content, created_at FROM conversation_messages "
                    "WHERE thread_id = :thread_id ORDER BY created_at"
                ),
                {"thread_id": thread.id},
            ).all()
            out.append(
                {
                    "title": thread.title,
                    "created_at": _iso(thread.created_at),
                    "messages": [
                        {"role": m.role, "content": m.content, "created_at": _iso(m.created_at)}
                        for m in messages
                    ],
                }
            )
        return out

    @staticmethod
    def _uploads_for(organization_id, user_id):
        rows = db.session.execute(
            text(
                "SELECT id, original_filename, file_path FROM ai_chat_document_uploads "
                "WHERE uploaded_by_id = :user_id AND organization_id = :org_id ORDER BY id"
            ),
            {"user_id": user_id, "org_id": organization_id},
        ).all()
        return [{"id": r.id, "file_name": r.original_filename} for r in rows]

    # ------------------------------------------------------------------ #
    #  Erasure                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def subject_in_org(organization_id, user_id):
        """The subject, only when they belong to ``organization_id``."""
        if not user_id or user_id == NO_PLATFORM_ACCOUNT or organization_id is None:
            return None
        return User.query.filter(
            User.id == user_id, User.organization_id == organization_id
        ).first()

    @staticmethod
    def erasure_preview(organization_id, user_id):
        """What an erasure of this user would remove, as counts and field
        names. ``None`` when the user is not in ``organization_id``."""
        user = GDPRService.subject_in_org(organization_id, user_id)
        if user is None:
            return None
        params = {"user_id": user.id, "org_id": organization_id}
        threads = db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
            text("SELECT COUNT(*) FROM conversation_threads WHERE user_id = :user_id"), params
        ).scalar()
        messages = db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
            text(
                "SELECT COUNT(*) FROM conversation_messages m "
                "JOIN conversation_threads t ON t.id = m.thread_id WHERE t.user_id = :user_id"
            ),
            params,
        ).scalar()
        embeddings = db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
            text("SELECT COUNT(*) FROM chat_message_embeddings WHERE user_id = :user_id"), params
        ).scalar()
        uploads = db.session.execute(
            text(
                "SELECT COUNT(*) FROM ai_chat_document_uploads "
                "WHERE uploaded_by_id = :user_id AND organization_id = :org_id"
            ),
            params,
        ).scalar()
        return {
            "user_id": user.id,
            "fields": [f for f in PII_FIELDS if getattr(user, f, None) not in (None, "")],
            "conversation_threads": int(threads or 0),
            "conversation_messages": int(messages or 0),
            "chat_message_embeddings": int(embeddings or 0),
            "uploaded_documents": int(uploads or 0),
        }

    @staticmethod
    def erase_platform_user_data(organization_id, user_id, requester_id, gdpr_request=None):
        """Remove the personal data the platform holds about one user of
        ``organization_id`` and record the evidence.

        Returns the evidence dict, or ``None`` when the user is not in that
        organisation (nothing is touched then). Removes: the user's personal
        fields and sign-in credentials, their AI chat threads and messages,
        their chat message embeddings, and the documents they uploaded to the
        AI chat (rows and files). Revokes their open sessions. Writes one audit
        entry in ``organization_id`` naming what was removed, when and by whom.
        """
        user = GDPRService.subject_in_org(organization_id, user_id)
        if user is None:
            return None
        params = {"user_id": user.id, "org_id": organization_id}

        fields_cleared = [f for f in PII_FIELDS if getattr(user, f, None) not in (None, "")]
        for field in PII_FIELDS:
            setattr(user, field, None)
        # Tombstone columns are optional on this schema — assigning an attribute
        # that is not mapped would silently persist nothing and then read back as
        # a plausible-looking value on export.
        if hasattr(type(user), "deleted_at"):
            user.deleted_at = _now()
        if hasattr(type(user), "status"):
            user.status = "deleted"
        db.session.add(user)

        embeddings = db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
            text("DELETE FROM chat_message_embeddings WHERE user_id = :user_id"), params
        ).rowcount

        uploads = db.session.execute(
            text(
                "SELECT id, file_path FROM ai_chat_document_uploads "
                "WHERE uploaded_by_id = :user_id AND organization_id = :org_id"
            ),
            params,
        ).all()
        files_removed = 0
        for upload in uploads:
            path = upload.file_path
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                    files_removed += 1
                except OSError:
                    logger.warning("erasure: could not remove uploaded file for upload %s", upload.id)
        uploads_deleted = db.session.execute(
            text(
                "DELETE FROM ai_chat_document_uploads "
                "WHERE uploaded_by_id = :user_id AND organization_id = :org_id"
            ),
            params,
        ).rowcount
        db.session.commit()

        thread_ids = [
            row.id
            for row in db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
                text("SELECT id FROM conversation_threads WHERE user_id = :user_id"), params
            ).all()
        ]
        messages_deleted = 0
        if thread_ids:
            messages_deleted = db.session.execute(  # tenant-filtered: scoped via parent FK (user_id)
                text(
                    "SELECT COUNT(*) FROM conversation_messages m "
                    "JOIN conversation_threads t ON t.id = m.thread_id WHERE t.user_id = :user_id"
                ),
                params,
            ).scalar() or 0
            from app.services.conversation_history import get_history_service

            history = get_history_service()
            for thread_id in thread_ids:
                history.delete_thread(thread_id)

        # Revoke every open session for the erased subject: without this, a
        # browser tab already logged in as this user keeps authenticating after
        # "erasure" completes, since erasure only touched stored rows.
        sessions_revoked = True
        from app.services import session_registry

        try:
            session_registry.revoke_all_for_user(user.id, "gdpr_erasure")
        except Exception:
            sessions_revoked = False
            logger.error(
                "erasure: failed to revoke sessions for user_id=%s", user.id, exc_info=True
            )

        removed_at = _now()
        evidence = {
            "subject_user_id": user.id,
            "removed_at": _iso(removed_at),
            "removed_by_id": requester_id,
            "fields_cleared": fields_cleared,
            "conversation_threads_deleted": len(thread_ids),
            "conversation_messages_deleted": int(messages_deleted),
            "chat_message_embeddings_deleted": int(embeddings or 0),
            "uploaded_documents_deleted": int(uploads_deleted or 0),
            "uploaded_files_removed": files_removed,
            "sessions_revoked": sessions_revoked,
        }
        if gdpr_request is not None:
            evidence["request_id"] = gdpr_request.id

        entry = AuditLog(
            organization_id=organization_id,
            user_id=requester_id,
            table_name="gdpr_requests" if gdpr_request is not None else "users",
            record_id=gdpr_request.id if gdpr_request is not None else user.id,
            action="gdpr_delete",
            new_value=evidence,
        )
        db.session.add(entry)
        db.session.flush()
        evidence["audit_entry_id"] = entry.id

        if gdpr_request is not None:
            record = copy.deepcopy(gdpr_request.erasure_evidence_json or {})
            record["result"] = evidence
            gdpr_request.erasure_evidence_json = record
            flag_modified(gdpr_request, "erasure_evidence_json")
            gdpr_request.status = "completed" if GDPRService._searches_done(gdpr_request) else "fulfilled"
            gdpr_request.completed_at = removed_at if gdpr_request.status == "completed" else None
        db.session.commit()
        return evidence

    @staticmethod
    def delete_user_data(user_id, requester_id, gdpr_request=None):
        """Platform-administrator erasure of any user (cross-organisation by
        design; the only caller is ``@platform_admin_required``). Runs the one
        erasure executor in the subject's own organisation."""
        # tenant-scoping-ok: erasure is cross-organisation by design and the only caller
        # (gdpr_bp.delete_user_data) is @platform_admin_required. User is not a
        # TenantMixin model, so no filter is injected here either way.
        user = User.query.get(user_id)
        if not user:
            return False
        evidence = GDPRService.erase_platform_user_data(
            user.organization_id, user_id, requester_id, gdpr_request=gdpr_request
        )
        return evidence is not None

    @staticmethod
    def get_request_status(user_id):
        req = GDPRRequest.query.filter_by(user_id=user_id).order_by(GDPRRequest.requested_at.desc()).first()
        if not req:
            return {"status": "none"}
        return {"status": req.status, "type": req.request_type, "requested_at": req.requested_at, "completed_at": req.completed_at}

    # ------------------------------------------------------------------ #
    #  Scoping                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def personal_data_categories(organization_id, ids=None):
        """The organisation's data entities recorded as holding personal data."""
        from app.models.process_data import DataEntity

        query = DataEntity.query.filter(
            DataEntity.organization_id == organization_id,
            db.or_(
                DataEntity.contains_pii.is_(True),
                DataEntity.gdpr_article_17_applies.is_(True),
                DataEntity.gdpr_article_13_applies.is_(True),
            ),
        )
        if ids:
            query = query.filter(DataEntity.id.in_(list(ids)))
        return query.order_by(DataEntity.name).all()

    @staticmethod
    def _systems_for_category(entity):
        """Applications recorded as using one category's data object: one hop,
        or two when the middle element is an application interface. Read
        through the canonical relationship walk, never a second walker."""
        if not entity.archimate_element_id:
            return []
        from app.modules.intelligence.services.query_service import IntelligenceQueryService

        answer = IntelligenceQueryService.cross_layer_impact(
            entity.archimate_element_id,
            include_derived=False,
            max_depth=2,
            direction="both",
            with_owner=True,
        )
        elements = answer.get("elements") or {}

        def _el(element_id):
            return elements.get(str(element_id)) or {}

        out = []
        for row in answer.get("rows") or []:
            if _el(row["element_id"]).get("type") != "ApplicationComponent":
                continue
            relation = row.get("relation") or {}
            chain = relation.get("chain_elements") or [entity.archimate_element_id, row["element_id"]]
            if any(_el(e).get("type") != "ApplicationInterface" for e in chain[1:-1]):
                continue
            path = [entity.name] + [_el(e).get("name") or "—" for e in chain[1:]]
            owner = row.get("owner") or {}
            out.append(
                {
                    "element_id": row["element_id"],
                    "name": _el(row["element_id"]).get("name") or "—",
                    "path": path,
                    "relationship": relation.get("type"),
                    "recorded_owner": owner.get("name"),
                }
            )
        return out

    @staticmethod
    def _processors_for(organization_id, component_by_element):
        """Suppliers recorded against each in-scope application, keyed by the
        application's element id: contracts, the primary vendor product, and
        the vendor named on the application record."""
        from app.models.application_portfolio import VendorContract

        by_element = {eid: [] for eid in component_by_element}
        if not component_by_element:
            return by_element
        element_by_component = {c.id: eid for eid, c in component_by_element.items()}
        contracts = (
            VendorContract.query.filter(
                VendorContract.organization_id == organization_id,
                VendorContract.application_id.in_(list(element_by_component)),
            )
            .order_by(VendorContract.id)
            .all()
        )
        for contract in contracts:
            vendor = contract.vendor
            if vendor is None:
                continue
            by_element[element_by_component[contract.application_id]].append(
                {
                    "vendor_id": vendor.id,
                    "name": vendor.display_name or vendor.name,
                    "link": "Contract: %s" % (contract.contract_name or "—"),
                }
            )
        for eid, component in component_by_element.items():
            product = component.primary_vendor_product
            vendor = getattr(product, "vendor_organization", None) if product is not None else None
            if vendor is not None:
                by_element[eid].append(
                    {
                        "vendor_id": vendor.id,
                        "name": vendor.display_name or vendor.name,
                        "link": "Primary vendor product: %s" % (product.name or "—"),
                    }
                )
            if (component.vendor_name or "").strip():
                by_element[eid].append(
                    {
                        "vendor_id": None,
                        "name": component.vendor_name.strip(),
                        "link": "Vendor named on the application record",
                    }
                )
        return by_element

    @staticmethod
    def _require_tenant(organization_id):
        from app.middleware.tenant_context import current_org_id

        if organization_id is None or current_org_id() != organization_id:
            raise DataSubjectRequestError("Scoping runs inside the organisation it is for.")

    @staticmethod
    def trace_categories(organization_id, categories):
        """Per category: the systems holding it (each hop cited), the
        suppliers behind each system, and the AI-system leg."""
        from app.models.application_portfolio import ApplicationComponent

        GDPRService._require_tenant(organization_id)
        traced = []
        element_ids = set()
        for entity in categories:
            systems = GDPRService._systems_for_category(entity)
            element_ids.update(s["element_id"] for s in systems)
            traced.append({"category": entity, "systems": systems})

        component_by_element = {}
        if element_ids:
            for component in (
                ApplicationComponent.query.filter(
                    ApplicationComponent.organization_id == organization_id,
                    ApplicationComponent.archimate_element_id.in_(sorted(element_ids)),
                )
                .order_by(ApplicationComponent.id)
                .all()
            ):
                component_by_element.setdefault(component.archimate_element_id, component)
        processors = GDPRService._processors_for(organization_id, component_by_element)
        for entry in traced:
            for system in entry["systems"]:
                component = component_by_element.get(system["element_id"])
                system["application_id"] = component.id if component is not None else None
                system["processors"] = processors.get(system["element_id"], [])
                system["ai_systems"] = AI_SYSTEM_NOT_RECORDED
        return traced

    @staticmethod
    def scope_data_subject_request(organization_id, request_type, category_ids=None):
        """The systems and processors to search for a request, each with the
        recorded links that put it in scope. Categories with no recorded
        system are listed separately, never padded with a guessed one."""
        if request_type not in REQUEST_TYPES:
            raise DataSubjectRequestError("Choose a request type.")
        categories = GDPRService.personal_data_categories(organization_id, category_ids)
        traced = GDPRService.trace_categories(organization_id, categories)

        systems, processors, unlinked = {}, {}, []
        for entry in traced:
            entity = entry["category"]
            if not entry["systems"]:
                unlinked.append({"id": entity.id, "name": entity.name})
            for system in entry["systems"]:
                key = "system:%s" % system["element_id"]
                item = systems.setdefault(
                    key,
                    {
                        "key": key,
                        "kind": "system",
                        "name": system["name"],
                        "element_id": system["element_id"],
                        "application_id": system["application_id"],
                        "recorded_owner": system["recorded_owner"],
                        "via": [],
                    },
                )
                item["via"].append(
                    {
                        "category": entity.name,
                        "path": system["path"],
                        "relationship": system["relationship"],
                    }
                )
                for proc in system["processors"]:
                    pkey = (
                        "processor:%s" % proc["vendor_id"]
                        if proc["vendor_id"]
                        else "processor:name:%s" % proc["name"].lower()
                    )
                    pitem = processors.setdefault(
                        pkey,
                        {"key": pkey, "kind": "processor", "name": proc["name"], "via": []},
                    )
                    hop = {"system": system["name"], "link": proc["link"]}
                    if hop not in pitem["via"]:
                        pitem["via"].append(hop)

        items = sorted(systems.values(), key=lambda i: i["name"].lower()) + sorted(
            processors.values(), key=lambda i: i["name"].lower()
        )
        for item in items:
            item.update(
                {
                    "assignee_id": None,
                    "status": "open",
                    "assigned_at": None,
                    "completed_at": None,
                    "completed_by_id": None,
                }
            )
        return {
            "scoped_at": _iso(_now()),
            "categories": [{"id": e["category"].id, "name": e["category"].name} for e in traced],
            "unlinked_categories": unlinked,
            "items": items,
        }

    # ------------------------------------------------------------------ #
    #  Requests                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def list_requests(organization_id):
        return (
            GDPRRequest.query.filter(GDPRRequest.organization_id == organization_id)
            .order_by(GDPRRequest.requested_at.desc(), GDPRRequest.id.desc())
            .all()
        )

    @staticmethod
    def get_request(organization_id, request_id):
        if organization_id is None:
            return None
        return GDPRRequest.query.filter(
            GDPRRequest.id == request_id, GDPRRequest.organization_id == organization_id
        ).first()

    @staticmethod
    def evidence_for(organization_id, gdpr_request):
        """The audit entries recorded for one request, oldest first."""
        return (
            AuditLog.query.filter(
                AuditLog.organization_id == organization_id,
                AuditLog.table_name == "gdpr_requests",
                AuditLog.record_id == gdpr_request.id,
            )
            .order_by(AuditLog.id)
            .all()
        )

    @staticmethod
    def _audit(organization_id, actor_id, gdpr_request, action, detail):
        entry = AuditLog(
            organization_id=organization_id,
            user_id=actor_id,
            table_name="gdpr_requests",
            record_id=gdpr_request.id,
            action=action,
            new_value=detail,
        )
        db.session.add(entry)
        return entry

    @staticmethod
    def create_request(
        organization_id,
        actor,
        request_type,
        subject_user_id=None,
        subject_reference=None,
        category_ids=None,
    ):
        subject_reference = (subject_reference or "").strip() or None
        if subject_user_id:
            subject = GDPRService.subject_in_org(organization_id, subject_user_id)
            if subject is None:
                raise DataSubjectRequestError("That person is not in your organisation.")
        elif not subject_reference:
            raise DataSubjectRequestError(
                "Name the data subject, or choose a person in your organisation."
            )
        scope = GDPRService.scope_data_subject_request(organization_id, request_type, category_ids)
        req = GDPRRequest(
            organization_id=organization_id,
            user_id=subject_user_id or NO_PLATFORM_ACCOUNT,
            request_type=request_type,
            status="pending",
            requested_at=_now(),
            subject_reference=subject_reference,
            requested_by_id=actor.id,
            scope_json=scope,
        )
        db.session.add(req)
        db.session.flush()
        GDPRService._audit(
            organization_id,
            actor.id,
            req,
            "dsr_scoped",
            {
                "request_type": request_type,
                "systems": sum(1 for i in scope["items"] if i["kind"] == "system"),
                "processors": sum(1 for i in scope["items"] if i["kind"] == "processor"),
                "categories": [c["name"] for c in scope["categories"]],
            },
        )
        db.session.commit()
        return req

    @staticmethod
    def _item(gdpr_request, key):
        scope = copy.deepcopy(gdpr_request.scope_json or {})
        for item in scope.get("items") or []:
            if item.get("key") == key:
                return scope, item
        raise DataSubjectRequestError("That system or processor is not in this request's scope.")

    @staticmethod
    def _save_scope(gdpr_request, scope):
        gdpr_request.scope_json = scope
        flag_modified(gdpr_request, "scope_json")

    @staticmethod
    def _searches_done(gdpr_request):
        items = (gdpr_request.scope_json or {}).get("items") or []
        return all(i.get("status") == "done" for i in items)

    @staticmethod
    def _needs_fulfilment(gdpr_request):
        if not gdpr_request.has_platform_account:
            return False
        if gdpr_request.canonical_type == "erasure":
            return not (gdpr_request.erasure_evidence_json or {}).get("result")
        if gdpr_request.canonical_type == "access":
            return not (gdpr_request.scope_json or {}).get("access_fulfilled_at")
        return False

    @staticmethod
    def _refresh_status(gdpr_request):
        if gdpr_request.status in ("completed", "failed"):
            return
        if GDPRService._searches_done(gdpr_request) and not GDPRService._needs_fulfilment(gdpr_request):
            gdpr_request.status = "completed"
            gdpr_request.completed_at = _now()

    @staticmethod
    def assign_search(organization_id, gdpr_request, key, assignee_id, actor):
        assignee = GDPRService.subject_in_org(organization_id, assignee_id)
        if assignee is None:
            raise DataSubjectRequestError("Choose a person in your organisation to search it.")
        scope, item = GDPRService._item(gdpr_request, key)
        item["assignee_id"] = assignee.id
        item["assigned_at"] = _iso(_now())
        GDPRService._save_scope(gdpr_request, scope)
        GDPRService._audit(
            organization_id, actor.id, gdpr_request, "dsr_assigned",
            {"item": item["name"], "kind": item["kind"], "assignee_id": assignee.id},
        )
        db.session.commit()
        return item

    @staticmethod
    def complete_search(organization_id, gdpr_request, key, actor):
        scope, item = GDPRService._item(gdpr_request, key)
        item["status"] = "done"
        item["completed_at"] = _iso(_now())
        item["completed_by_id"] = actor.id
        GDPRService._save_scope(gdpr_request, scope)
        GDPRService._audit(
            organization_id, actor.id, gdpr_request, "dsr_search_done",
            {"item": item["name"], "kind": item["kind"]},
        )
        GDPRService._refresh_status(gdpr_request)
        db.session.commit()
        return item

    @staticmethod
    def fulfil_access(organization_id, gdpr_request, actor):
        if gdpr_request.canonical_type != "access" or not gdpr_request.has_platform_account:
            raise DataSubjectRequestError("Only an access request about a platform user can be fulfilled here.")
        if GDPRService.subject_in_org(organization_id, gdpr_request.user_id) is None:
            raise DataSubjectRequestError("That person is not in your organisation.")
        data = GDPRService.export_user_data(gdpr_request.user_id)
        scope = copy.deepcopy(gdpr_request.scope_json or {})
        scope["access_fulfilled_at"] = _iso(_now())
        scope["access_fulfilled_by_id"] = actor.id
        GDPRService._save_scope(gdpr_request, scope)
        GDPRService._audit(
            organization_id, actor.id, gdpr_request, "gdpr_access",
            {
                "subject_user_id": gdpr_request.user_id,
                "conversations": len(data["conversations"]),
                "uploaded_documents": len(data["uploaded_documents"]),
            },
        )
        GDPRService._refresh_status(gdpr_request)
        db.session.commit()
        return data

    @staticmethod
    def _check_erasable(organization_id, gdpr_request, actor):
        if gdpr_request.canonical_type != "erasure" or not gdpr_request.has_platform_account:
            raise DataSubjectRequestError("Only an erasure request about a platform user can be run here.")
        if (gdpr_request.erasure_evidence_json or {}).get("result"):
            raise DataSubjectRequestError("This erasure has already run.")
        if gdpr_request.user_id == actor.id:
            raise DataSubjectRequestError("You cannot erase your own account.")
        subject = GDPRService.subject_in_org(organization_id, gdpr_request.user_id)
        if subject is None:
            raise DataSubjectRequestError("That person is not in your organisation.")
        if subject.is_platform_admin:
            raise DataSubjectRequestError("A platform administrator's account is erased by the platform team.")
        return subject

    @staticmethod
    def review_erasure(organization_id, gdpr_request, actor):
        """Record what the erasure will remove, and who reviewed it, before
        it can run."""
        GDPRService._check_erasable(organization_id, gdpr_request, actor)
        preview = GDPRService.erasure_preview(organization_id, gdpr_request.user_id)
        record = copy.deepcopy(gdpr_request.erasure_evidence_json or {})
        record["review"] = {
            "reviewed_at": _iso(_now()),
            "reviewed_by_id": actor.id,
            "will_remove": preview,
        }
        gdpr_request.erasure_evidence_json = record
        flag_modified(gdpr_request, "erasure_evidence_json")
        gdpr_request.status = "reviewed"
        GDPRService._audit(organization_id, actor.id, gdpr_request, "dsr_erasure_review", preview)
        db.session.commit()
        return preview

    @staticmethod
    def run_erasure(organization_id, gdpr_request, actor):
        GDPRService._check_erasable(organization_id, gdpr_request, actor)
        if gdpr_request.status != "reviewed" or not (gdpr_request.erasure_evidence_json or {}).get("review"):
            raise DataSubjectRequestError("Review what will be removed before running the erasure.")
        evidence = GDPRService.erase_platform_user_data(
            organization_id, gdpr_request.user_id, actor.id, gdpr_request=gdpr_request
        )
        if evidence is None:
            raise DataSubjectRequestError("That person is not in your organisation.")
        return evidence
