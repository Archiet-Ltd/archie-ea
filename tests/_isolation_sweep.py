"""The isolation sweep engine: drive every identifier-bearing route as another
organisation and record what it answers.

The policy -- what counts as an identifier-bearing route, which routes are
excluded and why, which record types are shared catalogues, which leaks are
already owned elsewhere -- lives in ``tests/test_tenant_isolation_matrix.py``
and is passed in. This module is only the mechanism, so the one list of
exclusions stays in the test file where a reviewer reads it.

For each route (one rule, one HTTP method):

1. Work out which model each identifier names -- from the view's own source
   (``Solution.query.get_or_404(solution_id)``), from a helper or service the
   view hands the id to, or from what that parameter name means across the
   whole codebase.
2. Seed a row of that model, and whatever parents its foreign keys need, in
   organisation A. Every text column carries a unique marker.
3. Request the route as organisation B with A's identifiers. A LEAK is A's
   marker in B's response, or A's row changed by B's request.
4. Request the same route as organisation A, the positive control. B's refusal
   only proves something when the owner is served.

Each route runs in a transaction of its own that is always rolled back, with
two organisations and an administrator in each created inside it, so a route
that aborts the transaction cannot poison the routes after it and nothing a
route writes survives. Background threads a route would start are recorded and
not started: they would share the test's one connection from another thread
and outlive the rollback.
"""

from __future__ import annotations

import collections
import contextlib
import datetime
import inspect
import pathlib
import re
import time
import uuid

REPO = pathlib.Path(__file__).resolve().parent.parent

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
REFUSED = frozenset({403, 404, 410})

# Outcomes. Only PROVEN counts towards coverage.
PROVEN = "refused"
LEAK = "leak"
EXCLUDED = "excluded"
SHARED = "shared"
NOT_PROVEN = (
    "unfenced",      # the record type carries no organisation at all
    "not_refused",   # B was served (without A's data) where the owner was too
    "b_error",       # B's request failed with a 5xx rather than a refusal
    "unproven",      # the owner control was not served, so B's answer proves nothing
    "unresolved",    # no model could be named for the identifier
    "unseedable",    # a row of the model could not be built generically
    "error",         # the harness itself failed on this route
)


class Policy:
    """What the sweep treats as in scope. Built by the test module."""

    def __init__(self, *, non_identifier_ints, string_identifier, excluded_params,
                 excluded_endpoint_prefixes, excluded_endpoints, shared_models,
                 param_models=None):
        # Explicit overrides: {path parameter name: table name of the record it names}.
        # They win over what the codebase reading finds, so a repointed lookup (a view
        # that hands the id to a service the resolver cannot follow) is still proven.
        self.param_models = dict(param_models or {})
        self.non_identifier_ints = frozenset(non_identifier_ints)
        self.string_identifier = re.compile(string_identifier)
        self.excluded_params = dict(excluded_params)
        self.excluded_endpoint_prefixes = dict(excluded_endpoint_prefixes)
        self.excluded_endpoints = dict(excluded_endpoints)
        self.shared_models = dict(shared_models)

    def identifier_params(self, rule):
        out = []
        for name, conv in rule._converters.items():
            kind = type(conv).__name__
            if kind in ("IntegerConverter", "UUIDConverter"):
                if name not in self.non_identifier_ints:
                    out.append(name)
            elif kind in ("UnicodeConverter", "PathConverter"):
                if self.string_identifier.search(name):
                    out.append(name)
        return out

    def exclusion(self, rule, params):
        endpoint = rule.endpoint
        if endpoint in self.excluded_endpoints:
            return self.excluded_endpoints[endpoint]
        for prefix, why in self.excluded_endpoint_prefixes.items():
            if endpoint == prefix or endpoint.startswith(prefix):
                return why
        for p in params:
            if p in self.excluded_params:
                return "%s: %s" % (p, self.excluded_params[p])
        return None


