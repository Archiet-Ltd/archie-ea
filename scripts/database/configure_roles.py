"""Idempotently separate PostgreSQL deployment ownership from app runtime.

This script is deliberately standalone: one-shot deployment containers run it
before and after schema deployment with the PostgreSQL bootstrap credential,
while web and worker containers never receive that credential or the schema-
owner credential.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Iterable

import psycopg2
from psycopg2 import sql

from scripts.database.transformation_privilege_policy import (
    PROTECTED_RUNTIME_TABLE_PRIVILEGES,
    PROTECTED_RUNTIME_UPDATE_COLUMNS,
    RUNTIME_EXECUTE_FUNCTIONS,
    RUNTIME_NO_ACCESS_TABLES,
    RUNTIME_SEQUENCE_ALLOWLIST,
)


DEPLOY_ROLE = "archie_deploy"
RUNTIME_ROLE = "archie_runtime"
SESSION_TERMINATION_TIMEOUT_MS = 5_000
SESSION_TERMINATION_POLL_SECONDS = 5.0
SESSION_TERMINATION_POLL_INTERVAL_SECONDS = 0.05


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _ensure_role(
    cursor, *, role: str, password: str, can_login: bool
) -> None:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if cursor.fetchone() is None:
        cursor.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
    login = sql.SQL("LOGIN") if can_login else sql.SQL("NOLOGIN")
    cursor.execute(
        sql.SQL(
            "ALTER ROLE {} WITH {} PASSWORD %s NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(role), login),
        (password,),
    )


def _runtime_membership_snapshot(
    cursor, *, runtime_role: str
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return outbound, direct inbound, and transitive inbound role names."""
    cursor.execute(
        """
        SELECT granted.rolname
        FROM pg_auth_members membership
        JOIN pg_roles granted ON granted.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        WHERE member.rolname = %s
        ORDER BY granted.rolname
        """,
        (runtime_role,),
    )
    outbound_roles = tuple(row[0] for row in cursor.fetchall())
    cursor.execute(
        """
        SELECT member.rolname
        FROM pg_auth_members membership
        JOIN pg_roles granted ON granted.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        WHERE granted.rolname = %s
        ORDER BY member.rolname
        """,
        (runtime_role,),
    )
    direct_inbound_roles = tuple(row[0] for row in cursor.fetchall())
    cursor.execute(
        """
        WITH RECURSIVE inbound_roles(role_oid, role_name) AS (
            SELECT member.oid, member.rolname
            FROM pg_auth_members membership
            JOIN pg_roles granted ON granted.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE granted.rolname = %s
          UNION
            SELECT member.oid, member.rolname
            FROM pg_auth_members membership
            JOIN inbound_roles granted ON granted.role_oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
        )
        SELECT role_name
        FROM inbound_roles
        ORDER BY role_name
        """,
        (runtime_role,),
    )
    transitive_inbound_roles = tuple(row[0] for row in cursor.fetchall())
    return outbound_roles, direct_inbound_roles, transitive_inbound_roles


def _pg16_membership_grant_snapshot(
    cursor,
) -> tuple[tuple[str, str, str, bool, bool, bool], ...]:
    """Return the exact PG16 membership graph, or nothing on older servers."""
    if cursor.connection.server_version < 160_000:
        return ()
    cursor.execute(
        """
        SELECT granted.rolname,
               member.rolname,
               grantor.rolname,
               membership.admin_option,
               membership.inherit_option,
               membership.set_option
        FROM pg_auth_members membership
        JOIN pg_roles granted ON granted.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles grantor ON grantor.oid = membership.grantor
        ORDER BY granted.rolname, member.rolname, grantor.rolname
        """
    )
    return tuple(cursor.fetchall())


def _removed_membership_grants(
    *,
    before: tuple[tuple[str, str, str, bool, bool, bool], ...],
    after: tuple[tuple[str, str, str, bool, bool, bool], ...],
    runtime_role: str,
) -> tuple[tuple[str, str, bool, bool, bool], ...]:
    """Return non-runtime membership states actually removed by CASCADE."""
    surviving_states = {
        (granted_role, member_role, admin, inherit, can_set)
        for granted_role, member_role, _grantor, admin, inherit, can_set in after
    }
    removed_options_by_pair: dict[tuple[str, str], tuple[bool, bool, bool]] = {}
    for granted_role, member_role, _grantor, admin, inherit, can_set in before:
        membership_state = (
            granted_role,
            member_role,
            admin,
            inherit,
            can_set,
        )
        if (
            granted_role == runtime_role
            or member_role == runtime_role
            or membership_state in surviving_states
        ):
            continue
        pair = (granted_role, member_role)
        prior = removed_options_by_pair.get(pair, (False, False, False))
        removed_options_by_pair[pair] = (
            prior[0] or admin,
            prior[1] or inherit,
            prior[2] or can_set,
        )
    return tuple(
        (granted_role, member_role, admin, inherit, can_set)
        for (granted_role, member_role), (
            admin,
            inherit,
            can_set,
        ) in removed_options_by_pair.items()
    )


def _rehome_dependent_membership_grants(
    cursor,
    *,
    dependent_grants: tuple[tuple[str, str, bool, bool, bool], ...],
) -> tuple[tuple[str, str, bool, bool, bool], ...]:
    """Consolidate removed options into the surviving current-user grant."""
    consolidated_grants = []
    for granted_role, member_role, admin, inherit, can_set in dependent_grants:
        cursor.execute(
            """
            SELECT membership.admin_option,
                   membership.inherit_option,
                   membership.set_option
            FROM pg_auth_members membership
            JOIN pg_roles granted ON granted.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            JOIN pg_roles grantor ON grantor.oid = membership.grantor
            WHERE granted.rolname = %s
              AND member.rolname = %s
              AND grantor.rolname = current_user
            """,
            (granted_role, member_role),
        )
        surviving_current_user_options = cursor.fetchone()
        if surviving_current_user_options is not None:
            admin = admin or surviving_current_user_options[0]
            inherit = inherit or surviving_current_user_options[1]
            can_set = can_set or surviving_current_user_options[2]
        cursor.execute(
            sql.SQL(
                "GRANT {} TO {} WITH ADMIN {}, INHERIT {}, SET {} "
                "GRANTED BY CURRENT_USER"
            ).format(
                sql.Identifier(granted_role),
                sql.Identifier(member_role),
                sql.SQL("TRUE" if admin else "FALSE"),
                sql.SQL("TRUE" if inherit else "FALSE"),
                sql.SQL("TRUE" if can_set else "FALSE"),
            )
        )
        consolidated_grants.append(
            (granted_role, member_role, admin, inherit, can_set)
        )
    return tuple(consolidated_grants)


