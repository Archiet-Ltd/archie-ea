"""Sign-up after the account: bring the company in, then choose the AI provider.

The pages are the sign-up stepper (``account/register.html``) at its second and third step. Each
source starts its import in the background the moment it is given, no step waits on a slow job and
Finish is never blocked. Who may connect a source is the connectors page's own rule, so the two
cannot disagree.
"""

from __future__ import annotations

import logging

from flask import g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import db
from app.models.organization import Organization
from app.modules.account.services import onboarding_service as onboarding
from app.modules.organization.website_crawl import confirm

_log = logging.getLogger(__name__)


def _org() -> Organization:
    return db.session.get(Organization, g.current_org_id)


def _problem(message: str, status: int = 400):
    return jsonify({"ok": False, "message": message}), status


def _state_payload(org) -> dict:
    return {"ok": True, "sources": onboarding.sources(org)}


def add_routes(bp, access) -> None:
    """Register the sign-up routes on the account blueprint. ``access`` is the connectors page's
    rule for who may bring a source in."""

    @bp.route("/welcome", methods=["GET"])
    @login_required
    @access
    def welcome():
        from app.connectors import github_app

        org = _org()
        step = request.args.get("step", type=int)
        step = step if step in (3, 4) else 2
        facts, completeness = (confirm.proposals(org.id) if step == 3 else ([], None))
        return render_template(
            "account/register.html",
            form=None,
            github_app_install_url=github_app.install_url(org.id, return_to="welcome"),
            step=step,
            org=org,
            kinds=onboarding.KINDS,
            industries=onboarding.INDUSTRIES,
            frameworks=onboarding.FRAMEWORKS,
            chosen={**onboarding.choices(org), **confirm.asked(org.settings)},
            size_bands=confirm.SIZE_BANDS,
            goals=confirm.GOALS,
            facts=facts,
            more_coming=onboarding.more_facts_coming(org) if step == 3 else None,
            completeness=completeness,
            suggested=onboarding.suggested_frameworks(
                onboarding.choices(org).get("kind"), confirm.asked(org.settings).get("goal")),
            goal_frameworks=onboarding.FRAMEWORKS_FOR_GOAL,
            kind_frameworks=onboarding.FRAMEWORKS_FOR_KIND,
            website=((org.settings or {}).get("onboarding") or {}).get("website"),
            sources=onboarding.sources(org),
            providers=onboarding.PROVIDERS,
            paying=onboarding.on_paying_plan(org),
            own_key=onboarding.own_key_provider(),
            signin_args={},
        )

    @bp.route("/welcome/status", methods=["GET"])
    @login_required
    @access
    def welcome_status():
        return jsonify(_state_payload(_org()))

    @bp.route("/welcome/github", methods=["POST"])
    @login_required
    @access
    def welcome_github():
        from app.connectors import github as gh

        data = request.get_json(silent=True) or {}
        try:
            gh.connect_and_start(
                g.current_org_id, data.get("organization"), data.get("token"),
                task=onboarding.GITHUB_TASK, extra={"approver_id": current_user.id},
            )
        except gh.ConnectRefused as exc:
            return _problem(str(exc))
        return jsonify(_state_payload(_org()))

    @bp.route("/welcome/jira", methods=["POST"])
    @login_required
    @access
    def welcome_jira():
        data = request.get_json(silent=True) or {}
        try:
            onboarding.connect_jira(
                g.current_org_id, data.get("instance_url"), data.get("email"), data.get("token"), current_user.id
            )
        except ValueError as exc:
            return _problem(str(exc))
        return jsonify(_state_payload(_org()))

    @bp.route("/welcome/website", methods=["POST"])
    @login_required
    @access
    def welcome_website():
        data = request.get_json(silent=True) or {}
        try:
            note = onboarding.save_website(_org(), data.get("url"), bool(data.get("consent")), current_user.id)
        except onboarding.ReadingLimited as exc:
            return _problem(str(exc), 429)
        except ValueError as exc:
            return _problem(str(exc))
        return jsonify({**_state_payload(_org()), "note": note})

    @bp.route("/welcome/choices", methods=["POST"])
    @login_required
    @access
    def welcome_choices():
        data = request.get_json(silent=True) or {}
        try:
            saved = onboarding.save_choices(_org(), data.get("industry"), data.get("frameworks") or [], data.get("kind"))
        except ValueError as exc:
            return _problem(str(exc))
        return jsonify({"ok": True, **saved, "removable": onboarding.removable_starter_rows(_org())})

    @bp.route("/welcome/starter/remove", methods=["POST"])
    @login_required
    @access
    def welcome_starter_remove():
        data = request.get_json(silent=True) or {}
        try:
            removed = onboarding.remove_unedited_starter_rows(_org(), data.get("model"))
        except ValueError as exc:
            return _problem(str(exc))
        except IntegrityError:
            db.session.rollback()
            return _problem("Some of these capabilities are still used elsewhere, so none were removed.")
        return jsonify({"ok": True, "removed": removed})

    @bp.route("/welcome/profile", methods=["POST"])
    @login_required
    @access
    def welcome_profile():
        """The three questions asked up front, and the person's decision on each fact read from the site."""
        data = request.get_json(silent=True) or {}
        org = _org()
        try:
            confirm.save_answers(org, data.get("size_band"), data.get("role"), data.get("goal"))
            result = confirm.confirm(org, current_user, data.get("facts") or [])
        except ValueError as exc:
            return _problem(str(exc))
        from app.modules.organization.public_signals import queue_signals_refresh

        onboarding.mark_profile_confirmed(org)
        queue_signals_refresh(org.id)  # the public sources, read for the company just confirmed
        return jsonify({"ok": True, **result})

    @bp.route("/signals/refresh", methods=["POST"])
    @login_required
    def signals_refresh():
        """Read the public sources again now, for the organisation that agreed to it. It reads public pages
        only and is held to once a day, so anyone shown the button on their account page may press it."""
        from flask import flash

        from app.modules.organization.public_signals import company_name, queue_signals_refresh

        if not company_name(_org()):
            flash("Confirm your company name first; the public sources are searched for that name.", "error")
        elif queue_signals_refresh(g.current_org_id) is None:
            flash("Your company has not agreed to its public footprint being read, so there is nothing to refresh.", "error")
        else:
            flash("Reading the public sources now. New signals appear here in a few minutes.", "success")
        return redirect(url_for("account.manage"))

    @bp.route("/welcome/ai/test", methods=["POST"])
    @login_required
    @access
    def welcome_ai_test():
        """Test the key, and keep it for this organisation only when the provider accepts it."""
        data = request.get_json(silent=True) or {}
        provider, key = data.get("provider"), (data.get("key") or "").strip()
        result = onboarding.check_key(provider, key)
        if not result.get("success"):
            return _problem(result.get("message") or "The provider did not accept this key.")
        onboarding.save_key(provider, key, current_user.id)
        label = dict(onboarding.PROVIDERS)[provider]
        return jsonify({
            "ok": True,
            "message": "%s accepted the key. Your questions now go to %s, and your organisation pays %s for them." % (label, label, label),
        })

    @bp.route("/welcome/finish", methods=["POST"])
    @login_required
    @access
    def welcome_finish():
        onboarding.mark_finished(_org())
        return redirect(url_for("dashboard.overview"))