class Case:
    """One route: a rule and a method, and what the sweep observed."""

    __slots__ = ("rule", "method", "params", "models", "status", "detail",
                 "b_status", "a_status", "elapsed")

    def __init__(self, rule, method, params):
        self.rule = rule
        self.method = method
        self.params = params
        self.models = {}
        self.status = None
        self.detail = ""
        self.b_status = None
        self.a_status = None
        self.elapsed = 0.0

    @property
    def key(self):
        return "%s %s" % (self.method, self.rule.rule)


# ---------------------------------------------------------------- enumeration

def enumerate_cases(app, policy):
    """Every identifier-bearing (rule, method) in the booted url_map."""
    cases, excluded = [], []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        params = policy.identifier_params(rule)
        if not params:
            continue
        why = policy.exclusion(rule, params)
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            case = Case(rule, method, params)
            if why:
                case.status, case.detail = EXCLUDED, why
                excluded.append(case)
            else:
                cases.append(case)
    return cases, excluded


# ---------------------------------------------------------------- resolution

def _lookup_patterns(param):
    p = r"(?:int\(\s*)?%s\b" % re.escape(param)
    m = r"([A-Z_]\w*)"
    return [
        r"\b%s\.query\.get(?:_or_404)?\(\s*%s" % (m, p),
        r"\b(?:db\.)?(?:session\.)?get(?:_or_404)?\(\s*%s\s*,\s*%s" % (m, p),
        r"\b%s\.query\.filter_by\(\s*id\s*=\s*%s" % (m, p),
        r"\b%s\.query\.filter\(\s*\w+\.id\s*==\s*%s" % (m, p),
        r"\bfilter\(\s*%s\.id\s*==\s*%s" % (m, p),
        r"select\(\s*%s\s*\)\s*\.\s*(?:where|filter)\(\s*\w+\.id\s*==\s*%s" % (m, p),
        r"select\(\s*%s\s*\)\s*\.\s*filter_by\(\s*id\s*=\s*%s" % (m, p),
        r"\brequire_entity(?:_json)?\(\s*%s\s*,\s*%s" % (m, p),
    ]


def mapped_classes():
    from app import db

    by_name = collections.defaultdict(list)
    for mapper in db.Model.registry.mappers:
        by_name[mapper.class_.__name__].append(mapper.class_)
    return by_name


_TABLE_MODELS = {}


def table_models():
    if not _TABLE_MODELS:
        from app import db

        for mapper in db.Model.registry.mappers:
            if mapper.inherits is None or mapper.local_table is not mapper.inherits.local_table:
                _TABLE_MODELS.setdefault(mapper.local_table.name, mapper.class_)
    return _TABLE_MODELS


def _resolve_class(name, func_globals, source, by_name):
    alias = re.search(r"import\s+(\w+)\s+as\s+%s\b" % re.escape(name), source)
    real = alias.group(1) if alias else name
    obj = func_globals.get(name) if func_globals else None
    if isinstance(obj, type) and hasattr(obj, "__table__"):
        return obj
    candidates = by_name.get(real) or []
    if candidates and len({c.__table__.name for c in candidates}) == 1:
        return candidates[0]
    return None


def codebase_param_models(by_name):
    """For each parameter name, the model it names across the whole codebase.

    Used when a view hands the id to a service the resolver cannot follow:
    ``app_id`` means ApplicationComponent in the dozens of views that look it up
    themselves. Only a dominant reading (2+ sites, 70%+ agreement) is used.
    """
    counts = collections.defaultdict(collections.Counter)
    generic = re.compile(
        r"\b([A-Z]\w*)\.query\.get(?:_or_404)?\(\s*(?:int\(\s*)?([a-z]\w*)\s*[,)]"
        r"|\bdb\.(?:session\.)?get(?:_or_404)?\(\s*([A-Z]\w*)\s*,\s*(?:int\(\s*)?([a-z]\w*)\s*[,)]"
    )
    for path in (REPO / "app").rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in generic.finditer(text):
            cls, param = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if param == "id":
                continue
            model = _resolve_class(cls, None, "", by_name)
            if model is not None:
                counts[param][model.__table__.name] += 1
    tm = table_models()
    out = {}
    for param, counter in counts.items():
        (table, n), total = counter.most_common(1)[0], sum(counter.values())
        if n >= 2 and n / total >= 0.7 and table in tm:
            out[param] = tm[table]
    return out