def _verify_rehomed_membership_grants(
    cursor,
    *,
    dependent_grants: tuple[tuple[str, str, bool, bool, bool], ...],
) -> None:
    for granted_role, member_role, admin, inherit, can_set in dependent_grants:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_auth_members membership
                JOIN pg_roles granted ON granted.oid = membership.roleid
                JOIN pg_roles member ON member.oid = membership.member
                JOIN pg_roles grantor ON grantor.oid = membership.grantor
                WHERE granted.rolname = %s
                  AND member.rolname = %s
                  AND membership.admin_option = %s
                  AND membership.inherit_option = %s
                  AND membership.set_option = %s
                  AND grantor.rolname = current_user
            )
            """,
            (
                granted_role,
                member_role,
                admin,
                inherit,
                can_set,
            ),
        )
        if cursor.fetchone() != (True,):
            raise RuntimeError(
                "runtime membership dependency was lost during cleanup: "
                f"grant={granted_role!r}, member={member_role!r}"
            )


def _strip_runtime_memberships(cursor, *, runtime_role: str) -> tuple[str, ...]:
    """Remove every inherited/SET ROLE path into or out of runtime."""
    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"archie_runtime_membership_cleanup:{runtime_role}",),
    )
    # Advisory locks serialize every Archie bootstrap and recovery path.  The
    # catalog lock also blocks arbitrary role GRANT/REVOKE writers that do not
    # participate in Archie's advisory-lock convention, keeping the two graph
    # snapshots attributable to this transaction's CASCADE operations.
    cursor.execute(
        "LOCK TABLE pg_catalog.pg_auth_members IN SHARE ROW EXCLUSIVE MODE"
    )
    outbound, direct_inbound, transitive_inbound = _runtime_membership_snapshot(
        cursor,
        runtime_role=runtime_role,
    )
    membership_grants_before = _pg16_membership_grant_snapshot(cursor)
    for granted_role in outbound:
        cursor.execute(
            sql.SQL("REVOKE {} FROM {} CASCADE").format(
                sql.Identifier(granted_role),
                sql.Identifier(runtime_role),
            )
        )
    for member_role in direct_inbound:
        cursor.execute(
            sql.SQL("REVOKE {} FROM {} CASCADE").format(
                sql.Identifier(runtime_role),
                sql.Identifier(member_role),
            )
        )
    membership_grants_after = _pg16_membership_grant_snapshot(cursor)
    dependent_grants = _removed_membership_grants(
        before=membership_grants_before,
        after=membership_grants_after,
        runtime_role=runtime_role,
    )
    consolidated_grants = _rehome_dependent_membership_grants(
        cursor,
        dependent_grants=dependent_grants,
    )
    _verify_rehomed_membership_grants(
        cursor,
        dependent_grants=consolidated_grants,
    )
    remaining_outbound, remaining_inbound, _ = _runtime_membership_snapshot(
        cursor,
        runtime_role=runtime_role,
    )
    if remaining_outbound or remaining_inbound:
        raise RuntimeError(
            "runtime membership fence verification failed: "
            f"outbound={remaining_outbound!r}, inbound={remaining_inbound!r}"
        )
    return transitive_inbound


def _commit_runtime_login_state(
    connection,
    *,
    runtime_role: str,
    runtime_password: str,
    can_login: bool,
) -> None:
    """Commit one transaction containing only the runtime-role state change."""
    with connection.cursor() as cursor:
        _ensure_role(
            cursor,
            role=runtime_role,
            password=runtime_password,
            can_login=can_login,
        )
    connection.commit()


def _verify_runtime_login_state(
    *, admin_url: str, runtime_role: str, expected_can_login: bool
) -> None:
    """Prove a role state from a new cluster connection and transaction."""
    with psycopg2.connect(_psycopg_url(admin_url)) as verification:
        with verification.cursor() as cursor:
            cursor.execute(
                "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
                "rolinherit, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (runtime_role,),
            )
            row = cursor.fetchone()
    expected_state = (expected_can_login, False, False, False, False, False, False)
    if row != expected_state:
        expected = "LOGIN" if expected_can_login else "NOLOGIN"
        raise RuntimeError(
            f"runtime role {runtime_role!r} did not durably reach the "
            f"restricted {expected} state; observed {row!r}"
        )


def _recover_runtime_nologin(
    *, admin_url: str, runtime_role: str, runtime_password: str
) -> None:
    """Durably fence login, memberships, and already-connected sessions."""
    with psycopg2.connect(_psycopg_url(admin_url)) as recovery:
        with recovery.cursor() as cursor:
            _ensure_role(
                cursor,
                role=runtime_role,
                password=runtime_password,
                can_login=False,
            )
        recovery.commit()
    _verify_runtime_login_state(
        admin_url=admin_url,
        runtime_role=runtime_role,
        expected_can_login=False,
    )
    with psycopg2.connect(_psycopg_url(admin_url)) as membership_recovery:
        with membership_recovery.cursor() as cursor:
            inbound_role_names = _strip_runtime_memberships(
                cursor,
                runtime_role=runtime_role,
            )
        membership_recovery.commit()
    _verify_runtime_memberships_cleared(
        admin_url=admin_url,
        runtime_role=runtime_role,
    )
    _terminate_runtime_capable_sessions(
        admin_url=admin_url,
        runtime_role=runtime_role,
        inbound_role_names=inbound_role_names,
    )


def _verify_runtime_memberships_cleared(
    *, admin_url: str, runtime_role: str
) -> None:
    with psycopg2.connect(_psycopg_url(admin_url)) as verification:
        with verification.cursor() as cursor:
            outbound, direct_inbound, _ = _runtime_membership_snapshot(
                cursor,
                runtime_role=runtime_role,
            )
    if outbound or direct_inbound:
        raise RuntimeError(
            "runtime membership cleanup was not durable: "
            f"outbound={outbound!r}, inbound={direct_inbound!r}"
        )


def _runtime_capable_session_pids(
    cursor,
    *,
    role_names: tuple[str, ...],
) -> tuple[int, ...]:
    cursor.execute(
        """
        SELECT activity.pid
        FROM pg_stat_activity activity
        WHERE activity.usename = ANY(%s)
          AND activity.pid <> pg_backend_pid()
        ORDER BY activity.pid
        """,
        (list(role_names),),
    )
    return tuple(row[0] for row in cursor.fetchall())


def _runtime_capable_session_pids_from_fresh_connection(
    *, admin_url: str, role_names: tuple[str, ...]
) -> tuple[int, ...]:
    with psycopg2.connect(_psycopg_url(admin_url)) as proof:
        proof.set_session(isolation_level="READ COMMITTED")
        with proof.cursor() as cursor:
            return _runtime_capable_session_pids(
                cursor,
                role_names=role_names,
            )


def _terminate_runtime_capable_sessions(
    *,
    admin_url: str,
    runtime_role: str,
    inbound_role_names: tuple[str, ...],
) -> None:
    """Terminate and prove absence of every session able to use runtime ACLs."""
    role_names = tuple(dict.fromkeys((runtime_role, *inbound_role_names)))
    with psycopg2.connect(_psycopg_url(admin_url)) as termination:
        termination.set_session(isolation_level="READ COMMITTED")
        with termination.cursor() as cursor:
            pids = _runtime_capable_session_pids(
                cursor,
                role_names=role_names,
            )
            cursor.execute(
                "SELECT to_regprocedure("
                "'pg_catalog.pg_terminate_backend(integer,bigint)') IS NOT NULL"
            )
            supports_timeout = cursor.fetchone()[0]
            termination_results: tuple[tuple[int, bool], ...] = ()
            if pids:
                if supports_timeout:
                    cursor.execute(
                        """
                        SELECT activity.pid,
                               pg_catalog.pg_terminate_backend(activity.pid, %s)
                        FROM pg_stat_activity activity
                        WHERE activity.pid = ANY(%s)
                          AND activity.usename = ANY(%s)
                        ORDER BY activity.pid
                        """,
                        (
                            SESSION_TERMINATION_TIMEOUT_MS,
                            list(pids),
                            list(role_names),
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT activity.pid,
                               pg_catalog.pg_terminate_backend(activity.pid)
                        FROM pg_stat_activity activity
                        WHERE activity.pid = ANY(%s)
                          AND activity.usename = ANY(%s)
                        ORDER BY activity.pid
                        """,
                        (list(pids), list(role_names)),
                    )
                termination_results = tuple(cursor.fetchall())

    # A `pg_terminate_backend(pid)` result of False here is not itself proof
    # of failure: Postgres returns False both when signalling genuinely
    # failed AND when the target backend had already disconnected on its own
    # between the SELECT that found `pids` and this UPDATE-like call — an
    # ordinary race under real traffic (e.g. a gunicorn worker's pooled
    # connection closing normally), not a defect. The poll loop directly
    # below is the actual, sufficient proof: it independently re-queries
    # pg_stat_activity from a fresh connection and only returns once no
    # runtime-capable session remains, raising its own clear RuntimeError if
    # that never becomes true within the deadline. Treating a False here as
    # immediately fatal — as this function did until 18 Sep 2026 — aborted a
    # real production deploy on exactly this benign race (one already-gone
    # PID out of eleven), even though the property this function exists to
    # guarantee was never actually violated.
    already_gone = tuple(
        pid for pid, terminated in termination_results if terminated is not True
    )
    if already_gone:
        print(
            f"  (terminate-sessions: {len(already_gone)} pid(s) already "
            f"disconnected before signal, not treated as fatal: {already_gone!r})"
        )

    deadline = time.monotonic() + SESSION_TERMINATION_POLL_SECONDS
    while True:
        remaining = _runtime_capable_session_pids_from_fresh_connection(
            admin_url=admin_url,
            role_names=role_names,
        )
        if not remaining:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "runtime-capable session termination could not be proven; "
                f"remaining pids={remaining!r}"
            )
        time.sleep(SESSION_TERMINATION_POLL_INTERVAL_SECONDS)


