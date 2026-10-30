"""The inventory's route and control records, checked on a tiny app so the counting rules are pinned down.

One rule in the url_map is one route (never one per template); a page is a GET route that renders a template; every
link, button, form, modal and data-testid in a template is one control, attributed to every page that renders it
through extends and include.
"""
import json
import sys
from pathlib import Path

from flask import Flask, render_template

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from component_inventory import build_inventory, view_guards  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "component_inventory_pages"


def login_required(f):
    return f


def _require_admin():
    """Guard function that checks auth and aborts."""
    from flask import abort
    from flask_login import current_user
    if not current_user or not current_user.is_authenticated:
        abort(401)
    if not getattr(current_user, "is_admin", False):
        abort(403)


def make_app():
    app = Flask(__name__, template_folder=str(FIX))

    @app.route("/")
    def home():
        return "home"

    @app.route("/page")
    @login_required
    def page():
        return render_template("inv_page.html")

    @app.route("/search")
    def search():
        return render_template("inv_page.html")

    @app.route("/change", methods=["POST"])
    def change():
        return "ok"

    @app.route("/item/<int:item_id>", methods=["GET", "DELETE"])
    def item(item_id):
        return "x"

    # Route with in-function guard call (like run-record routes)
    @app.route("/guarded-in-function")
    def guarded_in_function():
        _require_admin()
        return "guarded"

    # Route with in-function guard call that returns error tuple
    def _require_org_admin_tuple():
        from flask import jsonify
        from flask_login import current_user
        if not current_user or not current_user.is_authenticated:
            return jsonify({"error": "unauthorized"}), 401
        if not getattr(current_user, "is_org_admin", False):
            return jsonify({"error": "forbidden"}), 403

    @app.route("/guarded-in-function-tuple")
    def guarded_in_function_tuple():
        _require_org_admin_tuple()
        return "guarded"

    return app


def _files():
    return [(str(FIX), str(p)) for p in sorted(FIX.glob("*.html"))]


def _build():
    app = make_app()
    # the templates name endpoints fx.home and fx.search: register aliases so those targets resolve
    for ep in ("home", "search"):
        app.view_functions[f"fx.{ep}"] = app.view_functions[ep]
    return build_inventory(template_files_list=_files(), application=app)


def test_one_route_per_url_map_rule():
    inv = _build()
    rules = {(r["rule"], tuple(r["methods"])) for r in inv["routes"]}
    assert ("/page", ("GET",)) in rules and ("/change", ("POST",)) in rules
    assert ("/item/<int:item_id>", ("DELETE", "GET")) in rules
    assert inv["counts"]["routes"] == len(inv["routes"]) == 8  # seven rules plus static


def test_route_carries_methods_endpoint_blueprint_and_login():
    inv = _build()
    page = next(r for r in inv["routes"] if r["rule"] == "/page")
    assert page["endpoint"] == "page" and page["blueprint"] is None
    assert page["login_required"] is True and page["guards"] == ["login_required"]
    home = next(r for r in inv["routes"] if r["rule"] == "/")
    assert home["login_required"] is False
    assert page["page"] is True and next(r for r in inv["routes"] if r["rule"] == "/change")["page"] is False


def test_in_function_guard_is_detected():
    """Routes guarded by a call at the top of the function are marked login_required with the guard named."""
    inv = _build()
    
    # Route with abort-based guard
    guarded = next(r for r in inv["routes"] if r["rule"] == "/guarded-in-function")
    assert guarded["login_required"] is True, "Route with in-function abort guard should be login_required"
    assert "_require_admin" in guarded["guards"], f"Guard _require_admin should be named, got {guarded['guards']}"
    
    # Route with tuple-return guard
    guarded_tuple = next(r for r in inv["routes"] if r["rule"] == "/guarded-in-function-tuple")
    assert guarded_tuple["login_required"] is True, "Route with in-function tuple guard should be login_required"
    assert "_require_org_admin_tuple" in guarded_tuple["guards"], f"Guard _require_org_admin_tuple should be named, got {guarded_tuple['guards']}"


def test_view_guards_detects_in_function_calls():
    """Unit test for view_guards function with in-function guard calls."""
    app = make_app()
    with app.app_context():
        # Test abort-based guard
        guarded_view = app.view_functions["guarded_in_function"]
        guards = view_guards(guarded_view)
        assert "_require_admin" in guards, f"Expected _require_admin in guards, got {guards}"
        
        # Test tuple-return guard
        guarded_tuple_view = app.view_functions["guarded_in_function_tuple"]
        guards = view_guards(guarded_tuple_view)
        assert "_require_org_admin_tuple" in guards, f"Expected _require_org_admin_tuple in guards, got {guards}"
        
        # Test decorator-only guard
        page_view = app.view_functions["page"]
        guards = view_guards(page_view)
        assert "login_required" in guards, f"Expected login_required in guards, got {guards}"
        
        # Test unguarded route
        home_view = app.view_functions["home"]
        guards = view_guards(home_view)
        assert guards == [], f"Expected no guards for home, got {guards}"


def test_page_controls_come_through_extends_and_include():
    inv = _build()
    page = next(r for r in inv["routes"] if r["rule"] == "/page")
    assert {Path(t).name for t in page["closure"]} == {"inv_page.html", "inv_layout.html", "inv_partial.html"}
    by_testid = {c["testid"]: c for c in inv["controls"] if c.get("testid")}
    assert "/page" in by_testid["nav-home"]["rendered_on"] and "/search" in by_testid["nav-home"]["rendered_on"]
    assert by_testid["p-btn"]["label"] == "Do thing"  # text inside a child tag is kept
    assert by_testid["nav-home"]["target"]["endpoint"] == "fx.home"


def test_links_forms_and_testids_are_all_controls():
    inv = _build()
    kinds = {}
    for c in inv["controls"]:
        kinds.setdefault(c["kind"], []).append(c)
    plain = [c for c in kinds["link"] if c["target"]["href"] == "/plain"]
    assert plain and plain[0]["label"] == "Plain link text"
    forms = {c["target"]["method"]: c for c in kinds["form"]}
    assert set(forms) == {"GET", "POST"} and forms["GET"]["target"]["endpoint"] == "fx.search"
    assert forms["GET"]["target"]["fields"] == ["q"]
    assert any(c["testid"] == "kpi-card" for c in kinds["testid"])


def test_changing_controls_are_marked_so_the_sweep_leaves_them_alone():
    inv = _build()
    by_label = {c["label"]: c for c in inv["controls"] if c["kind"] in ("button", "form", "link")}
    assert by_label["Delete it"]["mutating"] is True
    assert by_label["Search"]["mutating"] is False and by_label["Do thing"]["mutating"] is False
    post_form = next(c for c in inv["controls"] if c["kind"] == "form" and c["target"]["method"] == "POST")
    assert post_form["mutating"] is True


def test_output_is_deterministic():
    assert json.dumps(_build(), sort_keys=True) == json.dumps(_build(), sort_keys=True)