def _unwrap(func):
    seen = set()
    while hasattr(func, "__wrapped__") and id(func) not in seen:
        seen.add(id(func))
        func = func.__wrapped__
    return func


def _source_of(func):
    func = _unwrap(func)
    try:
        return inspect.getsource(func), getattr(func, "__globals__", {}) or {}
    except (OSError, TypeError):
        return "", getattr(func, "__globals__", {}) or {}


def _search(source, func_globals, param, by_name):
    for pattern in _lookup_patterns(param):
        m = re.search(pattern, source)
        if m:
            model = _resolve_class(m.group(1), func_globals, source, by_name)
            if model is not None:
                return model
    return None


def _follow_calls(source, func_globals, param, by_name):
    """One level into a helper or service the view passes the id to."""
    for m in re.finditer(r"\b([A-Za-z_][\w.]*)\(\s*%s\s*[,)]" % re.escape(param), source):
        target = None
        head, _, attr = m.group(1).rpartition(".")
        if head:
            owner = func_globals.get(head)
            target = getattr(owner, attr, None) if owner is not None else None
        else:
            target = func_globals.get(attr)
        if not callable(target) or isinstance(target, type):
            continue
        callee_source, callee_globals = _source_of(target)
        if not callee_source:
            continue
        try:
            first = next(iter(inspect.signature(_unwrap(target)).parameters), None)
        except (TypeError, ValueError):
            first = None
        if not first or first in ("self", "cls"):
            continue
        model = _search(callee_source, callee_globals, first, by_name)
        if model is not None:
            return model
    return None


def resolve_models(app, rule, params, by_name, param_models):
    source, func_globals = _source_of(app.view_functions[rule.endpoint])
    resolved = {}
    for p in params:
        model = (_search(source, func_globals, p, by_name)
                 or _follow_calls(source, func_globals, p, by_name)
                 or param_models.get(p))
        resolved[p] = model
    return resolved


# ---------------------------------------------------------------- seeding

class Unseedable(Exception):
    pass


def tenant_owned(model, shared_models, depth=0, _memo=None):
    """True when a row of ``model`` belongs to one organisation.

    Directly (an organization_id column), or through a required foreign key to
    a model that does. A named shared catalogue never does.
    """
    _memo = {} if _memo is None else _memo
    key = model.__table__.name
    if key in _memo:
        return _memo[key]
    table = model.__table__
    result = False
    if model.__name__ in shared_models:
        result = False
    elif "organization_id" in table.c:
        result = True
    elif depth < 3:
        tm = table_models()
        for col in table.c:
            if col.nullable:
                continue
            for fk in col.foreign_keys:
                tname = fk.column.table.name
                if tname in ("users", "organizations") or tname == key:
                    continue
                target = tm.get(tname)
                if target is not None and tenant_owned(target, shared_models, depth + 1, _memo):
                    result = True
    _memo[key] = result
    return result


def _attr_for(row, column):
    import sqlalchemy as sa

    for prop in sa.inspect(type(row)).column_attrs:
        if column in prop.columns:
            return prop.key
    return column.key


def _construct(model, kwargs):
    """Instantiate, falling back past a custom __init__ that demands arguments."""
    try:
        return model(**kwargs)
    except TypeError:
        import sqlalchemy as sa

        row = sa.orm.class_mapper(model).class_manager.new_instance()
        for k, v in kwargs.items():
            setattr(row, k, v)
        return row