def _transfer_public_objects(
    cursor, *, deploy_role: str, runtime_role: str
) -> None:
    cursor.execute(
        """
        SELECT c.relname, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
          AND (
              c.relkind <> 'S'
              OR NOT EXISTS (
                  SELECT 1 FROM pg_depend ownership
                  WHERE ownership.classid = 'pg_class'::regclass
                    AND ownership.objid = c.oid
                    AND ownership.refclassid = 'pg_class'::regclass
                    AND ownership.deptype IN ('a', 'i')
              )
          )
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = c.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY CASE WHEN c.relkind = 'S' THEN 2 ELSE 1 END, c.relkind, c.relname
        """
    )
    statements = {
        "r": "ALTER TABLE {}.{} OWNER TO {}",
        "p": "ALTER TABLE {}.{} OWNER TO {}",
        "v": "ALTER VIEW {}.{} OWNER TO {}",
        "m": "ALTER MATERIALIZED VIEW {}.{} OWNER TO {}",
        "S": "ALTER SEQUENCE {}.{} OWNER TO {}",
        "f": "ALTER FOREIGN TABLE {}.{} OWNER TO {}",
    }
    for name, kind in cursor.fetchall():
        cursor.execute(
            sql.SQL(statements[kind]).format(
                sql.Identifier("public"),
                sql.Identifier(name),
                sql.Identifier(deploy_role),
            )
        )

    type_statements = {
        "e": "ALTER TYPE {}.{} OWNER TO {}",
        "r": "ALTER TYPE {}.{} OWNER TO {}",
        "c": "ALTER TYPE {}.{} OWNER TO {}",
        "d": "ALTER DOMAIN {}.{} OWNER TO {}",
        "m": "ALTER TYPE {}.{} OWNER TO {}",
    }
    for name, kind in _public_user_types(cursor):
        cursor.execute(
            sql.SQL(type_statements[kind]).format(
                sql.Identifier("public"),
                sql.Identifier(name),
                sql.Identifier(deploy_role),
            )
        )

    cursor.execute(
        """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_proc'::regclass
                AND dependency.objid = p.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY p.proname, p.oid
        """
    )
    for name, identity_arguments in cursor.fetchall():
        function = sql.SQL("{}.{}({})").format(
            sql.Identifier("public"),
            sql.Identifier(name),
            sql.SQL(identity_arguments),
        )
        cursor.execute(
            sql.SQL("ALTER FUNCTION {} OWNER TO {}").format(
                function, sql.Identifier(deploy_role)
            )
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON FUNCTION {} FROM PUBLIC").format(
                function
            )
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON FUNCTION {} FROM {}").format(
                function, sql.Identifier(runtime_role)
            )
        )


