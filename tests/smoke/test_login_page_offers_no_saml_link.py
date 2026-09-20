"""The sign-in page links to no SAML sign-in.

The page used to carry a "Sign in with SAML SSO" link that appeared when the
application was configured for SAML. The application registers no SAML sign-in
route, so the page has nothing to link to. This loads the page from the running
application and checks that it renders and that no SAML sign-in link is on it.
"""

import pytest
import requests

pytestmark = [pytest.mark.smoke]


def test_login_page_renders_without_a_saml_sign_in_link(live_server):
    response = requests.get(live_server + "/account/login", timeout=60)

    assert response.status_code == 200
    assert "Don't have an account?" in response.text
    assert "Sign in with SAML" not in response.text
    assert "/account/saml" not in response.text