class Seeder:
    """Builds minimal valid rows in organisation A, marking every text column."""

    def __init__(self, session, org_id, user_id, token, shared_models):
        self.session = session
        self.org_id = org_id
        self.user_id = user_id
        self.token = token
        self.shared_models = shared_models
        self.n = 0
        # A seeded row's primary-key value, captured the moment it is flushed.
        # A retry (tests/_isolation_sweep.py's _drive, well after the first
        # commit and the session churn _request causes between requests) may
        # call seed() again for a table already in ``context``; by then that
        # row is expired and detached, and reading an attribute off it raises
        # DetachedInstanceError. The id itself was already a plain value the
        # moment it was flushed, so keep that instead of the row.
        self.pks = {}

    def marker(self):
        self.n += 1
        return "%s%d" % (self.token, self.n)

    def _fk_value(self, col, context, depth):
        fk = next(iter(col.foreign_keys))
        ttable = fk.column.table.name
        if ttable == "organizations":
            return self.org_id
        if ttable == "users":
            return self.user_id
        if ttable in self.pks:
            return self.pks[ttable]
        target = table_models().get(ttable)
        if target is None or depth >= 3:
            if col.nullable:
                return None
            raise Unseedable("required %s -> %s" % (col.name, ttable))
        if col.nullable and not tenant_owned(target, self.shared_models):
            return None
        try:
            self.seed(target, context, depth + 1)
        except Unseedable:
            if col.nullable:
                return None
            raise
        return self.pks[target.__table__.name]

    def _value(self, col, marker, context, depth):
        import sqlalchemy as sa

        if col.name == "organization_id":
            return self.org_id
        if col.name == "adm_phase":
            return "A"
        if col.foreign_keys:
            return self._fk_value(col, context, depth)
        t = col.type
        has_default = col.default is not None or col.server_default is not None
        if isinstance(t, sa.Enum):
            if has_default:
                return None
            if t.enum_class is not None:
                return list(t.enum_class)[0]
            return t.enums[0] if t.enums else None
        if isinstance(t, sa.String):
            if has_default and not col.nullable and col.name not in ("name", "title"):
                return None
            value = marker
            if "email" in col.name:
                value = "%s@example.test" % marker
            elif "url" in col.name or col.name.endswith("_uri"):
                value = "https://example.test/%s" % marker
            length = getattr(t, "length", None)
            if length and len(value) > length:
                if length <= len(self.token):
                    return None if (col.nullable or has_default) else "x" * length
                value = value[:length]
            return value
        if has_default or col.nullable:
            return None
        if isinstance(t, sa.Boolean):
            return False
        if isinstance(t, (sa.Integer, sa.Numeric, sa.Float)):
            return 1
        if isinstance(t, sa.DateTime):
            return datetime.datetime.utcnow()
        if isinstance(t, sa.Date):
            return datetime.date.today()
        kind = type(t).__name__
        if isinstance(t, sa.JSON) or kind in ("JSONB", "JSON"):
            return {}
        if kind == "ARRAY":
            return []
        if kind in ("UUID", "Uuid"):
            return uuid.uuid4()
        if isinstance(t, sa.LargeBinary):
            return b""
        raise Unseedable("no value for required %s (%s)" % (col.name, kind))

    def seed(self, model, context, depth=0):
        """Insert one row of ``model``, reusing parents already in ``context``."""
        import sqlalchemy as sa

        table_name = model.__table__.name
        if table_name in context:
            return context[table_name]
        marker = self.marker()
        kwargs = {}
        for prop in sa.inspect(model).column_attrs:
            col = prop.columns[0]
            if not isinstance(col, sa.Column):
                continue
            if col.primary_key and not col.foreign_keys:
                try:
                    pytype = col.type.python_type
                except NotImplementedError:
                    pytype = None
                no_default = col.default is None and col.server_default is None
                if pytype is str and no_default:
                    kwargs[prop.key] = "%s-%s" % (marker, uuid.uuid4().hex[:8])
                elif pytype not in (int, str) and no_default:
                    kwargs[prop.key] = uuid.uuid4()
                continue
            value = self._value(col, marker, context, depth)
            if value is not None:
                kwargs[prop.key] = value
        try:
            with self.session.begin_nested():
                row = _construct(model, kwargs)
                self.session.add(row)
                self.session.flush()
        except Unseedable:
            raise
        except Exception as exc:  # noqa: BLE001 - any constraint means "cannot seed"
            first = (str(exc).splitlines() or [""])[0][:200]
            raise Unseedable("%s: %s" % (type(exc).__name__, first)) from exc
        context[table_name] = row
        self.pks[table_name] = getattr(row, _attr_for(row, list(model.__table__.primary_key.columns)[0]))
        return row