def _public_user_types(cursor) -> tuple[tuple[str, str], ...]:
    """Enum, domain, range, multirange and standalone composite types made in public.

    Array types follow their element type, table row types follow their table,
    and extension types belong to the extension. A range's multirange does not
    follow the range to a new owner, so multirange types are listed and handed
    over on their own.
    """
    cursor.execute(
        """
        SELECT t.typname, t.typtype
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        LEFT JOIN pg_class c ON c.oid = t.typrelid
        WHERE n.nspname = 'public'
          AND (
              t.typtype IN ('e', 'd', 'r', 'm')
              OR (t.typtype = 'c' AND c.relkind = 'c')
          )
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_type'::regclass
                AND dependency.objid = t.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY t.typname
        """
    )
    return tuple(cursor.fetchall())


def _verify_type_owners(cursor, *, database_name: str, deploy_role: str) -> None:
    for name, _kind in _public_user_types(cursor):
        cursor.execute(
            """
            SELECT pg_get_userbyid(t.typowner)
            FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname = 'public' AND t.typname = %s
            """,
            (name,),
        )
        row = cursor.fetchone()
        if row is None:
            raise _acl_verification_failure(
                database_name=database_name,
                object_kind="type",
                object_name=f"public.{name}",
                detail="disappeared while owners were being checked",
            )
        owner = row[0]
        if owner != deploy_role:
            raise _acl_verification_failure(
                database_name=database_name,
                object_kind="type",
                object_name=f"public.{name}",
                detail=f"is owned by {owner!r}, expected {deploy_role!r}",
            )


def _public_relations(cursor) -> tuple[tuple[str, str], ...]:
    cursor.execute(
        """
        SELECT relation.relname, relation.relkind
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY relation.relname
        """
    )
    return tuple(cursor.fetchall())


def _runtime_table_privileges(table_name: str, relation_kind: str) -> tuple[str, ...]:
    if table_name in RUNTIME_NO_ACCESS_TABLES:
        return ()
    protected = PROTECTED_RUNTIME_TABLE_PRIVILEGES.get(table_name)
    if protected is not None:
        return protected
    if relation_kind in {"r", "p", "f"}:
        return ("SELECT", "INSERT", "UPDATE", "DELETE")
    return ("SELECT",)


def _relation_columns(cursor, *, table_name: str) -> tuple[str, ...]:
    cursor.execute(
        """
        SELECT attribute.attname
        FROM pg_attribute attribute
        JOIN pg_class relation ON relation.oid = attribute.attrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relname = %s
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        ORDER BY attribute.attnum
        """,
        (table_name,),
    )
    return tuple(row[0] for row in cursor.fetchall())


def _configure_table_privileges(cursor, *, runtime_role: str) -> None:
    runtime = sql.Identifier(runtime_role)
    for table_name, relation_kind in _public_relations(cursor):
        table = sql.SQL("{}.{}").format(
            sql.Identifier("public"), sql.Identifier(table_name)
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM PUBLIC").format(table)
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM {}").format(
                table, runtime
            )
        )
        columns = _relation_columns(cursor, table_name=table_name)
        if columns:
            cursor.execute(
                sql.SQL(
                    "REVOKE ALL PRIVILEGES ({}) ON TABLE {} FROM PUBLIC"
                ).format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    table,
                )
            )
            cursor.execute(
                sql.SQL(
                    "REVOKE ALL PRIVILEGES ({}) ON TABLE {} FROM {}"
                ).format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    table,
                    runtime,
                )
            )
        privileges = _runtime_table_privileges(table_name, relation_kind)
        if privileges:
            cursor.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(map(sql.SQL, privileges)),
                    table,
                    runtime,
                )
            )
        update_columns = PROTECTED_RUNTIME_UPDATE_COLUMNS.get(table_name, ())
        if update_columns:
            cursor.execute(
                sql.SQL("GRANT UPDATE ({}) ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, update_columns)),
                    table,
                    runtime,
                )
            )


