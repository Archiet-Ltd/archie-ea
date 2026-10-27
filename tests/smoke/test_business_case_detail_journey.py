
import pytest
from flask import url_for
from playwright.sync_api import Page, expect
import os

from app.models.business_case import BusinessCase
from app.models.organization import Organization
from app.models.user import User

@pytest.mark.usefixtures("db_session")
class TestBusinessCaseDetailJourney:

    @pytest.fixture(autouse=True)
    def set_app_context(self, app):
        with app.app_context():
            yield

    def _make_org_and_user(self, db, suffix):
        org = Organization(name=f"BizCase Journey Org {suffix}", slug=f"bizcase-journey-org-{suffix}")
        db.session.add(org)
        db.session.flush()

        user = User(email=f"bizcase-journey-{suffix}@example.com", first_name="BizCase", last_name="Journeyer", password="password", confirmed=True)
        user.organization_id = org.id
        db.session.add(user)
        db.session.flush()

        return org, user

    def test_business_case_detail_page_links(self, app, db_session, live_server, page: Page):
        import uuid
        from app import db
        
        os.environ["SERVER_NAME"] = "127.0.0.1"

        suffix = uuid.uuid4().hex[:8]
        org, user = self._make_org_and_user(db, suffix)
        
        business_case = BusinessCase(
            title=f"Journey Test Case {suffix}",
            organization_id=org.id,
            created_by_id=user.id,
        )
        db.session.add(business_case)
        db.session.commit()
        
        # Log in as the user
        page.goto(live_server.url + url_for("account.login"))
        page.fill("input[name='email']", user.email, timeout=60000)
        page.fill("input[name='password']", "password", timeout=60000)
        page.click("button[type='submit']")
        expect(page).to_have_url(url_for("main.index", _external=True))

        # Navigate to a business case detail page
        page.goto(url_for("business_case.detail", business_case_id=business_case.id, _external=True))
        expect(page).to_have_url(url_for("business_case.detail", business_case_id=business_case.id, _external=True))

        # Assert every link lands on a page with content.
        links = page.locator("a")
        for i in range(links.count()):
            link = links.nth(i)
            href = link.get_attribute("href")
            if href and not href.startswith("#") and not href.startswith("mailto:") and not href.startswith("tel:"):
                print(f"Clicking link: {href}")
                # Open in new tab if target=_blank
                if link.get_attribute("target") == "_blank":
                    with page.context.expect_page() as new_page_event:
                        link.click()
                    new_page = new_page_event.value
                    new_page.wait_for_load_state()
                    expect(new_page.locator("body")).not_to_be_empty()
                    new_page.close()
                    page.bring_to_front() # Bring the original page back to front
                else:
                    # a relative link will be appended to the current URL.
                    # this can cause issues when we go back.
                    # so we resolve it to an absolute URL first.
                    import urllib.parse
                    abs_href = urllib.parse.urljoin(page.url, href)

                    with page.expect_navigation():
                        link.click()
                    
                    expect(page).to_have_url(abs_href)
                    expect(page.locator("body")).not_to_be_empty()
                    page.go_back()
                    expect(page).to_have_url(url_for("business_case.detail", business_case_id=business_case.id, _external=True))
