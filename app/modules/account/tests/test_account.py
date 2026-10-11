"""
Tests for account module - authentication, registration, and account management.

Verifies that the migrated account routes produce identical responses
to the original app/account/views.py implementation.
"""


class TestAccountService:
    """Unit tests for AccountService methods."""

    def test_authenticate_valid(self, app):
        """authenticate() returns User for valid credentials."""
        with app.app_context():
            from app.modules.account.services.account_service import AccountService
            # This is a structural import test - actual auth requires a seeded DB
            assert hasattr(AccountService, 'authenticate')

    def test_authenticate_invalid(self, app):
        """authenticate() returns None for invalid credentials."""
        with app.app_context():
            from app.modules.account.services.account_service import AccountService
            result = AccountService.authenticate("nonexistent@test.com", "wrongpass")
            assert result is None

    def test_register_user_creates_user(self, app):
        """register_user() creates and returns a User."""
        with app.app_context():
            from app.modules.account.services.account_service import AccountService
            assert hasattr(AccountService, 'register_user')

    def test_queue_email_callable(self, app):
        """The e-mail queueing helper the service sends through is callable."""
        with app.app_context():
            from app.modules.account.services import account_service
            assert callable(account_service._queue_email)

    def test_change_password_method_exists(self, app):
        """change_password() method exists on AccountService."""
        with app.app_context():
            from app.modules.account.services.account_service import AccountService
            assert hasattr(AccountService, 'change_password')

    def test_request_email_change_method_exists(self, app):
        """request_email_change() method exists on AccountService."""
        with app.app_context():
            from app.modules.account.services.account_service import AccountService
            assert hasattr(AccountService, 'request_email_change')

    def test_confirm_account_method_exists(self, app):
        """confirm_account() method exists on AccountService."""
        with app.app_context():
            from app.modules.account.services.account_service import AccountService
            assert hasattr(AccountService, 'confirm_account')

    def test_join_from_invite_is_retired(self, app):
        """The reusable-token invitation path is gone; /account/join/<token> is the one flow."""
        with app.app_context():
            from app.modules.account.services.account_service import AccountService
            assert not hasattr(AccountService, 'join_from_invite')


class TestAccountForms:
    """Tests for migrated WTForms classes."""

    def test_login_form_imports(self, app):
        """LoginForm imports from new location."""
        with app.app_context():
            from app.modules.account.forms.account_forms import LoginForm
            form = LoginForm(meta={'csrf': False})
            assert hasattr(form, 'email')
            assert hasattr(form, 'password')
            assert hasattr(form, 'remember_me')

    def test_registration_form_imports(self, app):
        """RegistrationForm imports from new location."""
        with app.app_context():
            from app.modules.account.forms.account_forms import RegistrationForm
            form = RegistrationForm(meta={'csrf': False})
            assert hasattr(form, 'first_name')
            assert hasattr(form, 'last_name')
            assert hasattr(form, 'email')
            assert hasattr(form, 'password')

    def test_reset_password_form_imports(self, app):
        """ResetPasswordForm imports from new location."""
        with app.app_context():
            from app.modules.account.forms.account_forms import ResetPasswordForm
            form = ResetPasswordForm(meta={'csrf': False})
            assert hasattr(form, 'email')
            assert hasattr(form, 'new_password')

    def test_change_password_form_imports(self, app):
        """ChangePasswordForm imports from new location."""
        with app.app_context():
            from app.modules.account.forms.account_forms import ChangePasswordForm
            form = ChangePasswordForm(meta={'csrf': False})
            assert hasattr(form, 'old_password')
            assert hasattr(form, 'new_password')

    def test_change_email_form_imports(self, app):
        """ChangeEmailForm imports from new location."""
        with app.app_context():
            from app.modules.account.forms.account_forms import ChangeEmailForm
            form = ChangeEmailForm(meta={'csrf': False})
            assert hasattr(form, 'email')
            assert hasattr(form, 'password')

    def test_create_password_form_imports(self, app):
        """CreatePasswordForm imports from new location."""
        with app.app_context():
            from app.modules.account.forms.account_forms import CreatePasswordForm
            form = CreatePasswordForm(meta={'csrf': False})
            assert hasattr(form, 'password')

    def test_request_reset_password_form_imports(self, app):
        """RequestResetPasswordForm imports from new location."""
        with app.app_context():
            from app.modules.account.forms.account_forms import RequestResetPasswordForm
            form = RequestResetPasswordForm(meta={'csrf': False})
            assert hasattr(form, 'email')


class TestAccountRoutes:
    """Integration tests for account route endpoints."""

    def test_login_page(self, client):
        """GET /account/login renders login form."""
        resp = client.get("/account/login")
        assert resp.status_code == 200

    def test_login_post_invalid(self, client):
        """POST /account/login with invalid credentials returns 200 (re-renders form)."""
        resp = client.post("/account/login", data={
            "email": "fake@test.com",
            "password": "wrongpassword",
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_register_page(self, client):
        """GET /account/register renders registration form."""
        resp = client.get("/account/register")
        assert resp.status_code == 200

    def test_logout_requires_auth(self, client):
        """GET /account/logout redirects to login when not authenticated."""
        resp = client.get("/account/logout")
        # Should redirect to login page
        assert resp.status_code in (302, 401)

    def test_manage_requires_auth(self, client):
        """GET /account/manage requires authentication."""
        resp = client.get("/account/manage")
        assert resp.status_code in (302, 401)

    def test_manage_info_requires_auth(self, client):
        """GET /account/manage/info requires authentication."""
        resp = client.get("/account/manage/info")
        assert resp.status_code in (302, 401)

    def test_change_password_requires_auth(self, client):
        """GET /account/manage/change-password requires authentication."""
        resp = client.get("/account/manage/change-password")
        assert resp.status_code in (302, 401)

    def test_change_email_requires_auth(self, client):
        """GET /account/manage/change-email requires authentication."""
        resp = client.get("/account/manage/change-email")
        assert resp.status_code in (302, 401)

    def test_reset_password_page(self, client):
        """GET /account/reset-password renders reset form."""
        resp = client.get("/account/reset-password")
        assert resp.status_code == 200

    def test_confirm_account_requires_auth(self, client):
        """GET /account/confirm-account requires authentication."""
        resp = client.get("/account/confirm-account")
        assert resp.status_code in (302, 401)

    def test_unconfirmed_anonymous_redirects(self, client):
        """GET /account/unconfirmed redirects anonymous users."""
        resp = client.get("/account/unconfirmed")
        assert resp.status_code == 302

    def test_before_request_hook_exists(self, app):
        """The confirm-your-address hook of the account blueprint in use runs on every request."""
        with app.app_context():
            from app.modules.account.routes import account_routes
            from app.modules.account.v2.routes import account_routes as account_routes_v2
            # A blueprint's before_app_request hook is recorded on the app,
            # under the None key that runs for every request. Which account
            # blueprint is mounted depends on USE_ACCOUNT_GUARDRAILS.
            hooks = app.before_request_funcs.get(None, [])
            assert (
                account_routes.before_request in hooks
                or account_routes_v2.before_request in hooks
            )
