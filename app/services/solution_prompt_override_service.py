"""The one way to write a solution-prompt AIPromptTemplate override.

``AIPromptTemplate`` carries no tenant column -- an override saved against a
solution-drafting prompt key (e.g. ``draft_architecture``) replaces that
prompt for every organisation's Architecture Journey. The write routes
(update, reset, rollback) were originally gated by ``@admin_required`` --
any organisation's own admin role -- across two parallel copies
(``app/modules/admin/v2/routes/admin_routes.py``'s inline copy, the one
actually registered at boot, and the separately-registered
``app/modules/admin/routes/solution_prompt_admin.py``, kept per the lead's
ruling that removing it could drop the page in a deployment without
``USE_ADMIN_GUARDRAILS``); fixed to ``@platform_admin_required`` in PR #307.

Routing every write through this module instead makes the check structural
rather than decorator-dependent (ADR-0008, one accessor per concept). It
returns plain data (the ``AIPromptTemplate`` row, or ``None`` where a route
needs to answer 404); each route keeps building its own JSON response from
its own ``_get_prompt_defaults()`` catalogue, since that catalogue is
presentation data, not something this module should own.

Learned from pr324-review-v1 nit 1 (the ScoringConfiguration chokepoint's
defence-in-depth 403 silently becoming a 500 under a bare ``except
Exception``): every route calling this module must re-raise
``HTTPException`` before its generic handler. Done from the start here
rather than found by review a second time.
"""

from __future__ import annotations

from datetime import datetime

from flask import abort
from flask_login import current_user

from app import db
from app.middleware.tenant_decorators import is_platform_admin
from app.models.ai_service import AIPromptTemplate, AIPromptTemplateVersion


def _require_platform_admin() -> None:
    """Defense in depth: the route decorator should already have refused a
    non-platform-admin caller before this module runs at all."""
    if not is_platform_admin(current_user):
        abort(403)


def override_key(prompt_key: str) -> str:
    return f"solution_prompt_{prompt_key}"


def update_override(prompt_key: str, description: str, prompt_text: str) -> AIPromptTemplate:
    """Create the override if none exists, or snapshot-then-mutate it if one does."""
    _require_platform_admin()
    name = override_key(prompt_key)
    override = AIPromptTemplate.query.filter_by(name=name).first()

    if not override:
        override = AIPromptTemplate(
            name=name,
            description=description,
            system_prompt=prompt_text,
            user_prompt_template="",
            category="solution_prompt",
            updated_by_id=current_user.id,
            version=1,
        )
        db.session.add(override)
    else:
        # A-05: snapshot the state being replaced BEFORE mutating, so the
        # history table always holds every prior version and the live row
        # is always "current".
        db.session.add(AIPromptTemplateVersion(
            template_name=override.name,
            version=override.version or 1,
            system_prompt=override.system_prompt,
            change_type="update",
            updated_by_id=override.updated_by_id,
        ))
        override.system_prompt = prompt_text
        override.updated_at = datetime.utcnow()
        override.updated_by_id = current_user.id
        override.version = (override.version or 1) + 1

    db.session.commit()
    return override


def reset_override(prompt_key: str) -> None:
    """Remove the override, reverting to the hardcoded default. A no-op if
    none exists -- matches the original routes' behaviour exactly."""
    _require_platform_admin()
    name = override_key(prompt_key)
    override = AIPromptTemplate.query.filter_by(name=name).first()

    if override:
        # A-05: keep the override's content in history even though the live
        # row is about to be deleted -- otherwise reset would erase the only
        # record of what the override used to say.
        db.session.add(AIPromptTemplateVersion(
            template_name=override.name,
            version=override.version or 1,
            system_prompt=override.system_prompt,
            change_type="reset",
            updated_by_id=current_user.id,
        ))
        db.session.delete(override)
        db.session.commit()


def rollback_override(prompt_key: str, version: int, description: str) -> AIPromptTemplate | None:
    """Restore a prior version's content as the live override.

    Returns None when no history row exists for that version -- the
    caller's job to turn into a 404, since the exact message differs
    slightly between the two route copies this module replaces.
    """
    _require_platform_admin()
    name = override_key(prompt_key)
    target = (
        AIPromptTemplateVersion.query.filter_by(template_name=name, version=version)
        .order_by(AIPromptTemplateVersion.id.desc())
        .first()
    )
    if not target:
        return None

    override = AIPromptTemplate.query.filter_by(name=name).first()

    if override:
        db.session.add(AIPromptTemplateVersion(
            template_name=override.name,
            version=override.version or 1,
            system_prompt=override.system_prompt,
            change_type="update",
            updated_by_id=current_user.id,
        ))
        override.system_prompt = target.system_prompt
        override.updated_at = datetime.utcnow()
        override.updated_by_id = current_user.id
        override.version = (override.version or 1) + 1
    else:
        override = AIPromptTemplate(
            name=name,
            description=description,
            system_prompt=target.system_prompt,
            user_prompt_template="",
            category="solution_prompt",
            updated_by_id=current_user.id,
            version=1,
        )
        db.session.add(override)

    db.session.commit()
    return override
