"""The sign-in page links to no direct SAML sign-in button.

SAML federation is now implemented (R1-B12 PR 2): /auth/sso/callback/saml
is a real route, and an organisation can configure SAML in /admin/sso. But
sign-in is still one email-domain lookup (the existing
/auth/sso/initiate?email=... path, which dispatches to OIDC or SAML per
the matched organisation's configured protocol) -- the login page itself
has no separate "Sign in with SAML" button, because there is no
per-protocol choice for the visitor to make before entering their email.
This still loads the page from the running application and checks that it
renders and that no such direct link exists.
"""

import pytest
import requests

pytestmark = [pytest.mark.smoke]


def test_login_page_renders_without_a_direct_saml_sign_in_button(live_server):
    response = requests.get(live_server + "/account/login", timeout=60)

    assert response.status_code == 200
    assert "Don't have an account?" in response.text
    assert "Sign in with SAML" not in response.text
    assert "/account/saml" not in response.text