def _public_sequences(
    cursor,
) -> tuple[tuple[int, str, str | None, str | None, str | None, bool], ...]:
    cursor.execute(
        """
        SELECT DISTINCT
               sequence.oid,
               sequence.relname,
               owned_table.relname,
               owned_table.relkind,
               owned_column.attname,
               CASE
                   WHEN ownership.deptype = 'i'
                       THEN owned_column.attidentity IN ('a', 'd')
                   WHEN ownership.deptype = 'a'
                       THEN EXISTS (
                           SELECT 1
                           FROM pg_attrdef default_value
                           JOIN pg_depend default_dependency
                             ON default_dependency.classid = 'pg_attrdef'::regclass
                            AND default_dependency.objid = default_value.oid
                            AND default_dependency.refclassid = 'pg_class'::regclass
                            AND default_dependency.refobjid = sequence.oid
                           WHERE default_value.adrelid = owned_table.oid
                             AND default_value.adnum = owned_column.attnum
                       )
                   ELSE false
               END AS generated_value_requires_sequence
        FROM pg_class sequence
        JOIN pg_namespace namespace ON namespace.oid = sequence.relnamespace
        LEFT JOIN pg_depend ownership
          ON ownership.classid = 'pg_class'::regclass
         AND ownership.objid = sequence.oid
         AND ownership.refclassid = 'pg_class'::regclass
         AND ownership.deptype IN ('a', 'i')
        LEFT JOIN pg_class owned_table ON owned_table.oid = ownership.refobjid
        LEFT JOIN pg_attribute owned_column
          ON owned_column.attrelid = ownership.refobjid
         AND owned_column.attnum = ownership.refobjsubid
         AND NOT owned_column.attisdropped
        WHERE namespace.nspname = 'public'
          AND sequence.relkind = 'S'
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = sequence.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY sequence.relname, owned_table.relname, owned_column.attname
        """
    )
    return tuple(cursor.fetchall())


def _runtime_sequence_allowed(
    *,
    sequence_name: str,
    owned_table_name: str | None,
    owned_table_kind: str | None,
    owned_column_name: str | None,
    generated_value_requires_sequence: bool,
) -> bool:
    if sequence_name in RUNTIME_SEQUENCE_ALLOWLIST:
        return True
    if (
        owned_table_name is None
        or owned_table_kind is None
        or owned_column_name is None
        or not generated_value_requires_sequence
    ):
        return False
    return "INSERT" in _runtime_table_privileges(
        owned_table_name,
        owned_table_kind,
    )


def _configure_sequence_privileges(cursor, *, runtime_role: str) -> None:
    runtime = sql.Identifier(runtime_role)
    for (
        _sequence_oid,
        sequence_name,
        owned_table_name,
        owned_table_kind,
        owned_column_name,
        generated_value_requires_sequence,
    ) in _public_sequences(cursor):
        sequence = sql.SQL("{}.{}").format(
            sql.Identifier("public"), sql.Identifier(sequence_name)
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SEQUENCE {} FROM PUBLIC").format(
                sequence
            )
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SEQUENCE {} FROM {}").format(
                sequence, runtime
            )
        )
        if not _runtime_sequence_allowed(
            sequence_name=sequence_name,
            owned_table_name=owned_table_name,
            owned_table_kind=owned_table_kind,
            owned_column_name=owned_column_name,
            generated_value_requires_sequence=generated_value_requires_sequence,
        ):
            continue
        cursor.execute(
            sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(
                sequence, runtime
            )
        )


def _configure_function_privileges(cursor, *, runtime_role: str) -> None:
    runtime = sql.Identifier(runtime_role)
    for function_name, identity_arguments in RUNTIME_EXECUTE_FUNCTIONS:
        cursor.execute(
            "SELECT to_regprocedure(%s)",
            (f"public.{function_name}({identity_arguments})",),
        )
        if cursor.fetchone()[0] is None:
            continue
        function = sql.SQL("{}.{}({})").format(
            sql.Identifier("public"),
            sql.Identifier(function_name),
            sql.SQL(identity_arguments),
        )
        cursor.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                function, runtime
            )
        )


def _configure_default_privileges(
    cursor, *, deploy_role: str, runtime_role: str
) -> None:
    deploy = sql.Identifier(deploy_role)
    runtime = sql.Identifier(runtime_role)
    for object_type in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} "
                "REVOKE ALL PRIVILEGES ON {} FROM PUBLIC"
            ).format(deploy, sql.SQL(object_type))
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} "
                "REVOKE ALL PRIVILEGES ON {} FROM {}"
            ).format(deploy, sql.SQL(object_type), runtime)
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "REVOKE ALL PRIVILEGES ON {} FROM PUBLIC"
            ).format(deploy, sql.SQL(object_type))
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "REVOKE ALL PRIVILEGES ON {} FROM {}"
            ).format(deploy, sql.SQL(object_type), runtime)
        )


def _acl_verification_failure(
    *, database_name: str, object_kind: str, object_name: str, detail: str
) -> RuntimeError:
    return RuntimeError(
        "database ACL verification failed for "
        f"{database_name}: {object_kind} {object_name!r} {detail}"
    )