# ---------------------------------------------------------------- driving

def _clear_g():
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@contextlib.contextmanager
def world(app, login):
    """Two organisations and an administrator in each, in a transaction of its
    own that is always rolled back.

    Mirrors tests/conftest.py::db_session -- an outer transaction per engine,
    sessions joined to it by SAVEPOINT -- but opened and discarded per route.
    Both users hold the Administrator role and the tenant-level platform_admin
    persona, never the cross-tenant ``is_platform_admin`` flag: the widest a
    tenant's own user can be, so a refusal is the organisation boundary and not
    a role guard.
    """
    from app import db
    from app.models.organization import Organization
    from app.models.user import Permission, Role, User

    with app.app_context(), contextlib.ExitStack() as resources:
        factory = db.session.session_factory
        original_class = factory.class_
        original_options = dict(factory.kw)
        connections = {}
        for engine in dict.fromkeys(db.engines.values()):
            connection = resources.enter_context(engine.connect())
            transaction = connection.begin()
            resources.callback(lambda t=transaction: t.rollback() if t.is_active else None)
            connections[engine] = connection

        class RollbackSession(original_class):
            def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
                resolved = super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)
                return connections.get(resolved, resolved)

        try:
            db.session.remove()
            factory.class_ = RollbackSession
            db.session.configure(join_transaction_mode="create_savepoint")
            suffix = uuid.uuid4().hex[:10]
            org_a = Organization(name="Sweep A %s" % suffix, slug="sweep-a-%s" % suffix)
            org_b = Organization(name="Sweep B %s" % suffix, slug="sweep-b-%s" % suffix)
            db.session.add_all([org_a, org_b])
            db.session.flush()
            role = Role.query.filter_by(permissions=Permission.ADMINISTER).first()
            users = []
            for org, tag in ((org_a, "a"), (org_b, "b")):
                user = User(
                    email="sweep-%s-%s@example.test" % (tag, suffix),
                    first_name="Sweep", last_name=tag.upper(), organization_id=org.id,
                    confirmed=True, enterprise_role="platform_admin", role=role,
                )
                db.session.add(user)
                users.append(user)
            db.session.commit()
            ctx = {
                "org_a": org_a.id, "org_b": org_b.id,
                "user_a": users[0].id, "user_b": users[1].id,
                "token": "zq" + uuid.uuid4().hex[:6],
                "client": app.test_client(), "login": login,
            }
            db.session.remove()
            yield ctx
        finally:
            try:
                db.session.remove()
            finally:
                _clear_g()
                factory.class_ = original_class
                factory.kw.clear()
                factory.kw.update(original_options)


@contextlib.contextmanager
def no_background_threads():
    """Record background work a route starts, and do not start it."""
    import concurrent.futures
    import threading

    started = []
    original_start = threading.Thread.start
    original_submit = concurrent.futures.ThreadPoolExecutor.submit

    def _start(self):
        started.append(getattr(self, "name", "thread"))

    def _submit(self, fn, *args, **kwargs):
        started.append(getattr(fn, "__name__", "task"))
        future = concurrent.futures.Future()
        future.set_exception(RuntimeError("background work is not run during the isolation sweep"))
        return future

    threading.Thread.start = _start
    concurrent.futures.ThreadPoolExecutor.submit = _submit
    try:
        yield started
    finally:
        threading.Thread.start = original_start
        concurrent.futures.ThreadPoolExecutor.submit = original_submit


def _identity(row):
    import sqlalchemy as sa

    return type(row).__table__, sa.inspect(row).identity


def _snapshot(ident):
    """Read a seeded row straight from its table, with no tenant context."""
    import sqlalchemy as sa
    from app import db

    _clear_g()
    table, pk = ident
    if pk is None:
        return None
    cond = sa.and_(*[c == v for c, v in zip(table.primary_key.columns, pk)])
    got = db.session.execute(sa.select(table).where(cond)).mappings().first()
    result = dict(got) if got is not None else "DELETED"
    db.session.remove()
    return result


