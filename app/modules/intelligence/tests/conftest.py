"""These tests sign administrators in by writing the session. Every user a test
here creates is recorded as enrolled for two-factor sign-in, so the real
enrolment checks run and see an enrolled administrator."""

import pytest


@pytest.fixture(autouse=True)
def _administrators_have_enrolled(enrolled_admin):
    yield
