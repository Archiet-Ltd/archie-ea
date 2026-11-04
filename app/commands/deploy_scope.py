"""Run the schema-deploy data steps inside an explicit platform scope.

Every tenant table has row-level security forced (migration 20261104_force_rls),
so the schema owner that runs ``scripts/database/deploy-schema.sh`` is bound by
the tenant policies like any other role. A backfill that sets neither an
organisation nor the platform scope then reads no tenant rows, writes nothing
and exits 0.

Each deploy command is wrapped here, once, in the one existing helper
(``app.jobs.tenant_safe_job.platform_scope``) with a reason that names the
command. The scope is set inside the command, not by the shell script, and it
is transaction-local, so it ends with the command.
"""
from __future__ import annotations

import functools

import click

#: Commands deploy-schema.sh runs after the structural steps. init-db,
#: schema-upgrade (which scopes each revision itself), reconcile-schema and
#: schema-status are DDL or read no tenant rows and are deliberately absent.
DEPLOY_DATA_COMMANDS = frozenset(
    {
        "migrate-connector-credentials",
        "backfill-servicenow-connector-config",
        "migrate-connector-row-secrets",
        "reconcile-admin-flags",
        "backfill-ai-chat-approval-org",
        "backfill-archimate-layer-casing",
        "backfill-layer-tenancy",
        "backfill-value-stream-tenancy",
        "backfill-principle-org",
        "backfill-initiative-org",
        "backfill-kanban-card-org",
        "backfill-saved-diagram-tenancy",
        "drop-audit-log-viewpoint-fk",
        "backfill-architect-role",
        "backfill-vendor-links",
        "backfill-decision-register-consolidation",
        "backfill-solution-risk-merge",
        "backfill-audit-trail",
        "backfill-review-queue-approvals",
        "backfill-work-package-org",
        "merge-work-package-stores",
        "project-backlog-requirements",
        "apply-entity-history-trigger",
        "backfill-entity-history",
        "backfill-application-owners",
        "apply-unified-capability-provenance-migration",
        "cutover-capability-tenancy",
        "project-capabilities",
        "backfill-capability-types",
        "backfill-capability-catalogs",
    }
)

SCOPED_MARK = "_deploy_platform_scope"


def require_platform_scope(connection, command: str) -> None:
    """Exit non-zero, plainly, when a count would be read without the platform scope.

    A check that counts rows across organisations must not pass on an empty
    read: under forced row-level security an unscoped read returns nothing.
    """
    from sqlalchemy import text

    value = connection.execute(
        text("SELECT current_setting('archie.platform_scope', true)")
    ).scalar()
    if value != "on":
        click.echo(
            f"{command}: refusing to run without the platform scope. Row-level security "
            "hides every organisation's rows from this connection, so a count here would "
            "read zero and pass on nothing.",
            err=True,
        )
        raise SystemExit(1)


def _wrap(name, callback):
    @functools.wraps(callback)
    def scoped(*args, **kwargs):
        from flask import current_app, has_app_context

        from app.jobs.tenant_safe_job import platform_scope

        # Bound at call time: the command objects are module-level and shared by
        # every app in a process, so the app is the one running the command now,
        # never the one that happened to wrap it first.
        if has_app_context():
            app = current_app._get_current_object()
        else:
            from flask.cli import ScriptInfo

            app = click.get_current_context().ensure_object(ScriptInfo).load_app()
        # The app context is entered here because the scope lives on its ``g``;
        # the command's own with_appcontext then reuses it.
        with app.app_context(), platform_scope(f"deploy backfill: {name}"):
            return callback(*args, **kwargs)

    setattr(scoped, SCOPED_MARK, True)
    return scoped


#: Defined in manage.py (registered by ``register_all_cli_commands``), not by
#: create_app, so the first pass cannot require them.
MANAGE_ONLY_COMMANDS = frozenset({"migrate-connector-credentials"})


def scope_deploy_commands(app, names=None) -> list[str]:
    """Wrap each named deploy data command once. Returns the names wrapped.

    A name that is not registered on ``app`` raises: a renamed or dropped command
    must not silently fall out of the scope.
    """
    if names is None:
        names = DEPLOY_DATA_COMMANDS - MANAGE_ONLY_COMMANDS
    wrapped = []
    missing = []
    for name in sorted(names):
        command = app.cli.commands.get(name)
        if command is None:
            missing.append(name)
            continue
        if getattr(command.callback, SCOPED_MARK, False):
            continue
        command.callback = _wrap(name, command.callback)
        wrapped.append(name)
    if missing:
        raise RuntimeError(
            "deploy data command(s) not registered, so they would run unscoped: "
            + ", ".join(missing)
        )
    return wrapped