def _request(ctx, user_id, url, method, kwargs):
    """One request with a fresh session, as production gets one per request.

    The world holds one app context open, so without this a request would reuse
    the session that seeded the rows and ``Query.get()`` would answer from its
    identity map -- a leak that exists only in the harness.
    """
    from app import db

    db.session.remove()
    ctx["login"](ctx["client"], user_id)
    db.session.remove()
    resp = ctx["client"].open(url, method=method, **kwargs)
    _clear_g()
    db.session.remove()
    return resp


def _fill_url(rule, values):
    url = rule.rule
    for name, conv in rule._converters.items():
        v = values.get(name)
        if v is None:
            kind = type(conv).__name__
            v = "1" if kind == "IntegerConverter" else (
                str(uuid.UUID(int=1)) if kind == "UUIDConverter" else "x")
        url = re.sub(r"<(?:[^<>:]+:)?%s>" % re.escape(name), str(v), url)
    return url


def _to_login(resp):
    return 300 <= resp.status_code < 400 and "/login" in (resp.headers.get("Location") or "")


def _classify(case, ctx, seeded, resp_b, resp_a, changed_a, unresolved):
    a_status, b_status = resp_a.status_code, resp_b.status_code
    case.a_status = a_status
    note = "; unresolved %s" % ",".join(unresolved) if unresolved else ""
    if _to_login(resp_a) or _to_login(resp_b):
        return "unproven", "a session was sent to sign-in (A %d, B %d)" % (a_status, b_status)
    served_a = a_status < 300
    if b_status >= 500:
        if served_a or changed_a:
            return "b_error", "B answered %d where the owner was served %d" % (b_status, a_status)
        return "unproven", "B answered %d and the owner %d%s" % (b_status, a_status, note)
    if b_status in REFUSED and (served_a or changed_a or 300 <= a_status < 400):
        return PROVEN, ""
    if served_a and 300 <= b_status < 400:
        return PROVEN, "B redirected away (%d), owner served %d" % (b_status, a_status)
    if case.method in WRITE_METHODS:
        if changed_a:
            return PROVEN, "owner's write changed the row, B's identical write did not (B %d)" % b_status
        if b_status < 300 and a_status < 300:
            return "not_refused", "B's write answered %d; the owner's changed nothing either" % b_status
        return "unproven", "the write changed nothing for its owner either (A %d, B %d)%s" % (
            a_status, b_status, note)
    if served_a and seeded.search(resp_a.get_data(as_text=True)) and b_status < 300:
        return PROVEN, "owner's response carries the record, B's (%d) does not" % b_status
    if served_a and b_status < 300:
        return "not_refused", "B answered %d; the owner's %d carries no marker either" % (b_status, a_status)
    return "unproven", "owner control answered %d (B %d)%s" % (a_status, b_status, note)


_REQUIRED_RE = re.compile(
    r"\b((?:[a-z][a-z0-9_]*\s*,\s*)*(?:[a-z][a-z0-9_]*\s+and\s+)?[a-z][a-z0-9_]*)\s+(?:is|are)\s+required\b"
    r"|\b([a-z][a-z0-9_]*)\s+required\b"
)

# How many missing fields the retry (below) will add across one case's whole
# drive, and how it decides a free-text value is enough versus a real row is
# needed. Guards from the lead's ruling on the coverage-sweep brief.
MAX_RETRY_FIELDS = 3


def _missing_fields(resp):
    """Field names a 400/422 names as required, read from the response body.

    Recognises ``{"error": "X is required"}``, ``{"errors": ["X is
    required", ...]}``, ``{"message": "X required"}`` and a conjunction of
    several names in one sentence (``"X and Y are required"``,
    ``"X, Y and Z are required"``). Anything else yields no fields, which
    is the safe fallback: the route stays unproven rather than guessed at.
    """
    data = resp.get_json(silent=True)
    if not isinstance(data, dict):
        return []
    texts = [v for k in ("error", "message") if isinstance(v := data.get(k), str)]
    errs = data.get("errors")
    if isinstance(errs, list):
        texts.extend(e for e in errs if isinstance(e, str))
    fields = []
    for text in texts:
        m = _REQUIRED_RE.search(text)
        if not m:
            continue
        subject = m.group(1) or m.group(2)
        for part in re.split(r"\s*,\s*|\s+and\s+", subject):
            part = part.strip()
            if part and part not in fields:
                fields.append(part)
    return fields


