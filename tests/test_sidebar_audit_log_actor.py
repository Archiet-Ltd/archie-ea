"""The sidebar audit log names the signed-in user by e-mail; ``User`` has no ``username``.

Every sidebar toggle committed its change and then raised ``AttributeError: 'User' object has no attribute
'username'`` from the audit line, so the caller got a 500 for a change that had already been saved.
"""

from types import SimpleNamespace

from app.modules.admin.v2.services import sidebar_menu_audit_log_v2 as audit


def test_toggle_and_update_are_logged_with_the_users_email(app, monkeypatch):
    monkeypatch.setattr(audit, "current_user", SimpleNamespace(id=7, email="admin@example.test", is_authenticated=True))

    with app.test_request_context("/"):
        toggled = audit.SidebarMenuAuditLog.log_toggle("section.home", True)
        updated = audit.SidebarMenuAuditLog.log_update("item.x", {"label": "y"})

    assert (toggled["user_id"], toggled["user_name"]) == (7, "admin@example.test")
    assert (updated["user_id"], updated["user_name"]) == (7, "admin@example.test")


def test_a_signed_out_caller_is_unknown_not_a_crash(app, monkeypatch):
    monkeypatch.setattr(audit, "current_user", SimpleNamespace(is_authenticated=False))

    with app.test_request_context("/"):
        toggled = audit.SidebarMenuAuditLog.log_toggle("section.home", False)

    assert (toggled["user_id"], toggled["user_name"]) == (None, "unknown")
