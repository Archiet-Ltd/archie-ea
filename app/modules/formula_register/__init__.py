"""R1-B34: Formula register blueprint — one place to view and version a
composite score's formula (TB-0135)."""
from .routes import formula_register_bp


def register(app):
    app.register_blueprint(formula_register_bp, url_prefix="/admin/formula-register")