def _verify_table_privileges(
    cursor, *, database_name: str, deploy_role: str, runtime_role: str
) -> None:
    cursor.execute(
        """
        SELECT relation.relname,
               relation.relkind,
               pg_get_userbyid(relation.relowner),
               has_table_privilege(%s, relation.oid, 'SELECT'),
               has_table_privilege(%s, relation.oid, 'INSERT'),
               has_table_privilege(%s, relation.oid, 'UPDATE'),
               has_table_privilege(%s, relation.oid, 'DELETE'),
               has_table_privilege(%s, relation.oid, 'TRUNCATE'),
               has_table_privilege(%s, relation.oid, 'REFERENCES'),
               has_table_privilege(%s, relation.oid, 'TRIGGER'),
               NOT EXISTS (
                   SELECT 1
                   FROM aclexplode(relation.relacl) relation_acl
                   WHERE relation_acl.grantee = 0
               ) AS public_acl_is_empty
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY relation.relname
        """,
        (runtime_role,) * 7,
    )
    relation_rows = tuple(cursor.fetchall())
    for table_name, relation_kind, owner_name, *state in relation_rows:
        privileges = _runtime_table_privileges(table_name, relation_kind)
        expected = (
            "SELECT" in privileges,
            "INSERT" in privileges,
            "UPDATE" in privileges,
            "DELETE" in privileges,
            "TRUNCATE" in privileges,
            "REFERENCES" in privileges,
            "TRIGGER" in privileges,
            True,
        )
        if owner_name != deploy_role or tuple(state) != expected:
            raise _acl_verification_failure(
                database_name=database_name,
                object_kind="table",
                object_name=table_name,
                detail=(
                    f"has owner={owner_name!r}, state={tuple(state)!r}; "
                    f"expected owner={deploy_role!r}, state={expected!r}"
                ),
            )

    cursor.execute(
        """
        SELECT relation.relname,
               relation.relkind,
               attribute.attname,
               has_column_privilege(%s, relation.oid, attribute.attnum, 'SELECT'),
               has_column_privilege(%s, relation.oid, attribute.attnum, 'INSERT'),
               has_column_privilege(%s, relation.oid, attribute.attnum, 'UPDATE'),
               has_column_privilege(%s, relation.oid, attribute.attnum, 'REFERENCES'),
               NOT EXISTS (
                   SELECT 1
                   FROM aclexplode(attribute.attacl) column_acl
                   WHERE column_acl.grantee = 0
               ) AS public_acl_is_empty
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_attribute attribute ON attribute.attrelid = relation.oid
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_class'::regclass
                AND dependency.objid = relation.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY relation.relname, attribute.attnum
        """,
        (runtime_role,) * 4,
    )
    for table_name, relation_kind, column_name, *state in cursor.fetchall():
        privileges = _runtime_table_privileges(table_name, relation_kind)
        update_columns = PROTECTED_RUNTIME_UPDATE_COLUMNS.get(table_name, ())
        expected = (
            "SELECT" in privileges,
            "INSERT" in privileges,
            "UPDATE" in privileges or column_name in update_columns,
            "REFERENCES" in privileges,
            True,
        )
        if tuple(state) != expected:
            raise _acl_verification_failure(
                database_name=database_name,
                object_kind="column",
                object_name=f"{table_name}.{column_name}",
                detail=f"has state {tuple(state)!r}, expected {expected!r}",
            )


def _verify_sequence_privileges(
    cursor, *, database_name: str, deploy_role: str, runtime_role: str
) -> None:
    for (
        sequence_oid,
        sequence_name,
        owned_table_name,
        owned_table_kind,
        owned_column_name,
        generated_value_requires_sequence,
    ) in _public_sequences(cursor):
        allowed = _runtime_sequence_allowed(
            sequence_name=sequence_name,
            owned_table_name=owned_table_name,
            owned_table_kind=owned_table_kind,
            owned_column_name=owned_column_name,
            generated_value_requires_sequence=generated_value_requires_sequence,
        )
        cursor.execute(
            """
            SELECT pg_get_userbyid(sequence.relowner),
                   has_sequence_privilege(%s, %s, 'USAGE'),
                   has_sequence_privilege(%s, %s, 'SELECT'),
                   has_sequence_privilege(%s, %s, 'UPDATE'),
                   NOT EXISTS (
                       SELECT 1
                       FROM aclexplode(sequence.relacl) sequence_acl
                       WHERE sequence_acl.grantee = 0
                   ) AS public_acl_is_empty
            FROM pg_class sequence
            WHERE sequence.oid = %s
            """,
            (
                runtime_role,
                sequence_oid,
                runtime_role,
                sequence_oid,
                runtime_role,
                sequence_oid,
                sequence_oid,
            ),
        )
        state = cursor.fetchone()
        expected = (deploy_role, allowed, allowed, False, True)
        if state != expected:
            raise _acl_verification_failure(
                database_name=database_name,
                object_kind="sequence",
                object_name=sequence_name,
                detail=f"has state {state!r}, expected {expected!r}",
            )


def _verify_function_privileges(
    cursor, *, database_name: str, deploy_role: str, runtime_role: str
) -> None:
    allowed_oids = set()
    for function_name, identity_arguments in RUNTIME_EXECUTE_FUNCTIONS:
        cursor.execute(
            "SELECT to_regprocedure(%s)::oid",
            (f"public.{function_name}({identity_arguments})",),
        )
        function_oid = cursor.fetchone()[0]
        if function_oid is not None:
            allowed_oids.add(function_oid)
    cursor.execute(
        """
        SELECT function.oid,
               function.proname,
               pg_get_function_identity_arguments(function.oid),
               pg_get_userbyid(function.proowner),
               has_function_privilege(%s, function.oid, 'EXECUTE'),
               NOT EXISTS (
                   SELECT 1
                   FROM aclexplode(
                       COALESCE(
                           function.proacl,
                           acldefault('f', function.proowner)
                       )
                   ) function_acl
                   WHERE function_acl.grantee = 0
               ) AS public_acl_is_empty
        FROM pg_proc function
        JOIN pg_namespace namespace ON namespace.oid = function.pronamespace
        WHERE namespace.nspname = 'public'
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend dependency
              WHERE dependency.classid = 'pg_proc'::regclass
                AND dependency.objid = function.oid
                AND dependency.deptype = 'e'
          )
        ORDER BY function.proname, function.oid
        """,
        (runtime_role,),
    )
    for (
        function_oid,
        function_name,
        arguments,
        owner_name,
        can_execute,
        public_clean,
    ) in cursor.fetchall():
        expected = function_oid in allowed_oids
        if owner_name != deploy_role or can_execute != expected or not public_clean:
            raise _acl_verification_failure(
                database_name=database_name,
                object_kind="function",
                object_name=f"{function_name}({arguments})",
                detail=(
                    f"has owner={owner_name!r}, EXECUTE={can_execute!r}, "
                    f"public_clean={public_clean!r}; expected owner={deploy_role!r}, "
                    f"EXECUTE={expected!r}"
                ),
            )


