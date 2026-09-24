"""
Page-aware AI guide service.
"""

import logging
from typing import Any, Dict, List, Optional

from flask import current_app

from sqlalchemy import text

from app import db
from app.models.vector_embeddings import ChatMessageEmbedding
from app.modules.ai_chat.services.llm_service_impl import LLMService
from app.modules.ai_chat.services.page_guide_registry import get_entry_for_page_key

logger = logging.getLogger(__name__)

_PERSONA_PROFILES = {
    "solution_architect": {
        "label": "solution architect",
        "emphasis": [
            "Focus on the current solution or application context and the safest next design step.",
            "Highlight architecture detail, linked records, and what should be reviewed before editing.",
        ],
        "avoid": [
            "Do not imply the design is complete or already approved.",
            "Do not present write actions as if they have already happened.",
        ],
        "keywords": ["solution", "architecture", "detail", "design", "review"],
    },
    "enterprise_architect": {
        "label": "enterprise architect",
        "emphasis": [
            "Focus on cross-system relationships, capability impacts, and reuse of existing platform data.",
            "Prioritize navigation that helps the user assess enterprise context before making decisions.",
        ],
        "avoid": [
            "Do not over-index on local edits without broader portfolio context.",
            "Do not imply governance approval or enterprise alignment is already satisfied.",
        ],
        "keywords": ["application", "portfolio", "capability", "relationship", "dashboard"],
    },
    "arb_reviewer": {
        "label": "ARB reviewer",
        "emphasis": [
            "Focus on review readiness, evidence, risks, and the next governance-safe action.",
            "Keep recommendations audit-friendly and explicit about manual review steps.",
        ],
        "avoid": [
            "Do not imply approval authority has already been exercised.",
            "Do not state that a record is compliant unless the page context explicitly proves it.",
        ],
        "keywords": ["review", "governance", "risk", "approval", "solution", "evidence"],
    },
    "portfolio_reader": {
        "label": "portfolio reader",
        "emphasis": [
            "Focus on orientation, summary interpretation, and what page or workflow to open next.",
            "Prefer simple navigation guidance over deep implementation detail.",
        ],
        "avoid": [
            "Do not overwhelm with low-level modeling instructions.",
            "Do not suggest editing steps when a read-only interpretation path is more appropriate.",
        ],
        "keywords": ["dashboard", "overview", "portfolio", "applications", "summary"],
    },
    "admin_operator": {
        "label": "admin operator",
        "emphasis": [
            "Focus on platform operations, feature availability, and safe manual administration paths.",
            "Prioritize operational navigation and explain where settings or management views live.",
        ],
        "avoid": [
            "Do not imply hidden system actions or direct backend changes.",
            "Do not claim an administrative change has been executed.",
        ],
        "keywords": ["admin", "users", "settings", "dashboard", "manage"],
    },
}