def _drive(case, ctx, shared_models, param_models=None):
    from app import db

    unresolved = [p for p in case.params if case.models.get(p) is None]
    if len(unresolved) == len(case.params):
        case.status, case.detail = "unresolved", "no model for %s" % ",".join(case.params)
        return
    resolved = {p: m for p, m in case.models.items() if m is not None}
    if all(not tenant_owned(m, shared_models) for m in resolved.values()):
        shared = sorted({m.__name__ for m in resolved.values() if m.__name__ in shared_models})
        if shared and len(shared) == len({m.__name__ for m in resolved.values()}):
            case.status = SHARED
            case.detail = "; ".join("%s: %s" % (n, shared_models[n]) for n in shared)
        else:
            case.status = "unfenced"
            case.detail = "record type %s carries no organisation, directly or through a required parent" % (
                ", ".join(sorted({m.__name__ for m in resolved.values()})))
        return

    seeder = Seeder(db.session, ctx["org_a"], ctx["user_a"], ctx["token"], shared_models)
    context, values, rows = {}, {}, []
    for p in case.params:
        model = case.models.get(p)
        if model is None:
            continue
        try:
            row = seeder.seed(model, context)
        except Unseedable as exc:
            case.status, case.detail = "unseedable", "%s: %s" % (model.__name__, exc)
            return
        values[p] = getattr(row, _attr_for(row, list(model.__table__.primary_key.columns)[0]))
        rows.append(row)
    db.session.commit()
    idents = [_identity(r) for r in rows]
    db.session.remove()
    before = [_snapshot(i) for i in idents]

    url = _fill_url(case.rule, values)
    kwargs = {}
    probe = ctx["token"] + "w"
    if case.method in WRITE_METHODS:
        # Fields most write handlers accept, so a write that goes through shows
        # as a changed row rather than a no-op on an empty body.
        kwargs["json"] = {"name": probe, "title": probe, "description": probe, "notes": probe}
    seeded = re.compile(re.escape(ctx["token"]) + r"\d")

    resp_b = _request(ctx, ctx["user_b"], url, case.method, kwargs)
    case.b_status = resp_b.status_code
    leak = bool(seeded.search(resp_b.get_data(as_text=True))
                or seeded.search(resp_b.headers.get("Location") or ""))
    changed_b = case.method in WRITE_METHODS and any(
        a != b for a, b in zip([_snapshot(i) for i in idents], before))
    if leak or changed_b:
        case.status = LEAK
        case.detail = "B answered %d%s%s" % (resp_b.status_code,
                                             " with A's record" if leak else "",
                                             " and changed A's row" if changed_b else "")
        return

    resp_a = _request(ctx, ctx["user_a"], url, case.method, kwargs)
    changed_a = case.method in WRITE_METHODS and any(
        a != b for a, b in zip([_snapshot(i) for i in idents], before))

    # A missing-required-field 400/422 proves nothing either way -- B's
    # refusal is indistinguishable from the same validation wall the owner
    # just hit. Add the field the owner's own response names and ask both
    # again, so a write that is otherwise provable is not left "unproven"
    # for a reason that has nothing to do with tenant isolation. B's verdict
    # after this point is judged only on the retried request, never the one
    # above. Guarded: at most MAX_RETRY_FIELDS fields, one lookup per field,
    # free-text fields get a fixed marker, an "..._id" field gets a real row
    # of its inferred model seeded in A's own organisation -- never a bare
    # number -- and if no model can be inferred the route stays unproven,
    # with that reason recorded rather than guessed at.
    added, stop_reason = [], None
    while (case.method in WRITE_METHODS and resp_a.status_code in (400, 422)
           and len(added) < MAX_RETRY_FIELDS):
        missing = [f for f in _missing_fields(resp_a) if f not in added]
        if not missing:
            break
        for field in missing:
            if len(added) >= MAX_RETRY_FIELDS:
                break
            if field.endswith("_id"):
                # A real row, not a bare number: a made-up id would just move
                # the route from "unproven" to a false "not_refused" if it
                # resolves to nothing, or a false LEAK if it collides with
                # someone else's row. Resolved through the same codebase-wide
                # param-name reading enumerate_cases uses for URL params
                # (codebase_param_models), since a JSON field is named the
                # same way the model it refers to is everywhere else. The
                # earlier attempt at this read the seeded row's attribute
                # straight off Seeder's ``context`` after the drive's first
                # commit and several _request-driven session churns, by
                # which point that row is expired and detached --
                # DetachedInstanceError. Seeder now keeps each row's primary
                # key as a plain value (``self.pks``) the moment it is
                # flushed, so nothing here ever reads an attribute off a row
                # that might have outlived its session.
                model = (param_models or {}).get(field)
                if model is None:
                    stop_reason = "no model inferred for %r" % field
                    break
                try:
                    seeder.seed(model, context)
                except Unseedable as exc:
                    stop_reason = "%s: %s" % (field, exc)
                    break
                kwargs["json"][field] = seeder.pks[model.__table__.name]
                added.append(field)
                continue
            kwargs["json"][field] = probe
            added.append(field)
        if stop_reason:
            break
        db.session.commit()

        resp_b = _request(ctx, ctx["user_b"], url, case.method, kwargs)
        case.b_status = resp_b.status_code
        leak = bool(seeded.search(resp_b.get_data(as_text=True))
                    or seeded.search(resp_b.headers.get("Location") or ""))
        changed_b = any(a != b for a, b in zip([_snapshot(i) for i in idents], before))
        if leak or changed_b:
            case.status = LEAK
            case.detail = "B answered %d%s%s (after adding %s)" % (
                resp_b.status_code, " with A's record" if leak else "",
                " and changed A's row" if changed_b else "", ", ".join(added))
            return
        resp_a = _request(ctx, ctx["user_a"], url, case.method, kwargs)
        changed_a = any(a != b for a, b in zip([_snapshot(i) for i in idents], before))

    case.status, case.detail = _classify(case, ctx, seeded, resp_b, resp_a, changed_a, unresolved)
    note = ("retried with %s" % ", ".join(added)) if added else (
        ("retry stopped: %s" % stop_reason) if stop_reason else None)
    if note:
        case.detail = "%s; %s" % (case.detail, note) if case.detail else note