def _verify_default_privileges(
    cursor, *, database_name: str, deploy_role: str, runtime_role: str
) -> None:
    cursor.execute(
        """
        WITH object_types(object_type) AS (
            VALUES ('r'::"char"), ('S'::"char"), ('f'::"char")
        ),
        deploy AS (
            SELECT oid FROM pg_roles WHERE rolname = %s
        ),
        global_defaults AS (
            SELECT object_types.object_type,
                   COALESCE(defaults.defaclacl,
                            acldefault(object_types.object_type, deploy.oid)) AS acl
            FROM object_types
            CROSS JOIN deploy
            LEFT JOIN pg_default_acl defaults
              ON defaults.defaclrole = deploy.oid
             AND defaults.defaclnamespace = 0
             AND defaults.defaclobjtype = object_types.object_type
        ),
        schema_defaults AS (
            SELECT defaults.defaclobjtype AS object_type,
                   defaults.defaclacl AS acl
            FROM pg_default_acl defaults
            JOIN deploy ON deploy.oid = defaults.defaclrole
            JOIN pg_namespace namespace
              ON namespace.oid = defaults.defaclnamespace
            WHERE namespace.nspname = 'public'
              AND defaults.defaclobjtype IN ('r', 'S', 'f')
        ),
        effective_acl AS (
            SELECT global_defaults.object_type,
                   '<global>'::text AS scope,
                   expanded.grantee,
                   expanded.privilege_type
            FROM global_defaults
            CROSS JOIN LATERAL aclexplode(global_defaults.acl) expanded
          UNION ALL
            SELECT schema_defaults.object_type,
                   'public'::text AS scope,
                   expanded.grantee,
                   expanded.privilege_type
            FROM schema_defaults
            CROSS JOIN LATERAL aclexplode(schema_defaults.acl) expanded
        )
        SELECT effective_acl.object_type,
               effective_acl.scope,
               effective_acl.privilege_type
        FROM effective_acl
        JOIN pg_roles runtime ON runtime.rolname = %s
        WHERE effective_acl.grantee IN (0, runtime.oid)
        ORDER BY effective_acl.object_type,
                 effective_acl.scope,
                 effective_acl.privilege_type
        """,
        (deploy_role, runtime_role),
    )
    leaked_defaults = tuple(cursor.fetchall())
    if leaked_defaults:
        raise _acl_verification_failure(
            database_name=database_name,
            object_kind="default privileges",
            object_name=deploy_role,
            detail=f"leak to PUBLIC/runtime: {leaked_defaults!r}",
        )


def _verify_database_acl(
    cursor,
    *,
    database_name: str,
    deploy_role: str,
    runtime_role: str,
) -> None:
    cursor.execute(
        """
        SELECT pg_get_userbyid(database.datdba),
               has_database_privilege(%s, database.oid, 'CONNECT'),
               has_database_privilege(%s, database.oid, 'CREATE'),
               has_database_privilege(%s, database.oid, 'TEMPORARY'),
               NOT EXISTS (
                   SELECT 1
                   FROM aclexplode(
                       COALESCE(
                           database.datacl,
                           acldefault('d', database.datdba)
                       )
                   ) database_acl
                   WHERE database_acl.grantee = 0
               ) AS public_acl_is_empty
        FROM pg_database database
        WHERE database.datname = current_database()
        """,
        (runtime_role,) * 3,
    )
    database_state = cursor.fetchone()
    expected_database_state = (deploy_role, True, False, False, True)
    if database_state != expected_database_state:
        raise _acl_verification_failure(
            database_name=database_name,
            object_kind="database",
            object_name=database_name,
            detail=(
                f"has state {database_state!r}, "
                f"expected {expected_database_state!r}"
            ),
        )

    cursor.execute(
        """
        SELECT pg_get_userbyid(namespace.nspowner),
               has_schema_privilege(%s, namespace.oid, 'USAGE'),
               has_schema_privilege(%s, namespace.oid, 'CREATE'),
               NOT EXISTS (
                   SELECT 1
                   FROM aclexplode(
                       COALESCE(
                           namespace.nspacl,
                           acldefault('n', namespace.nspowner)
                       )
                   ) schema_acl
                   WHERE schema_acl.grantee = 0
               ) AS public_acl_is_empty
        FROM pg_namespace namespace
        WHERE namespace.nspname = 'public'
        """,
        (runtime_role,) * 2,
    )
    schema_state = cursor.fetchone()
    expected_schema_state = (deploy_role, True, False, True)
    if schema_state != expected_schema_state:
        raise _acl_verification_failure(
            database_name=database_name,
            object_kind="schema",
            object_name="public",
            detail=f"has state {schema_state!r}, expected {expected_schema_state!r}",
        )

    _verify_table_privileges(
        cursor,
        database_name=database_name,
        deploy_role=deploy_role,
        runtime_role=runtime_role,
    )
    _verify_type_owners(cursor, database_name=database_name, deploy_role=deploy_role)
    _verify_sequence_privileges(
        cursor,
        database_name=database_name,
        deploy_role=deploy_role,
        runtime_role=runtime_role,
    )
    _verify_function_privileges(
        cursor,
        database_name=database_name,
        deploy_role=deploy_role,
        runtime_role=runtime_role,
    )
    _verify_default_privileges(
        cursor,
        database_name=database_name,
        deploy_role=deploy_role,
        runtime_role=runtime_role,
    )