class PageGuideService:
    """Provides scoped history and safe prompt generation for the page guide."""

    def __init__(self, user_id: int):
        self.user_id = user_id

    @staticmethod
    def is_enabled() -> bool:
        """Guide policy, independent of inference readiness for saved records.

        An absent chat override means automatic provider selection, not an
        administrator disable. Generation checks provider readiness separately.
        """
        chat_override = current_app.config.get("AI_CHAT_ENABLED")
        return bool(current_app.config.get("AI_PAGE_GUIDE_ENABLED", False)) and (
            chat_override is None or bool(chat_override)
        )

    def get_history(self, page_key: str, scope_key: str) -> List[Dict[str, Any]]:
        session_id = self._build_session_id(page_key, scope_key)
        messages = (
            ChatMessageEmbedding.query.filter(
                ChatMessageEmbedding.chat_session_id == session_id,
                ChatMessageEmbedding.user_id == self.user_id,
            )
            .order_by(ChatMessageEmbedding.created_at.asc())
            .all()
        )
        return [
            {
                "role": msg.message_role,
                "content": msg.message_text,
                "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                "metadata": msg.metadata_json or {},
            }
            for msg in messages
        ]

    def clear_history(self, page_key: str, scope_key: str) -> Dict[str, Any]:
        session_id = self._build_session_id(page_key, scope_key)
        cleared = ChatMessageEmbedding.query.filter(
            ChatMessageEmbedding.chat_session_id == session_id,
            ChatMessageEmbedding.user_id == self.user_id,
        ).delete()
        db.session.commit()
        return {"success": True, "cleared_count": cleared}

    def answer_message(
        self,
        page_key: str,
        scope_key: str,
        message: str,
        role_name: str,
        page_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        entry = get_entry_for_page_key(page_key)
        if not entry:
            raise ValueError("Unsupported page guide context")

        persona = self._normalize_persona(role_name)
        prioritized_actions = self._prioritize_actions(
            entry.get("suggested_actions", []),
            persona["key"],
        )
        prompt = self._build_prompt(
            entry=entry,
            scope_key=scope_key,
            page_title=page_title or entry["title"],
            role_name=role_name,
            persona=persona,
            suggested_actions=prioritized_actions,
            user_message=message,
            history=self.get_history(page_key, scope_key),
        )
        response_text = LLMService.generate_from_prompt(prompt, use_cache=False).strip()

        self._persist_message(page_key, scope_key, "user", message)
        self._persist_message(page_key, scope_key, "assistant", response_text)

        return {
            "success": True,
            "response": response_text,
            "guide_mode": entry.get("guide_mode", "specialized"),
            "suggested_actions": prioritized_actions,
            "starter_questions": entry.get("starter_questions", []),
        }

    def _persist_message(self, page_key: str, scope_key: str, role: str, content: str) -> None:
        record = ChatMessageEmbedding(
            chat_session_id=self._build_session_id(page_key, scope_key),
            user_id=self.user_id,
            message_text=content,
            message_role=role,
            domain="guide",
            metadata_json={
                "guide_mode": True,
                "page_key": page_key,
                "scope_key": scope_key,
            },
        )
        db.session.add(record)
        db.session.commit()

    def _build_session_id(self, page_key: str, scope_key: str) -> str:
        safe_scope = (scope_key or page_key).replace(" ", "_")[:120]
        return f"guide_user_{self.user_id}_{page_key}_{safe_scope}"

    def _get_live_context(self, page_key: str, scope_key: str) -> str:
        """Query live DB data to give the LLM real page context. Returns '' on any error."""
        try:
            # Tenant scoping for these raw-SQL snapshots (they bypass the ORM
            # filter). _org_and/_org_where yield an org clause only in a tenant
            # request, so the LLM never sees another org's portfolio; _op binds it.
            from flask import g
            _org = getattr(g, "current_org_id", None)
            def _org_and(col="organization_id"):
                return f" AND {col} = :org" if _org is not None else ""

            def _org_where(col="organization_id"):
                return f" WHERE {col} = :org" if _org is not None else ""
            _op = ({"org": _org} if _org is not None else {})

            record_id: Optional[int] = None
            if ":" in (scope_key or ""):
                try:
                    record_id = int(scope_key.split(":", 1)[1])
                except (ValueError, IndexError):
                    pass

            if page_key == "solutions.detail" and record_id:
                row = db.session.execute(
                    text(
                        f"""
                        SELECT s.name, s.governance_status, s.maturity_current,
                               COUNT(DISTINCT sa.id) AS linked_apps,
                               COUNT(DISTINCT sae.id) AS archimate_elements
                        FROM solutions s
                        LEFT JOIN solution_applications sa ON sa.solution_id = s.id
                        LEFT JOIN solution_archimate_elements sae ON sae.solution_id = s.id
                        WHERE s.id = :id{_org_and('s.organization_id')}
                        GROUP BY s.name, s.governance_status, s.maturity_current
                        """
                    ),
                    {"id": record_id, **_op},
                ).fetchone()
                if row:
                    mc = row[2]
                    maturity = f"CMM level {int(mc)}/5" if mc and int(mc) > 0 else "not scored"
                    return (
                        f"LIVE PAGE DATA (solution id={record_id}):\n"
                        f"- Name: {row[0]}\n"
                        f"- Governance status: {row[1] or 'draft'}\n"
                        f"- Maturity level: {maturity}\n"
                        f"- Linked applications: {row[3]}\n"
                        f"- ArchiMate elements: {row[4]}\n"
                    )

            elif page_key == "applications.detail" and record_id:
                row = db.session.execute(
                    text(
                        f"""
                        SELECT a.name, a.lifecycle_status,
                               ars.rationalization_action, ars.overall_health_score,
                               COUNT(DISTINCT cam.business_capability_id) AS capability_count
                        FROM application_components a
                        LEFT JOIN application_rationalization_scores ars
                            ON ars.application_component_id = a.id
                        LEFT JOIN application_capability_mapping cam
                            ON cam.application_component_id = a.id
                        WHERE a.id = :id{_org_and('a.organization_id')}
                        GROUP BY a.name, a.lifecycle_status,
                                 ars.rationalization_action, ars.overall_health_score
                        """
                    ),
                    {"id": record_id, **_op},
                ).fetchone()
                if row:
                    score = f"{round(float(row[3]), 1)}" if row[3] is not None else "not scored"
                    return (
                        f"LIVE PAGE DATA (application id={record_id}):\n"
                        f"- Name: {row[0]}\n"
                        f"- Lifecycle status: {row[1] or 'unknown'}\n"
                        f"- Rationalization recommendation: {row[2] or 'not assessed'} (score: {score})\n"
                        f"- Mapped capabilities: {row[4]}\n"
                    )

            elif page_key == "dashboard.overview":
                row = db.session.execute(
                    text(
                        f"""
                        SELECT
                            (SELECT COUNT(*) FROM application_components
                             WHERE deleted_at IS NULL{_org_and()}) AS total_apps,
                            (SELECT COUNT(*) FROM solutions{_org_where()}) AS total_solutions,
                            (SELECT ROUND(AVG(maturity_current), 1) FROM solutions
                             WHERE maturity_current > 0{_org_and()}) AS avg_maturity_level,
                            (SELECT COUNT(*) FROM application_rationalization_scores
                             WHERE rationalization_action = 'ELIMINATE'{_org_and()}) AS eliminate_count
                        """
                    ),
                    _op,
                ).fetchone()
                if row:
                    avg = f"CMM {row[2]}/5" if row[2] is not None else "n/a"
                    return (
                        f"LIVE PAGE DATA (portfolio snapshot):\n"
                        f"- Total applications: {row[0]}\n"
                        f"- Total solutions: {row[1]}\n"
                        f"- Average solution maturity: {avg}\n"
                        f"- Applications flagged for elimination: {row[3]}\n"
                    )

            elif page_key == "rationalization":
                row = db.session.execute(
                    text(
                        f"""
                        SELECT
                            COUNT(*) FILTER (WHERE rationalization_action = 'INVEST') AS invest,
                            COUNT(*) FILTER (WHERE rationalization_action = 'TOLERATE') AS tolerate,
                            COUNT(*) FILTER (WHERE rationalization_action = 'ELIMINATE') AS eliminate,
                            COUNT(*) FILTER (WHERE rationalization_action = 'MIGRATE') AS migrate,
                            COUNT(*) AS total
                        FROM application_rationalization_scores{_org_where()}
                        """
                    ),
                    _op,
                ).fetchone()
                if row and row[4]:
                    return (
                        f"LIVE PAGE DATA (rationalization scores):\n"
                        f"- INVEST: {row[0]}  TOLERATE: {row[1]}  ELIMINATE: {row[2]}  MIGRATE: {row[3]}\n"
                        f"- Total scored: {row[4]}\n"
                    )

            elif page_key == "solutions.list":
                row = db.session.execute(
                    text(
                        f"""
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) FILTER (WHERE governance_status = 'approved') AS approved,
                            COUNT(*) FILTER (WHERE governance_status = 'pending_review') AS pending,
                            COUNT(*) FILTER (WHERE governance_status = 'draft' OR governance_status IS NULL) AS draft,
                            ROUND(AVG(maturity_current) FILTER (WHERE maturity_current > 0), 1) AS avg_maturity
                        FROM solutions{_org_where()}
                        """
                    ),
                    _op,
                ).fetchone()
                if row and row[0]:
                    avg = f"CMM {row[4]}/5" if row[4] is not None else "n/a"
                    return (
                        f"LIVE PAGE DATA (solutions portfolio):\n"
                        f"- Total solutions: {row[0]}\n"
                        f"- Approved: {row[1]}  Pending review: {row[2]}  Draft: {row[3]}\n"
                        f"- Average maturity level: {avg}\n"
                    )

            elif page_key == "applications.list":
                row = db.session.execute(
                    text(
                        f"""
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) FILTER (WHERE lifecycle_status ILIKE '%strategic%' OR lifecycle_status ILIKE '%invest%') AS strategic,
                            COUNT(*) FILTER (WHERE lifecycle_status ILIKE '%decommission%' OR lifecycle_status ILIKE '%eliminat%') AS decommission,
                            COUNT(*) FILTER (WHERE lifecycle_status IS NULL OR lifecycle_status = '') AS unknown
                        FROM application_components{_org_where()}
                        """
                    ),
                    _op,
                ).fetchone()
                if row and row[0]:
                    return (
                        f"LIVE PAGE DATA (application portfolio):\n"
                        f"- Total applications: {row[0]}\n"
                        f"- Strategic/Invest: {row[1]}\n"
                        f"- Decommissioning/Eliminate: {row[2]}\n"
                        f"- Unknown lifecycle: {row[3]}\n"
                    )

            elif page_key == "capability_map":
                row = db.session.execute(
                    text(
                        f"""
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) FILTER (WHERE id NOT IN (
                                SELECT DISTINCT business_capability_id
                                FROM application_capability_mapping{_org_where()}
                            )) AS uncovered,
                            COUNT(*) FILTER (WHERE id IN (
                                SELECT DISTINCT business_capability_id
                                FROM application_capability_mapping{_org_where()}
                            )) AS covered
                        FROM business_capability{_org_where()}
                        """
                    ),
                    _op,
                ).fetchone()
                if row and row[0]:
                    pct = round(row[2] / row[0] * 100, 1) if row[0] else 0
                    return (
                        f"LIVE PAGE DATA (capability map):\n"
                        f"- Total capabilities: {row[0]}\n"
                        f"- Covered (≥1 app linked): {row[2]} ({pct}%)\n"
                        f"- Gaps (no apps linked): {row[1]}\n"
                    )

            elif page_key in ("vendors.list", "vendor.list"):
                row = db.session.execute(
                    text(
                        """
                        SELECT COUNT(DISTINCT vo.id) AS vendors,
                               COUNT(DISTINCT vp.id) AS products,
                               COUNT(DISTINCT m.application_component_id) AS linked_apps
                        FROM vendor_organizations vo
                        LEFT JOIN vendor_products vp ON vp.vendor_organization_id = vo.id
                        LEFT JOIN application_vendor_product_mappings m ON m.vendor_product_id = vp.id
                        """
                    )
                ).fetchone()
                if row:
                    return (
                        f"LIVE PAGE DATA (vendor portfolio):\n"
                        f"- Total vendors: {row[0]}\n"
                        f"- Total products: {row[1]}\n"
                        f"- Applications with vendor mappings: {row[2]}\n"
                    )

        except Exception as exc:
            logger.warning(
                "_get_live_context failed for page_key=%r scope=%r: %s",
                page_key, scope_key, exc,
            )
        return ""

    def _build_prompt(
        self,
        *,
        entry: Dict[str, Any],
        scope_key: str,
        page_title: str,
        role_name: str,
        persona: Dict[str, Any],
        suggested_actions: List[Dict[str, Any]],
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> str:
        history_lines = []
        for msg in history[-6:]:
            history_lines.append(f"{msg['role']}: {msg['content']}")

        glossary_lines = [
            f"- {item['term']}: {item['definition']}" for item in entry.get("glossary", [])
        ]
        action_lines = [
            f"- {item['label']}: {item['description']}" for item in suggested_actions
        ]
        emphasis_lines = [f"- {item}" for item in persona["emphasis"]]
        avoid_lines = [f"- {item}" for item in persona["avoid"]]
        generic_endpoint_hint = ""
        if entry.get("guide_mode") == "generic":
            raw_endpoint = scope_key.replace("admin.generic:", "").strip()
            if raw_endpoint and raw_endpoint != "admin.generic":
                readable = raw_endpoint.replace("_", " ").replace(".", " › ").replace("  ", " ")
                generic_endpoint_hint = f"Current page endpoint: {raw_endpoint} (human-readable: {readable})\n"
        generic_rules = (
            "Generic fallback rule:\n"
            "- This page does not have specialized guide metadata yet.\n"
            "- Use the endpoint name above to infer what module or workflow the user is on.\n"
            "- Give orientation and safe navigation help relevant to that section.\n"
            "- Do not claim you have specific page data unless LIVE PAGE DATA is provided.\n\n"
            if entry.get("guide_mode") == "generic"
            else ""
        )
        live_context = self._get_live_context(entry["page_key"], scope_key)

        return (
            "You are the in-app Entelim Page Guide.\n"
            "Your job is to orient a user on the current page, explain what they are seeing, "
            "and suggest safe next steps.\n\n"
            "Rules:\n"
            "- Be concise and practical.\n"
            "- Do not claim you changed data or executed actions.\n"
            "- Do not invent permissions, records, or missing page data.\n"
            "- If the question asks for an action you cannot verify, explain the safest manual path.\n"
            "- Prefer guidance based on the provided page summary, glossary, and actions.\n"
            "- When LIVE PAGE DATA is provided, use it to give specific, accurate answers.\n\n"
            f"Guide mode: {entry.get('guide_mode', 'specialized')}\n"
            f"User role: {role_name}\n"
            f"Normalized persona: {persona['label']} ({persona['key']})\n"
            f"Page key: {entry['page_key']}\n"
            f"Scope key: {scope_key}\n"
            f"Page title: {page_title}\n"
            f"Page summary: {entry['summary']}\n\n"
            f"{generic_endpoint_hint}"
            f"{('Live page data:' + chr(10) + live_context + chr(10)) if live_context else ''}"
            f"{generic_rules}"
            "Persona emphasis:\n"
            f"{chr(10).join(emphasis_lines)}\n\n"
            "Persona avoidances:\n"
            f"{chr(10).join(avoid_lines)}\n\n"
            "Glossary:\n"
            f"{chr(10).join(glossary_lines) if glossary_lines else '- None'}\n\n"
            "Suggested actions:\n"
            f"{chr(10).join(action_lines) if action_lines else '- None'}\n\n"
            "Recent conversation:\n"
            f"{chr(10).join(history_lines) if history_lines else '- None yet'}\n\n"
            f"User question: {user_message}\n\n"
            "Answer in short paragraphs or bullets. Include at most one small next-step list."
        )

    def _normalize_persona(self, role_name: str) -> Dict[str, Any]:
        normalized = (role_name or "").strip().lower().replace("-", "_").replace(" ", "_")

        if "admin" in normalized or "operator" in normalized:
            persona_key = "admin_operator"  # secrets-safety-ok: persona identifier, not a secret
        elif "enterprise" in normalized and "architect" in normalized:
            persona_key = "enterprise_architect"  # secrets-safety-ok: persona identifier, not a secret
        elif "solution" in normalized and "architect" in normalized:
            persona_key = "solution_architect"  # secrets-safety-ok: persona identifier, not a secret
        elif "architect" in normalized:
            persona_key = "solution_architect"  # secrets-safety-ok: persona identifier, not a secret
        elif (
            "review" in normalized
            or normalized.startswith("arb")
            or "governance" in normalized
            or "compliance" in normalized
        ):
            persona_key = "arb_reviewer"  # secrets-safety-ok: persona identifier, not a secret
        elif (
            "portfolio" in normalized
            or "analyst" in normalized
            or "viewer" in normalized
            or "executive" in normalized
            or normalized in {"user", "anonymous", ""}
        ):
            persona_key = "portfolio_reader"  # secrets-safety-ok: persona identifier, not a secret
        else:
            persona_key = "portfolio_reader"  # secrets-safety-ok: persona identifier, not a secret

        return {"key": persona_key, **_PERSONA_PROFILES[persona_key]}

    def _prioritize_actions(
        self,
        actions: List[Dict[str, Any]],
        persona_key: str,
    ) -> List[Dict[str, Any]]:
        keywords = _PERSONA_PROFILES[persona_key]["keywords"]

        def score(action: Dict[str, Any]) -> int:
            haystack = " ".join(
                str(action.get(field, "")).lower() for field in ("label", "description", "url")
            )
            return sum(1 for keyword in keywords if keyword in haystack)

        return sorted(
            actions,
            key=lambda item: score(item),
            reverse=True,
        )
