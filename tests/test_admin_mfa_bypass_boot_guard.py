"""Boot-time guarantees for the admin-MFA smoke bypass (R1-B12 PR 2 fix
round, PR #356).

``ADMIN_MFA_BYPASS`` exists only as a hardcoded class attribute on
``config.py``'s ``SmokeTestingConfig``, read by
``app.services.mfa_service._admin_mfa_bypass_active()``. It must never be
readable from an environment variable, a request, a header or a database
setting, and it must never reach a real deployment. These tests assert both
halves of that: the switch is absent from every config class that is not
the dedicated smoke-boot config, and ``create_app()`` itself refuses to
start if the switch is ever true without Flask's own ``TESTING`` flag --
the backstop if a future change ever copies the attribute onto the wrong
class.
"""

from __future__ import annotations

import pytest


def test_production_config_never_sets_the_admin_mfa_bypass():
    """Read the class directly, not an instantiated app -- nothing a
    deployed environment could set should make this true."""
    from config import ProductionConfig

    assert not getattr(ProductionConfig, "ADMIN_MFA_BYPASS", False)


def test_heroku_and_unix_configs_never_set_the_admin_mfa_bypass():
    from config import HerokuConfig, UnixConfig

    assert not getattr(HerokuConfig, "ADMIN_MFA_BYPASS", False)
    assert not getattr(UnixConfig, "ADMIN_MFA_BYPASS", False)


def test_development_config_never_sets_the_admin_mfa_bypass():
    from config import DevelopmentConfig

    assert not getattr(DevelopmentConfig, "ADMIN_MFA_BYPASS", False)


def test_ordinary_testing_config_does_not_set_the_bypass_either():
    """Only the dedicated smoke-boot config carries the switch -- the shared
    non-browser pytest suite (tests/conftest.py's session-scoped ``app``
    fixture, used by hundreds of modules including tests/test_mfa_login_gate.py)
    keeps exercising the real gate, unchanged."""
    from config import TestingConfig

    assert not getattr(TestingConfig, "ADMIN_MFA_BYPASS", False)


def test_smoke_testing_config_is_the_one_place_the_switch_is_true():
    from config import SmokeTestingConfig

    assert SmokeTestingConfig.ADMIN_MFA_BYPASS is True
    assert SmokeTestingConfig.TESTING is True


def test_create_app_refuses_to_boot_if_the_bypass_is_true_without_testing():
    """The switch is a hardcoded class attribute, never an environment
    variable -- but if a future config class ever combines it with TESTING
    unset (e.g. a careless copy of SmokeTestingConfig), create_app() must
    refuse to start rather than ship a build where an administrator can
    sign in without completing MFA."""
    import config as config_module
    from app import create_app

    class _TamperedConfig(config_module.ProductionConfig):
        ADMIN_MFA_BYPASS = True

    name = "_tampered_admin_mfa_bypass_for_this_test"
    config_module.config[name] = _TamperedConfig
    try:
        with pytest.raises(RuntimeError, match="ADMIN_MFA_BYPASS"):
            create_app(name)
    finally:
        del config_module.config[name]


def test_create_app_boots_fine_with_the_bypass_true_under_a_genuine_testing_config():
    """The counterpart to the refusal above: the same switch, combined with
    TESTING=True (SmokeTestingConfig's actual shape), must not itself stop
    the app from booting."""
    from app import create_app

    app = create_app("smoke")
    assert app.config["ADMIN_MFA_BYPASS"] is True
    assert app.config["TESTING"] is True