def _configure_database(
    *, admin_url: str, database_name: str, deploy_role: str, runtime_role: str
) -> None:
    with psycopg2.connect(_psycopg_url(admin_url), dbname=database_name) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"archie_runtime_acl:public:{runtime_role}",),
            )
            cursor.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(deploy_role),
                )
            )
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database_name)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(runtime_role),
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(runtime_role),
                )
            )
            cursor.execute(
                sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                    sql.Identifier(deploy_role)
                )
            )
            _transfer_public_objects(
                cursor,
                deploy_role=deploy_role,
                runtime_role=runtime_role,
            )
            cursor.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC")
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(
                    sql.Identifier(runtime_role)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(runtime_role)
                )
            )
            _configure_table_privileges(cursor, runtime_role=runtime_role)
            _configure_sequence_privileges(cursor, runtime_role=runtime_role)
            _configure_default_privileges(
                cursor,
                deploy_role=deploy_role,
                runtime_role=runtime_role,
            )
            _configure_function_privileges(cursor, runtime_role=runtime_role)
            _verify_database_acl(
                cursor,
                database_name=database_name,
                deploy_role=deploy_role,
                runtime_role=runtime_role,
            )


def _verify_database_after_commit(
    *, admin_url: str, database_name: str, deploy_role: str, runtime_role: str
) -> None:
    with psycopg2.connect(
        _psycopg_url(admin_url),
        dbname=database_name,
    ) as verification:
        with verification.cursor() as cursor:
            _verify_database_acl(
                cursor,
                database_name=database_name,
                deploy_role=deploy_role,
                runtime_role=runtime_role,
            )


def configure_database_roles(
    *,
    admin_url: str,
    database_names: Iterable[str],
    deploy_password: str,
    runtime_password: str,
    deploy_role: str = DEPLOY_ROLE,
    runtime_role: str = RUNTIME_ROLE,
) -> None:
    """Create/refresh roles, ownership and least runtime privileges."""
    if not admin_url or not deploy_password or not runtime_password:
        raise ValueError("admin URL and both generated role passwords are required")
    names = tuple(dict.fromkeys(name.strip() for name in database_names if name.strip()))
    if not names:
        raise ValueError("at least one target database is required")

    coordinator = None
    lock_key = f"archie_database_role_bootstrap:{deploy_role}:{runtime_role}"
    lock_acquired = False
    try:
        coordinator = psycopg2.connect(_psycopg_url(admin_url))
        coordinator.autocommit = True
        with coordinator.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (lock_key,))
            lock_acquired = True
        coordinator.autocommit = False

        # This is deliberately the first transaction under the cluster lock.
        # Nothing fallible about targets, memberships, sessions, or ACL repair
        # runs until a fresh connection has observed the committed NOLOGIN.
        _commit_runtime_login_state(
            coordinator,
            runtime_role=runtime_role,
            runtime_password=runtime_password,
            can_login=False,
        )
        _verify_runtime_login_state(
            admin_url=admin_url,
            runtime_role=runtime_role,
            expected_can_login=False,
        )

        with coordinator.cursor() as cursor:
            for database_name in names:
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (database_name,)
                )
                if cursor.fetchone() is None:
                    raise ValueError(
                        f"target database does not exist: {database_name}"
                    )
        coordinator.commit()

        with coordinator.cursor() as cursor:
            _ensure_role(
                cursor,
                role=deploy_role,
                password=deploy_password,
                can_login=True,
            )
        coordinator.commit()

        with coordinator.cursor() as cursor:
            inbound_role_names = _strip_runtime_memberships(
                cursor,
                runtime_role=runtime_role,
            )
        coordinator.commit()
        _verify_runtime_memberships_cleared(
            admin_url=admin_url,
            runtime_role=runtime_role,
        )

        _terminate_runtime_capable_sessions(
            admin_url=admin_url,
            runtime_role=runtime_role,
            inbound_role_names=inbound_role_names,
        )

        for database_name in names:
            _configure_database(
                admin_url=admin_url,
                database_name=database_name,
                deploy_role=deploy_role,
                runtime_role=runtime_role,
            )
            _verify_database_after_commit(
                admin_url=admin_url,
                database_name=database_name,
                deploy_role=deploy_role,
                runtime_role=runtime_role,
            )

        # LOGIN is the last dedicated transaction.  Every target database has
        # committed and then passed a fresh-connection exact-policy check.
        _commit_runtime_login_state(
            coordinator,
            runtime_role=runtime_role,
            runtime_password=runtime_password,
            can_login=True,
        )
        _verify_runtime_login_state(
            admin_url=admin_url,
            runtime_role=runtime_role,
            expected_can_login=True,
        )
    except BaseException:
        if coordinator is not None:
            try:
                coordinator.rollback()
            except psycopg2.Error:
                pass
        try:
            _recover_runtime_nologin(
                admin_url=admin_url,
                runtime_role=runtime_role,
                runtime_password=runtime_password,
            )
        except BaseException as fence_error:
            raise RuntimeError(
                "database role bootstrap failed and the runtime NOLOGIN, "
                "membership, and session termination fence could not be proven"
            ) from fence_error
        raise
    finally:
        if lock_acquired and coordinator is not None:
            try:
                with coordinator.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))", (lock_key,)
                    )
                    cursor.fetchone()
                coordinator.commit()
            except psycopg2.Error:
                coordinator.rollback()
        if coordinator is not None:
            coordinator.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--admin-url", default=os.environ.get("DATABASE_ADMIN_URL", "")
    )
    parser.add_argument(
        "--databases", default=os.environ.get("DATABASE_NAMES", "")
    )
    parser.add_argument(
        "--deploy-password", default=os.environ.get("DATABASE_DEPLOY_PASSWORD", "")
    )
    parser.add_argument(
        "--runtime-password", default=os.environ.get("DATABASE_RUNTIME_PASSWORD", "")
    )
    args = parser.parse_args()
    configure_database_roles(
        admin_url=args.admin_url,
        database_names=args.databases.split(","),
        deploy_password=args.deploy_password,
        runtime_password=args.runtime_password,
    )
    print("database roles configured")


if __name__ == "__main__":
    main()