def drive(app, case, login, shared_models, param_models=None):
    started = time.time()
    try:
        with world(app, login) as ctx:
            _drive(case, ctx, shared_models, param_models)
    except Exception as exc:  # noqa: BLE001 - recorded on the case, never swallowed
        case.status = "error"
        case.detail = "%s: %s" % (type(exc).__name__, (str(exc).splitlines() or [""])[0][:200])
    finally:
        case.elapsed = time.time() - started


def run_sweep(app, login, policy, only=None):
    """Drive every identifier-bearing route (or those ``only`` accepts)."""
    by_name = mapped_classes()
    param_models = codebase_param_models(by_name)
    tables = table_models()
    param_models.update({
        param: tables[table] for param, table in policy.param_models.items() if table in tables})
    cases, excluded = enumerate_cases(app, policy)
    if only is not None:
        cases = [c for c in cases if only(c)]
        excluded = [c for c in excluded if only(c)]
    with no_background_threads():
        for case in cases:
            case.models = resolve_models(app, case.rule, case.params, by_name, param_models)
            drive(app, case, login, policy.shared_models, param_models)
    return cases, excluded


def coverage(cases):
    """(proven, denominator, percent). Shared-catalogue routes are out of scope."""
    in_scope = [c for c in cases if c.status not in (EXCLUDED, SHARED)]
    proven = sum(1 for c in in_scope if c.status == PROVEN)
    percent = round(100.0 * proven / len(in_scope), 1) if in_scope else None
    return proven, len(in_scope), percent
