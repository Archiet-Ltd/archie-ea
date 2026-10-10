"""Onboarding module — the five-screen first-run flow.

Replaces the old first-login modal (PLT-040, app/templates/layouts/admin_base.html)
with a real guided flow: Welcome, Bring your company, First question, Fill the
gaps, Your twin. Nothing beyond screen 2 (P0) is ever mandatory.
"""
from flask import Flask


def register(app: Flask) -> None:
    from .routes import onboarding_bp

    app.register_blueprint(onboarding_bp, url_prefix="/onboarding")
