"""The one way to write a FeatureFlag (the admin/sidebar DB model -- not
``app.services.feature_flag_service.FeatureFlagService``, an unrelated,
confusingly-named AI-capability env-var gate).

``FeatureFlag`` carries no tenant column -- one row switches a feature for
every organisation. Its write routes were gated by ``@admin_required``
(any organisation's own admin role) before being fixed to
``@platform_admin_required`` -- the same defect class found and fixed
across five other platform-wide models this session (``ExternalSystem``,
``Job``, ``AIPromptTemplate``, ``ScoringConfiguration``), each caught only
because a reviewer happened to read the diff.

Every create, edit, toggle, delete and bulk-create-from-sidebar-discovery
write now goes through this module, which checks ``is_platform_admin()``
itself. A future route — in either admin tree, or a new one nobody has
written yet — cannot reintroduce the bug by forgetting the decorator,
because the decorator is no longer the thing enforcing it: this module is.
That is strictly stronger than the route-level fix, and the structural
answer to "how do we make sure this never comes back" -- a CI gate
(``scripts/check_platform_admin_coverage.py``) catches a *new* unguarded
route; this makes the write itself refuse the caller regardless of what
guards the route around it.

One accessor per concept (ADR-0008): both the legacy and v2 admin route
trees call these functions rather than each inlining their own copy of
the same mutation logic.
"""

from __future__ import annotations

from flask import abort
from flask_login import current_user

from app import db
from app.middleware.tenant_decorators import is_platform_admin
from app.models.feature_flags import FeatureFlag, FeatureState, FeatureType


def _require_platform_admin() -> None:
    """Defense in depth: the route decorator should already have refused a
    non-platform-admin caller before this module runs at all. This checks
    again, at the point the write actually happens, so a route that someday
    omits or mis-orders its decorator still cannot mutate the table."""
    if not is_platform_admin(current_user):
        abort(403)


def create_feature_flag(
    *,
    key: str,
    name: str,
    feature_type: FeatureType,
    state: FeatureState,
    description: str | None = None,
    enabled: bool = False,
    sidebar_label: str | None = None,
    sidebar_icon: str | None = None,
    routes: list | None = None,
    parent_id: int | None = None,
    sort_order: int = 0,
) -> FeatureFlag:
    _require_platform_admin()
    feature = FeatureFlag(
        key=key,
        name=name,
        description=description,
        feature_type=feature_type,
        state=state,
        enabled=enabled,
        sidebar_label=sidebar_label,
        sidebar_icon=sidebar_icon,
        routes=routes,
        parent_id=parent_id,
        sort_order=sort_order,
        last_modified_by=current_user.id,
    )
    db.session.add(feature)
    db.session.commit()
    FeatureFlag.clear_cache(feature.key)
    return feature


def update_feature_flag(feature: FeatureFlag, **fields) -> FeatureFlag:
    """``fields`` are the FeatureFlag column names to set -- same shape as
    the form data every caller already had, so callers pass a dict rather
    than this module re-declaring every field the form routes already
    validate.

    Clears the cache under both the old and new key: the legacy admin tree
    already did this (``FeatureFlag.clear_cache`` after every write); the
    v2 tree never called it at all, so toggling or editing a flag there
    would not take effect until the in-process cache happened to clear on
    its own. One accessor fixes the inconsistency for both trees at once.
    """
    _require_platform_admin()
    old_key = feature.key
    for attr, value in fields.items():
        setattr(feature, attr, value)
    feature.last_modified_by = current_user.id
    db.session.commit()
    FeatureFlag.clear_cache(old_key)
    if feature.key != old_key:
        FeatureFlag.clear_cache(feature.key)
    return feature


def toggle_feature_flag(feature: FeatureFlag) -> FeatureFlag:
    _require_platform_admin()
    feature.enabled = not feature.enabled
    feature.last_modified_by = current_user.id
    db.session.commit()
    FeatureFlag.clear_cache(feature.key)
    return feature


def delete_feature_flag(feature: FeatureFlag) -> None:
    _require_platform_admin()
    key = feature.key
    db.session.delete(feature)
    db.session.commit()
    FeatureFlag.clear_cache(key)


def create_feature_flags_from_sidebar(selected_keys: list[str], items_map: dict) -> tuple[int, int]:
    """Bulk-create from the sidebar-discovery picker. Returns (created, skipped)."""
    _require_platform_admin()
    created_count = 0
    skipped_count = 0
    for key in selected_keys:
        existing = FeatureFlag.query.filter_by(key=key).first()
        if existing:
            skipped_count += 1
            continue
        item_data = items_map.get(key)
        if not item_data:
            continue
        link = item_data["link"]
        submenu = item_data["submenu"]
        section = item_data["section"]
        feature = FeatureFlag(
            key=key,
            name=link.text,
            description=f"Controls visibility of '{link.text}' in {section}"
            + (f" > {submenu}" if submenu else ""),
            feature_type=FeatureType.SIDEBAR_LINK,
            state=FeatureState.BETA,
            enabled=True,
            sidebar_label=link.text,
            sidebar_icon=link.icon,
            routes=[link.endpoint] if link.endpoint else [],
            last_modified_by=current_user.id,
        )
        db.session.add(feature)
        created_count += 1
    db.session.commit()
    return created_count, skipped_count
