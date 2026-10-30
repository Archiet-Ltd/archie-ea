"""The post-deploy smoke script: refuses a live host without its flag and word, prints one PASS or FAIL line
per item, names a rollback trigger when the site read stalls, and cleans up what it created."""

from __future__ import annotations

import importlib.util
import pathlib
import re
import threading

import pytest
from flask import Flask, jsonify, redirect, request
from werkzeug.serving import make_server

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "ops" / "live_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("live_smoke", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


live_smoke = _load()


def _product(*, facts_after_site=True, erase_ok=True):
    """A stand-in for the signed-in surface the script drives."""
    app = Flask(__name__)
    app.secret_key = "t"
    state = {"users": {}, "website": False, "erased": [], "pages": []}

    @app.route("/health")
    def health():
        return jsonify({"status": "healthy", "version": "t", "checks": {"database": {"status": "healthy"}}})

    for path in ("/", "/pricing", "/account/login"):
        app.add_url_rule(path, path, lambda: "<main><h1>Entelim</h1><p>content here</p></main>")

    @app.route("/account/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            state["users"][request.form["email"]] = {"org": len(state["users"]) + 1}
            state["current"] = request.form["email"]
            return redirect("/account/welcome")
        return '<meta name="csrf-token" content="tok"><form><input name="csrf_token" value="tok"></form>'

    @app.route("/account/welcome", methods=["GET"])
    def welcome():
        if request.args.get("step") == "3" and state["website"] and facts_after_site:
            return '<ul data-testid="signup-facts-list"><li data-fact="1">x</li></ul>'
        return "<main>Welcome</main>"

    @app.route("/account/welcome/website", methods=["POST"])
    def website():
        assert request.get_json()["consent"] is True
        state["website"] = True
        return jsonify({"ok": True})

    for path in ("/dashboard/overview", "/ai-chat", "/account/manage"):
        app.add_url_rule(path, path, lambda: "<main><h1>Page</h1><p>content here</p></main>")

    @app.route("/compliance/data-subject-requests", methods=["GET", "POST"])
    def dsr():
        if request.method == "GET":
            opts = "".join('<option value="%d">%s</option>' % (v["org"] + 6, e) for e, v in state["users"].items())
            return '<meta name="csrf-token" content="tok"><select name="subject_user_id">%s</select>' % opts
        return redirect("/compliance/data-subject-requests/1")

    @app.route("/compliance/data-subject-requests/<int:i>/erasure/<step>", methods=["POST"])
    def erase(i, step):
        if erase_ok:
            state["erased"].append(step)
        return redirect("/compliance/data-subject-requests/1")

    @app.route("/compliance/data-subject-requests/<int:i>")
    def dsr_detail(i):
        done = "run" in state["erased"]
        return "Erasure complete." if done else "You cannot erase your own account."

    return app, state


@pytest.fixture
def serve():
    servers = []

    def start(**kw):
        app, state = _product(**kw)
        srv = make_server("127.0.0.1", 0, app)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return "http://127.0.0.1:%d" % srv.server_port, state

    yield start
    for s in servers:
        s.shutdown()


def test_refuses_a_live_host_without_flag_and_word(capsys):
    code = live_smoke.main(["https://entelim.org"])
    assert code == 2
    assert "refused" in capsys.readouterr().out.lower()


def test_refuses_live_flag_without_the_confirmation_word(capsys):
    code = live_smoke.main(["https://entelim.org", "--live", "https://entelim.org"])
    assert code == 2


def test_live_flag_must_name_the_same_host(capsys):
    code = live_smoke.main(["https://entelim.org", "--live", "https://other.example", "--confirm", live_smoke.CONFIRM_WORD])
    assert code == 2


def test_preview_hosts_need_no_flag():
    assert live_smoke.host_allowed("http://127.0.0.1:5193", None, None)
    assert live_smoke.host_allowed("http://localhost:5193", None, None)
    assert live_smoke.host_allowed("http://10.1.2.3:5193", None, None)
    assert not live_smoke.host_allowed("https://entelim.org", None, None)
    assert live_smoke.host_allowed("https://entelim.org", "https://entelim.org", live_smoke.CONFIRM_WORD)


def test_prints_one_line_per_item(serve, capsys):
    base, _ = serve()
    code = live_smoke.main([base, "--website", "https://example.com", "--site-timeout", "20", "--health-timeout", "20"])
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if re.match(r"^(PASS|FAIL) ", ln)]
    names = [ln.split(" ", 2)[1] for ln in lines]
    for item in ("health", "schema", "pages", "signup", "signed-in-pages", "website-read", "cleanup"):
        assert item in names, out
    assert all(re.search(r"\(\d+\.\d+s\)", ln) for ln in lines)
    assert code == 0, out


def test_website_read_fails_with_rollback_trigger_when_no_facts_appear(serve, capsys):
    base, _ = serve(facts_after_site=False)
    code = live_smoke.main([base, "--website", "https://example.com", "--site-timeout", "2", "--health-timeout", "20"])
    out = capsys.readouterr().out
    assert re.search(r"^FAIL website-read", out, re.M)
    assert "ROLLBACK TRIGGER" in out and "queue stalled" in out
    assert code == 1


def test_cleanup_erases_each_account_it_made(serve, capsys):
    base, state = serve()
    live_smoke.main([base, "--website", "https://example.com", "--site-timeout", "20", "--health-timeout", "20"])
    assert state["erased"] == ["review", "run"]


def test_cleanup_reports_what_it_could_not_delete(serve, capsys):
    base, state = serve(erase_ok=False)
    code = live_smoke.main([base, "--website", "https://example.com", "--site-timeout", "20", "--health-timeout", "20"])
    out = capsys.readouterr().out
    assert re.search(r"^FAIL cleanup", out, re.M)
    assert "smoke+" in out.split("FAIL cleanup", 1)[1]
    assert code == 1


def test_health_never_answering_fails_within_the_timeout(capsys):
    code = live_smoke.main(["http://127.0.0.1:9", "--health-timeout", "2", "--website", "https://example.com"])
    out = capsys.readouterr().out
    assert re.search(r"^FAIL health", out, re.M)
    assert code == 1
