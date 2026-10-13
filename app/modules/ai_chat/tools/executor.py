"""
Tool executor: runs tool calls in-process against the SQLAlchemy service layer.

Design decisions:
  - No HTTP.  Direct DB calls only.  One transaction per tool.
  - EntityResolver handles all name→ID fuzzy matching before any write.
  - Ambiguity returns a clarification request; the LLM re-prompts the user.
  - Exceptions roll back and surface as {"success": False, "error": "..."}.
"""

from app.services.archimate_backbone import sync_archimate_element
import hashlib
import logging
import re
from dataclasses import dataclass

from app import db
from app.utils.duplicate_guard import (
    find_duplicate_by_name,
    find_similar_entities,
    lock_name_for_write,
)

from .registry import TOOL_SCHEMA_BY_NAME
from .resolver import EntityResolver

logger = logging.getLogger(__name__)

# ARCH-071: the agent creates entities autonomously (create_solution,
# create_archimate_element, create_driver/goal/constraint, ...), so its
# name/identifier arguments are as much a stored-input path as the
# Applications form's name field, which validate_application_name already
# strips (see app/utils/validators.py). Applied once here, at execute()'s
# single dispatch choke point, rather than per-tool, so no future tool with a
# free-text "name"/"title" argument has to remember to add it. Stripping, not
# entity-escaping — matches validate_application_name's approach so this does
# not double-escape on top of Jinja's own autoescape at render time.
_TAG_RE = re.compile(r"<[^>]*>")
_NAME_LIKE_ARG_KEYS = frozenset({"name", "title"})


def _strip_tags_from_name_args(arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        return arguments
    cleaned = dict(arguments)
    for key in _NAME_LIKE_ARG_KEYS:
        value = cleaned.get(key)
        if isinstance(value, str):
            cleaned[key] = _TAG_RE.sub("", value)
    return cleaned



def _load_acting_user(user_id):
    """Load the user a request is executing as, scoped to the current tenant.

    Deliberately NOT User.query.get(). Query.get() is tenant-scoped only on an
    identity-map MISS (CLAUDE.md): on a hit it returns the cached object without
    emitting SQL, so do_orm_execute never runs and no tenant predicate is
    applied. A permission check must never be able to authorise against a user
    cached from another organisation — harmless per-request, but the agent
    runner, CLI and scheduler all loop over tenants inside one session, and that
    is exactly where an autonomous agent executes.

    User is tenant-owned but does NOT carry TenantMixin, so the org predicate is
    added explicitly here rather than injected by the middleware.
    """
    from flask import g
    from app.models.user import User

    query = User.query.filter_by(id=user_id)
    org_id = getattr(g, "current_org_id", None)
    if org_id is not None:
        query = query.filter_by(organization_id=org_id)
    return query.first()


def _verified_solution_workspace_id(workspace_id, solution_id):
    """Turn an untrusted tool argument into a tenant-bound workspace id."""
    from flask import g

    from app.models.solution_architect_models import SolutionAnalysisSession
    from app.models.solution_models import Solution

    organization_id = getattr(g, "current_org_id", None)
    if type(workspace_id) is not int or type(solution_id) is not int:
        return None
    if not isinstance(organization_id, int) or organization_id <= 0:
        return None

    solution = db.session.execute(
        db.select(Solution).where(
            Solution.id == solution_id,
            Solution.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    workspace = db.session.execute(
        db.select(SolutionAnalysisSession).where(
            SolutionAnalysisSession.id == workspace_id,
            SolutionAnalysisSession.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if solution is None or workspace is None:
        return None

    metadata_solution_id = (workspace.custom_metadata or {}).get("solution_id")
    if solution.analysis_session_id == workspace.id or metadata_solution_id == solution.id:
        return workspace.id
    return None


class ToolPermissionError(Exception):
    """Raised when the invoking user lacks permission to run a mutating tool.

    Deliberately a distinct exception rather than a plain return dict from a
    private helper, so a future call site cannot forget to check a boolean and
    fall through to execution — every code path that wants the refusal message
    has to go through _authorize_tool_call, which either returns normally or
    raises.
    """


def _permission_denied_result(tool_name: str, user) -> dict:
    """The honest, permissions-framed refusal (V-01).

    Named after the user's own permission, not a generic 'forbidden' — the
    architectural requirement is that a Viewer is told WHY in terms they can
    act on: they don't hold write access, and someone who does can be asked.
    """
    role_label = getattr(user, "role_name", None) or "your current role"
    return {
        "success": False,
        "error": (
            f"I can't do that — {role_label} doesn't include write access, so I'm not "
            f"able to run '{tool_name}' on your behalf. This isn't something a prompt "
            f"instruction can override; it's enforced by the system for every "
            f"write action, not just this one. If this is something you need, I can "
            f"flag it for someone with write permission (an Architect, Approver, or "
            f"Admin) to action, or you can ask them directly."
        ),
        "code": "PERMISSION_DENIED",
        "permission_denied": True,
    }


# The rule both refusal points apply: the user's role must grant
# Permission.GENERAL (write access). Stored with each refusal so an
# administrator reads which rule refused the call, not only that it was refused.
WRITE_PERMISSION_RULE = "write_permission"
WRITES_PAUSED_RULE = "writes_paused"
REFUSED_TOOL_CALL_ACTION = "tool_refused"
REFUSED_TOOL_CALL_TABLE = "ai_tool_call"
_SECRET_ARGUMENT = re.compile(r"pass(word)?|secret|token|api[_-]?key|credential|auth", re.I)


def _summarise_arguments(arguments) -> dict:
    """A short, secret-free account of what the tool call asked for.

    Keys are kept (one nested level is written as ``outer.inner``); a value
    whose key names a secret is replaced by "[withheld]"; long text is cut;
    anything nested deeper is counted, not copied. At most 20 entries.
    """
    summary = {}
    if not isinstance(arguments, dict):
        return summary

    def _add(key, value, depth):
        if len(summary) >= 20:
            return
        if _SECRET_ARGUMENT.search(key.rsplit(".", 1)[-1]):
            summary[key] = "[withheld]"
        elif isinstance(value, dict) and depth == 0:
            for inner in sorted(value, key=str):
                _add("%s.%s" % (key, inner), value[inner], 1)
        elif isinstance(value, (dict, list, tuple)):
            summary[key] = "%d item%s" % (len(value), "" if len(value) == 1 else "s")
        elif value is None or isinstance(value, (bool, int, float)):
            summary[key] = value
        else:
            text_value = str(value)
            summary[key] = text_value if len(text_value) <= 120 else text_value[:117] + "..."

    for key in sorted(arguments, key=str):
        if str(key).startswith("_"):
            continue  # server-injected context, not something the caller asked for
        _add(str(key), arguments[key], 0)
    return summary


def record_refused_tool_call(user, tool_name: str, arguments, *, via: str) -> None:
    """Record that an AI tool call was refused for lacking write permission.

    Written through ``AuditLog.log`` to the append-only audit log (the store
    the admin audit screen reads) under the refusing user's organisation. It
    is called before any tool handler runs, so the commit it makes carries
    nothing but the record. Never raises -- a refusal must still be returned
    when the record cannot be written.
    """
    try:
        from flask import has_request_context, request

        from app.models.audit_log import AuditLog

        role_name = getattr(user, "role_name", None) or "no role"
        values = {
            "organization_id": getattr(user, "organization_id", None),
            "user_id": getattr(user, "id", None),
            "action": REFUSED_TOOL_CALL_ACTION,
            "table_name": REFUSED_TOOL_CALL_TABLE,
            "new_value": {
                "tool": tool_name,
                "rule": WRITE_PERMISSION_RULE,
                "rule_description": (
                    "The %s role does not include write access, which every "
                    "AI tool that changes records requires." % role_name
                ),
                "role": role_name,
                "via": via,
                "arguments": _summarise_arguments(arguments),
            },
        }
        if has_request_context():
            values["ip_address"] = (request.remote_addr or "")[:45] or None
            values["user_agent"] = (request.headers.get("User-Agent") or "")[:500] or None
        AuditLog.log(**values)
    except Exception:
        logger.warning("Could not record the refused tool call '%s'", tool_name, exc_info=True)


def record_paused_tool_call(user, tool_name: str, arguments, *, paused_by_name: str,
                            paused_reason: str) -> None:
    """Record that an AI tool call was refused because writes are paused.

    Written through ``AuditLog.log`` to the append-only audit log under the
    refusing user's organisation. Called before any tool handler runs when the
    organisation's stop-all-writes switch is active. Never raises.
    """
    try:
        from flask import has_request_context, request

        from app.models.audit_log import AuditLog

        values = {
            "organization_id": getattr(user, "organization_id", None),
            "user_id": getattr(user, "id", None),
            "action": REFUSED_TOOL_CALL_ACTION,
            "table_name": REFUSED_TOOL_CALL_TABLE,
            "new_value": {
                "tool": tool_name,
                "rule": WRITES_PAUSED_RULE,
                "rule_description": (
                    "Agent writes are paused for this organisation "
                    "(paused by %s): %s" % (paused_by_name, paused_reason)
                ),
                "role": getattr(user, "role_name", None) or "no role",
                "via": "oversight",
                "arguments": _summarise_arguments(arguments),
            },
        }
        if has_request_context():
            values["ip_address"] = (request.remote_addr or "")[:45] or None
            values["user_agent"] = (request.headers.get("User-Agent") or "")[:500] or None
        AuditLog.log(**values)
    except Exception:
        logger.warning("Could not record the paused tool call '%s'", tool_name, exc_info=True)


def _duplicate_tool_result(noun: str, existing) -> dict:
    """The tool-call analogue of a 409 (ARCH-030).

    Neither merges nor silently rejects: the agent is handed the id and name of
    what it collided with, so it can reference the existing record, and told the
    exact argument that would override the guard if the duplicate is intended.
    """
    return {
        "success": False,
        "error": (
            f"A {noun} named '{existing.name}' already exists (ID {existing.id}). "
            f"Reference it instead of creating another, or pass "
            f"allow_duplicate=true if a second one is genuinely intended."
        ),
        "code": "DUPLICATE_NAME",
        "duplicate_of": {"id": existing.id, "name": existing.name},
    }


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


class ToolExecutor:

    def __init__(self, user_id: int, persona: str = None):
        self.user_id = user_id
        self._resolver = EntityResolver()
        self._org_id = None  # cached lazily
        self._persona = persona
        self._charter_checked = None  # cached result of the charter check

    @staticmethod
    def _coverage(rows, total, noun):
        """Result fields that state how much of the matching set is being shown.

        Every read tool used to return `"count": len(rows)` with a message of the
        form "Found N application(s)" - where N was the LIMIT, not the number of
        matches. Ask "how many applications are in production?" against a
        5,000-application estate and the model was handed `{"count": 15,
        "message": "Found 15 application(s)."}`, with nothing to indicate the
        other 4,985 existed. It then answered "15", truthfully reporting what it
        was told and inventing a fact about the customer's portfolio.

        That is the exact failure CLAUDE.md's "never invent data" rule targets,
        and the fabricated-data gate cannot see it: the gate inspects templates
        and view code, not tool-result strings assembled server-side.

        `total` is None when the caller genuinely cannot count the matching set
        cheaply - the fields then say so rather than implying completeness.
        """
        returned = len(rows)
        if total is None:
            return {
                "count": returned,
                "returned": returned,
                "total": None,
                "truncated": None,
                "message": (
                    f"Showing {returned} {noun}. The total number of matches was "
                    f"not determined - do not report this as a total."
                ),
            }
        truncated = total > returned
        return {
            "count": returned,
            "returned": returned,
            "total": total,
            "truncated": truncated,
            "message": (
                f"Showing {returned} of {total} matching {noun}. "
                f"Report {total} as the total, not {returned}; "
                f"narrow the query to see different ones."
                if truncated
                else f"Found {returned} {noun} - this is the complete set."
            ),
        }

    def _get_organization_id(self) -> int:
        """Return the organization_id for the current user (cached after first call)."""
        if self._org_id is not None:
            return self._org_id
        # The request/tenant context is the authority for the current operation.
        # Prefer it over an identity lookup so a reused executor cannot silently
        # act in another user's tenant. This also avoids the historic fallback to
        # organization 1, which was a fail-open cross-tenant default.
        from flask import g, has_app_context

        if has_app_context() and getattr(g, "current_org_id", None) is not None:
            self._org_id = int(g.current_org_id)
            return self._org_id
        from sqlalchemy import text
        row = db.session.execute(
            text("SELECT organization_id FROM users WHERE id = :uid"),
            {"uid": self.user_id},
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError("Cannot resolve an organization for the tool executor")
        self._org_id = int(row[0])
        return self._org_id

    # ------------------------------------------------------------------ #
    # Public dispatch                                                      #
    # ------------------------------------------------------------------ #

    def _user_can_write(self) -> bool:
        """Server-side write-permission check for the invoking user (V-01).

        This — not the prompt, not the LLM's own judgement — is the access
        control. `Permission.GENERAL` is the same bitfield `user.can()` already
        checks for every non-AI write route (app/models/user.py); a Viewer role
        carries permissions=0 and fails it, exactly as it fails the equivalent
        HTTP routes. Reusing it means the AI surface can never be *more*
        permissive than the API it wraps.
        """
        from app.models.user import Permission

        user = _load_acting_user(self.user_id)
        if not user:
            return False
        return bool(user.can(Permission.GENERAL))

    def execute(self, tool_call: ToolCall) -> dict:
        """Dispatch one tool call. THE choke point for every agent write.

        Every write reaches the database through a handler on this class —
        called here directly for auto-tier tools (AgentRunner._run below) and
        again here for queued tool_use approvals
        (AIChatApprovalService.approve_and_execute, which builds a ToolCall and
        calls this same method). There is no other path from an agent tool
        name to a `_tool_*` handler, so a permission check placed here — before
        the handler dispatch, not inside any individual `_tool_*` method —
        covers every mutating tool without per-tool ad hoc checks, including
        tools added in future that forget to add their own.
        """
        handler = getattr(self, f"_tool_{tool_call.name}", None)
        if not handler:
            return {"success": False, "error": f"Unknown tool: {tool_call.name}"}

        # Charter enforcement: a tool call must be within the persona's charter
        # bounds. This is checked BEFORE the permission check so a refused
        # charter-bound call is surfaced as the charter refusal, not a
        # permission denial — the persona has no business calling this tool,
        # regardless of the user's own permissions.
        persona = self._persona
        if persona:
            try:
                org_id = self._get_organization_id()
                from app.models.agent_charter import AgentCharter

                if not AgentCharter.tool_allowed(persona, tool_call.name, org_id):
                    logger.warning(
                        "ToolExecutor: refusing tool '%s' for persona '%s' — outside charter bounds",
                        tool_call.name, persona,
                    )
                    return {
                        "success": False,
                        "error": (
                            f"I can't run '{tool_call.name}' — the {persona.replace('_', ' ')} "
                            f"charter does not permit this action. If this is something you "
                            f"need, switch to a persona whose scope covers it, or ask an "
                            f"administrator to amend the charter."
                        ),
                        "code": "CHARTER_REFUSED",
                        "charter_refused": True,
                        "persona": persona,
                        "tool": tool_call.name,
                    }
            except Exception:
                # Fail CLOSED: if the charter cannot be read, refuse the call.
                logger.exception(
                    "ToolExecutor: charter check failed for persona '%s' tool '%s' — refusing",
                    persona, tool_call.name,
                )
                return {
                    "success": False,
                    "error": (
                        f"I can't run '{tool_call.name}' — the charter for "
                        f"{persona.replace('_', ' ')} could not be verified. "
                        f"An administrator should check the charter is correctly seeded."
                    ),
                    "code": "CHARTER_UNAVAILABLE",
                    "charter_refused": True,
                }

        schema = TOOL_SCHEMA_BY_NAME.get(tool_call.name)
        # Fail CLOSED: an unregistered/unclassified tool is treated as mutating,
        # matching AgentRunner._should_queue's own fail-closed rule for the same
        # reason — "we don't know" must never be treated as "safe to run".
        mutates = True if schema is None else bool(schema.get("mutates", True))
        if mutates:
            user = _load_acting_user(self.user_id)

            # Oversight pause check: if the organisation's stop-all-writes
            # switch is active, refuse every mutating call before it reaches
            # the write-permission check or the approval queue. The pause
            # takes effect on the next dispatch, not on a poll.
            if user is not None:
                org_id = self._get_organization_id()
                from app.models.agent_oversight_state import AgentOversightState
                oversight_state = AgentOversightState.get_for_org(org_id)
                if oversight_state.is_paused():
                    paused_by_name = "Unknown"
                    if oversight_state.paused_by_id:
                        paused_by_user = _load_acting_user(oversight_state.paused_by_id)
                        if paused_by_user:
                            paused_by_name = paused_by_user.full_name()
                    logger.warning(
                        "ToolExecutor: refusing mutating tool '%s' for user_id=%s — writes paused",
                        tool_call.name, self.user_id,
                    )
                    record_paused_tool_call(
                        user, tool_call.name, tool_call.arguments,
                        paused_by_name=paused_by_name,
                        paused_reason=oversight_state.reason or "No reason given",
                    )
                    return {
                        "success": False,
                        "error": (
                            "Agent writes are paused for this organisation "
                            "(paused by %s): %s"
                            % (paused_by_name, oversight_state.reason or "No reason given")
                        ),
                        "code": "WRITES_PAUSED",
                        "writes_paused": True,
                    }

            if not user or not self._user_can_write():
                logger.warning(
                    "ToolExecutor: refusing mutating tool '%s' for user_id=%s — no write permission",
                    tool_call.name, self.user_id,
                )
                if user is not None:
                    record_refused_tool_call(user, tool_call.name, tool_call.arguments, via="agent")
                return _permission_denied_result(tool_call.name, user)

        try:
            arguments = _strip_tags_from_name_args(tool_call.arguments)
            if tool_call.name == "submit_for_arb_review":
                tool_call_digest = hashlib.sha256(str(tool_call.id).encode("utf-8")).hexdigest()
                arguments["_trusted_command_key"] = f"ai-tool-{tool_call_digest}"
            return handler(arguments)
        except Exception as e:
            db.session.rollback()
            logger.exception("Tool %s failed", tool_call.name)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _get_or_create_problem_id(self, solution_id: int) -> int:
        """
        Get or create the SolutionProblemDefinition for a solution.
        SolutionDriver/Goal/Constraint all require a problem_id FK.
        Creates SolutionAnalysisSession + SolutionProblemDefinition if absent.
        """
        from datetime import datetime as _dt
        from app.models.solution_architect_models import (
            SolutionAnalysisSession,
            SolutionProblemDefinition,
            SolutionSessionStatus,
        )

        # Look up by the canonical agent-session name for this solution
        session_name = f"Agent session — solution {solution_id}"
        session = SolutionAnalysisSession.query.filter_by(name=session_name).first()
        if not session:
            session = SolutionAnalysisSession(
                name=session_name,
                status=SolutionSessionStatus.COMPLETED,
                created_by_id=self.user_id,
                created_at=_dt.utcnow(),
                updated_at=_dt.utcnow(),
                current_version=1,
                organization_id=self._get_organization_id(),
            )
            db.session.add(session)
            db.session.flush()

        prob = SolutionProblemDefinition.query.filter_by(session_id=session.id).first()
        if not prob:
            prob = SolutionProblemDefinition(
                session_id=session.id,
                problem_description="Agent-initiated session",
                organization_id=self._get_organization_id(),
            )
            db.session.add(prob)
            db.session.flush()

        return prob.id

    def _clarify(self, entity: str, result: dict) -> dict:
        return {
            "success": False,
            "needs_clarification": True,
            "entity": entity,
            "candidates": result.get("candidates", []),
            "error": (
                f"Ambiguous {entity} name — found {len(result['candidates'])} matches. "
                "Ask the user which one they meant."
                if result["candidates"]
                else f"No {entity} found with that name. Ask the user to check the name."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: create_solution                                                #
    # ------------------------------------------------------------------ #

    def _tool_create_solution(self, args: dict) -> dict:
        """Create a Solution.

        ARCH-030: refuses a name that already exists in the organisation
        (case-insensitive, whitespace-normalised) and hands the agent the
        colliding record instead, so it can reuse it. ``allow_duplicate: true``
        in the tool arguments creates it anyway. This is the path that produced
        four near-identical "HxGN EAM Asset Management" solutions.
        """
        from app.models.solution_models import Solution

        org_id = self._get_organization_id()
        if not args.get("allow_duplicate"):
            # Serialise the check-then-insert against a concurrent identical
            # request (see lock_name_for_write).
            lock_name_for_write(Solution, args["name"], organization_id=org_id)
            existing = find_duplicate_by_name(
                Solution, args["name"], organization_id=org_id
            )
            if existing is not None:
                return _duplicate_tool_result("solution", existing)

        # S-06: near-duplicate advisory for the agent -- surfaced in the tool
        # result BEFORE the caller acts on it further, not only in the
        # post-hoc rationalization sweep.
        similar = find_similar_entities(Solution, args["name"], organization_id=org_id)

        sol = Solution(
            name=args["name"],
            description=args.get("description", ""),
            business_domain=args.get("business_domain"),
            solution_type=args.get("solution_type"),
            status="planned",
            governance_status="draft",
            organization_id=org_id,
        )
        db.session.add(sol)
        db.session.commit()
        logger.info("AgentRunner created solution id=%s name=%r user=%s", sol.id, sol.name, self.user_id)
        result = {
            "success": True,
            "result": {"id": sol.id, "name": sol.name},
            "message": f"Created solution '{sol.name}' (ID {sol.id}).",
            "url": f"/solutions/{sol.id}",
        }
        if similar:
            result["similar_entities"] = similar
            result["message"] += (
                f" Note: {len(similar)} similar existing solution(s) found — consider reusing "
                "one instead if it represents the same thing."
            )
        return result

    # ------------------------------------------------------------------ #
    # Tool: link_capability_to_solution                                   #
    # ------------------------------------------------------------------ #

    def _tool_link_capability_to_solution(self, args: dict) -> dict:
        sol_r = self._resolver.resolve_solution(args["solution_name"])
        cap_r = self._resolver.resolve_capability(args["capability_name"])

        if not sol_r["resolved"]:
            return self._clarify("solution", sol_r)
        if not cap_r["resolved"]:
            return self._clarify("capability", cap_r)

        from app.models.solution_models import SolutionCapabilityMapping

        # Avoid duplicate
        existing = SolutionCapabilityMapping.query.filter_by(
            solution_id=sol_r["id"],
            capability_id=cap_r["id"],
        ).first()
        if existing:
            return {
                "success": True,
                "result": {"solution": sol_r["name"], "capability": cap_r["name"]},
                "message": (
                    f"Capability '{cap_r['name']}' is already linked to "
                    f"solution '{sol_r['name']}'."
                ),
            }

        mapping = SolutionCapabilityMapping(
            solution_id=sol_r["id"],
            capability_id=cap_r["id"],
            support_level=args.get("support_level", "primary"),
            notes=args.get("notes"),
            created_by_id=self.user_id,
        )
        db.session.add(mapping)
        db.session.commit()
        return {
            "success": True,
            "result": {"solution": sol_r["name"], "capability": cap_r["name"]},
            "message": (
                f"Linked capability '{cap_r['name']}' to solution '{sol_r['name']}' "
                f"({args.get('support_level', 'primary')} support)."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: link_application_to_capability                                #
    # ------------------------------------------------------------------ #

    def _tool_link_application_to_capability(self, args: dict) -> dict:
        app_r = self._resolver.resolve_application(args["application_name"])
        cap_r = self._resolver.resolve_capability(args["capability_name"])

        if not app_r["resolved"]:
            return self._clarify("application", app_r)
        if not cap_r["resolved"]:
            return self._clarify("capability", cap_r)

        from app.models.application_capability import ApplicationCapabilityMapping

        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        existing = ApplicationCapabilityMapping.query.filter_by(
            application_component_id=app_r["id"],
            business_capability_id=cap_r["id"],
        ).first()
        if existing:
            return {
                "success": True,
                "message": (
                    f"'{app_r['name']}' is already mapped to capability '{cap_r['name']}'."
                ),
            }

        mapping = ApplicationCapabilityMapping(
            application_component_id=app_r["id"],
            business_capability_id=cap_r["id"],
            support_level=args.get("coverage_level", "partial"),
        )
        db.session.add(mapping)
        db.session.commit()
        return {
            "success": True,
            "result": {"application": app_r["name"], "capability": cap_r["name"]},
            "message": (
                f"Mapped application '{app_r['name']}' to capability '{cap_r['name']}' "
                f"({args.get('coverage_level', 'partial')} coverage)."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: create_archimate_element                                       #
    # ------------------------------------------------------------------ #

    def _tool_create_archimate_element(self, args: dict) -> dict:
        """Create an ArchiMateElement.

        ARCH-030: the repository was 46% duplicated (25 duplicate-name groups
        over 67 of 145 elements, several triplicated) because this tool inserted
        unconditionally and the agent runs autonomously. A same-named element of
        the *same type* in the same organisation is now refused, and the
        colliding element is returned so the agent can reference it. Two
        elements may legitimately share a name across types (an
        ApplicationService and a BusinessProcess called "Order Processing"), so
        ``type`` is part of the match. ``allow_duplicate: true`` overrides.
        """
        try:
            from app.models.archimate_core import ArchiMateElement
        except ImportError:
            from app.models.models import ArchiMateElement

        org_id = self._get_organization_id()
        if not args.get("allow_duplicate"):
            # Serialise the check-then-insert against a concurrent identical
            # request (see lock_name_for_write).
            lock_name_for_write(
                ArchiMateElement, args["name"], organization_id=org_id
            )
            existing = find_duplicate_by_name(
                ArchiMateElement,
                args["name"],
                organization_id=org_id,
                extra_filters=[ArchiMateElement.type == args["type"]],
            )
            if existing is not None:
                return _duplicate_tool_result("ArchiMate element", existing)

        # S-06: near-duplicate advisory for the agent, same-type scoped like
        # the exact-match guard above.
        similar = find_similar_entities(
            ArchiMateElement,
            args["name"],
            organization_id=org_id,
            extra_filters=[ArchiMateElement.type == args["type"]],
        )

        elem = ArchiMateElement(
            name=args["name"],
            type=args["type"],
            layer=args["layer"],
            description=args.get("description", ""),
            organization_id=org_id,
        )
        db.session.add(elem)
        db.session.flush()  # get elem.id before linking

        # Optionally link to a solution
        solution_name = args.get("solution_name")
        if solution_name:
            sol_r = self._resolver.resolve_solution(solution_name)
            if sol_r["resolved"]:
                from app.models.solution_archimate_element import SolutionArchiMateElement
                link = SolutionArchiMateElement(
                    solution_id=sol_r["id"],
                    element_id=elem.id,
                    layer_type=args["layer"],
                    element_table='archimate_elements',
                )
                db.session.add(link)

        db.session.commit()
        result = {
            "success": True,
            "result": {"id": elem.id, "name": elem.name, "type": elem.type, "layer": elem.layer},
            "message": (
                f"Created ArchiMate element '{elem.name}' ({elem.type}, {elem.layer} layer)"
                + (f" and linked to solution '{sol_r['name']}'." if solution_name and sol_r.get("resolved") else ".")
            ),
        }
        if similar:
            result["similar_entities"] = similar
            result["message"] += (
                f" Note: {len(similar)} similar existing element(s) of this type found — consider "
                "reusing one instead if it represents the same thing."
            )
        return result

    # ------------------------------------------------------------------ #
    # Tool: update_application_status (approve tier — pre-approved by     #
    # ApprovalGate before this is called)                                 #
    # ------------------------------------------------------------------ #

    def _tool_update_application_status(self, args: dict) -> dict:
        app_r = self._resolver.resolve_application(args["application_name"])
        if not app_r["resolved"]:
            return self._clarify("application", app_r)

        from app.models.application_component_fast import ApplicationComponent
        app_obj = ApplicationComponent.query.get(app_r["id"])
        if not app_obj:
            return {"success": False, "error": "Application not found"}

        old_status = app_obj.deployment_status
        app_obj.deployment_status = args["new_status"]
        db.session.commit()
        logger.info(
            "AgentRunner updated application id=%s status %r → %r (user=%s, rationale=%r)",
            app_r["id"], old_status, args["new_status"], self.user_id, args.get("rationale"),
        )
        return {
            "success": True,
            "result": {
                "application": app_r["name"],
                "old_status": old_status,
                "new_status": args["new_status"],
            },
            "message": (
                f"Updated '{app_r['name']}' status from '{old_status}' to '{args['new_status']}'."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: submit_for_arb_review (approve tier)                          #
    # ------------------------------------------------------------------ #

    def _tool_submit_for_arb_review(self, args: dict) -> dict:
        command_key = args.pop("_trusted_command_key", None)
        sol_r = self._resolver.resolve_solution(args["solution_name"])
        if not sol_r["resolved"]:
            return self._clarify("solution", sol_r)

        # Tool arguments remain model-controlled at this edge even though the
        # normal runner replaces workspace_id from request context. Re-resolve
        # the binding from tenant-owned records before labelling it trusted.
        workspace_id = _verified_solution_workspace_id(
            args.get("workspace_id"), sol_r["id"]
        )
        if workspace_id is None:
            return {
                "success": False,
                "reason_codes": ["trusted_workspace_required"],
                "missing_evidence": [
                    {
                        "code": "trusted_workspace_required",
                        "action": "Open the linked solution workbench and retry",
                    }
                ],
                "error": "A trusted solution workbench is required for AI submission.",
            }

        from app.modules.transformation_room.arb_submission_adapter import (
            TypedARBSubmissionAdapter,
        )

        submission = TypedARBSubmissionAdapter.submit_solution_for_actor(
            actor_id=self.user_id,
            solution_id=sol_r["id"],
            trusted_workspace_id=workspace_id,
            trusted_human_reviewed=True,
            command_key=command_key,
        )
        if not submission.success:
            blocked = {
                "success": False,
                "reason_codes": submission.reason_codes,
                "missing_evidence": submission.missing_evidence,
                "error": "ARB submission is blocked until the listed evidence is complete.",
            }
            if "cost_source_required" in submission.reason_codes:
                from app.modules.solutions_strategic.v2.services.arb_submission_service import (
                    architect_cost_provenance_recovery,
                )

                blocked["recovery"] = architect_cost_provenance_recovery(sol_r["id"])
                blocked["error"] = blocked["recovery"]["message"]
            return blocked

        return {
            "success": True,
            "result": {
                "solution": sol_r.get("name") or args["solution_name"],
                "review_item_id": submission.review_item_id,
                "review_number": submission.review_number,
                "snapshot_id": submission.snapshot_id,
                "idempotent": submission.idempotent,
                "review_cycle_id": submission.review_cycle_id,
                "canonical_url": submission.canonical_url,
            },
            "message": f"Submitted '{sol_r.get('name') or args['solution_name']}' for ARB review ({submission.review_number}).",
        }

    # ------------------------------------------------------------------ #
    # Tool: query_capability_gaps (read-only)                             #
    # ------------------------------------------------------------------ #

    def _tool_query_capability_gaps(self, args: dict) -> dict:
        from app.models.business_capabilities import BusinessCapability

        max_maturity = args.get("max_maturity", 2)
        domain_filter = args.get("business_domain")
        limit = min(args.get("limit", 20), 100)

        q = BusinessCapability.query.filter(
            BusinessCapability.current_maturity_level <= max_maturity
        )
        if domain_filter:
            q = q.filter(BusinessCapability.business_domain.ilike(f"%{domain_filter}%"))

        total = q.count()
        caps = q.order_by(BusinessCapability.current_maturity_level.asc()).limit(limit).all()

        rows = []
        for c in caps:
            app_count = ApplicationCapabilityMapping_count(c.id)
            rows.append({
                "id": c.id,
                "name": c.name,
                "current_maturity": c.current_maturity_level,
                "target_maturity": c.target_maturity_level,
                "business_domain": c.business_domain,
                "strategic_importance": c.strategic_importance,
                "supporting_apps": app_count,
            })

        scope = (
            f"capabilities with maturity <= {max_maturity}"
            + (f" in '{domain_filter}'" if domain_filter else "")
        )
        return {
            "success": True,
            "result": rows,
            **self._coverage(rows, total, scope),
        }

    # ------------------------------------------------------------------ #
    # Tool: find_applications (read-only)                                 #
    # ------------------------------------------------------------------ #

    def _tool_find_applications(self, args: dict) -> dict:
        # ARCH-013: application_component_fast.ApplicationComponent and
        # application_portfolio.ApplicationComponent are two model classes
        # mapped (via extend_existing) onto the SAME "application_components"
        # table, but they declare different columns. The fast model only
        # exposes deployment_status (design/development/testing/production/
        # retiring/decommissioned); the portfolio model — the one
        # /applications/api/list and the Applications UI actually use — also
        # has lifecycle_status (planning/development/testing/operational/
        # deprecated/retired), a genuinely distinct field. This tool used to
        # import the fast model and label deployment_status as bare "status",
        # so the agent reported "development" (deployment_status) for the
        # same row the UI correctly reported "operational" (lifecycle_status)
        # for — not a mapping bug, a field-naming collision. Fixed by
        # querying the portfolio model (matching the UI/API's source of
        # truth) and never emitting an ambiguous "status" key.
        from app.models.application_portfolio import ApplicationComponent

        limit = min(args.get("limit", 15), 50)
        q = ApplicationComponent.query

        name_filter = args.get("name_contains")
        if name_filter:
            q = q.filter(ApplicationComponent.name.ilike(f"%{name_filter}%"))

        status_filter = args.get("lifecycle_status") or args.get("status")
        if status_filter:
            q = q.filter(ApplicationComponent.lifecycle_status == status_filter)

        # Capability filter: join through ApplicationCapabilityMapping
        cap_name = args.get("capability_name")
        if cap_name:
            cap_r = self._resolver.resolve_capability(cap_name)
            if cap_r["resolved"]:
                from app.models.application_capability import ApplicationCapabilityMapping
                cap_ids = [
                    row.application_component_id
                    # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                    for row in ApplicationCapabilityMapping.query.filter_by(
                        business_capability_id=cap_r["id"]
                    ).all()
                ]
                q = q.filter(ApplicationComponent.id.in_(cap_ids))

        # Count the matching set BEFORE applying the limit, so the model can be
        # told what it is not seeing.
        total = q.count()
        apps = q.limit(limit).all()
        rows = [
            {
                "id": a.id,
                "name": a.name,
                # Never "status" — the ambiguity was the defect (ARCH-013).
                # lifecycle_status matches /applications/api/list and the
                # Applications UI exactly; deployment_status is a distinct
                # technical-deployment concept and is labelled as such.
                "lifecycle_status": a.lifecycle_status,
                "deployment_status": a.deployment_status,
                "owner_team": getattr(a, "owner_team", None),
            }
            for a in apps
        ]
        return {
            "success": True,
            "result": rows,
            **self._coverage(rows, total, "application(s)"),
        }


    # ------------------------------------------------------------------ #
    # Tool: create_driver                                                 #
    # ------------------------------------------------------------------ #

    def _tool_create_driver(self, args: dict) -> dict:
        from app.models.solution_architect_models import SolutionDriver, DriverType
        problem_id = self._get_or_create_problem_id(args["solution_id"])
        driver = SolutionDriver(
            problem_id=problem_id,
            name=args["name"],
            description=args.get("description", ""),
            driver_type=DriverType(args["driver_type"]),
            ai_generated=True,
            organization_id=self._get_organization_id(),
        )
        db.session.add(driver)
        sync_archimate_element(driver)
        db.session.commit()
        logger.info("Agent created driver id=%s solution=%s", driver.id, args["solution_id"])
        return {
            "success": True,
            "result": {"id": driver.id, "name": driver.name, "entity_type": "driver", "solution_id": args["solution_id"]},
            "message": f"Added driver '{driver.name}' to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: create_goal                                                   #
    # ------------------------------------------------------------------ #

    def _tool_create_goal(self, args: dict) -> dict:
        from app.models.solution_architect_models import SolutionGoal
        problem_id = self._get_or_create_problem_id(args["solution_id"])
        goal = SolutionGoal(
            problem_id=problem_id,
            name=args["name"],
            description=args.get("description", ""),
            priority=args.get("priority", 3),
            ai_generated=True,
            organization_id=self._get_organization_id(),
        )
        db.session.add(goal)
        sync_archimate_element(goal)
        db.session.commit()
        return {
            "success": True,
            "result": {"id": goal.id, "name": goal.name, "entity_type": "goal", "solution_id": args["solution_id"]},
            "message": f"Added goal '{goal.name}' to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: create_constraint                                             #
    # ------------------------------------------------------------------ #

    def _tool_create_constraint(self, args: dict) -> dict:
        from app.models.solution_architect_models import SolutionConstraint, ConstraintType
        problem_id = self._get_or_create_problem_id(args["solution_id"])
        constraint = SolutionConstraint(
            problem_id=problem_id,
            name=args["name"],
            description=args.get("description", ""),
            constraint_type=ConstraintType(args["constraint_type"]),
            severity=args.get("severity", 3),
            ai_generated=True,
            organization_id=self._get_organization_id(),
        )
        db.session.add(constraint)
        sync_archimate_element(constraint)
        db.session.commit()
        return {
            "success": True,
            "result": {"id": constraint.id, "name": constraint.name, "entity_type": "constraint", "solution_id": args["solution_id"]},
            "message": f"Added constraint '{constraint.name}' to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: create_requirement                                            #
    # ------------------------------------------------------------------ #

    def _tool_create_requirement(self, args: dict) -> dict:
        from app.models.solution_architect_models import SolutionRequirement, RequirementType
        req = SolutionRequirement(
            solution_id=args["solution_id"],
            name=args["name"],
            description=args.get("description", args["name"]),
            requirement_type=RequirementType(args["requirement_type"]) if args.get("requirement_type") else None,
            ai_generated=True,
            status="open",
            organization_id=self._get_organization_id(),
        )
        db.session.add(req)
        sync_archimate_element(req)
        db.session.commit()
        return {
            "success": True,
            "result": {"id": req.id, "name": req.name, "entity_type": "requirement", "solution_id": args["solution_id"]},
            "message": f"Added requirement '{req.name}' to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: create_risk                                                   #
    # ------------------------------------------------------------------ #

    def _tool_create_risk(self, args: dict) -> dict:
        """Writes through the canonical risk register (app/services/risk_service.py)
        -- the one writer -- instead of creating a SolutionRisk row directly,
        so the superseded store gets no new row."""
        from app.modules.solutions_strategic.v2.routes.solution_routes import _level_to_int
        from app.services import risk_service

        description = args["risk_description"]
        risk = risk_service.create_risk(
            solution_id=args["solution_id"],
            title=description[:255],
            description=description,
            likelihood=_level_to_int(args["probability"]),
            impact=_level_to_int(args["impact"]),
            owner=None,
            mitigation_plan=args.get("mitigation") or None,
        )
        risk_service.add_risk_link(risk.id, "solution", args["solution_id"])
        return {
            "success": True,
            "result": {"id": risk.id, "entity_type": "risk", "solution_id": args["solution_id"]},
            "message": f"Added risk '{args['risk_description'][:60]}' (impact={args['impact']}) to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: create_option                                                 #
    # ------------------------------------------------------------------ #

    def _tool_create_option(self, args: dict) -> dict:
        from app.models.solution_architect_models import (
            SolutionRecommendation, RecommendationOptionType, SolutionAnalysisSession,
        )
        session_name = f"Agent session — solution {args['solution_id']}"
        session = SolutionAnalysisSession.query.filter_by(name=session_name).first()
        if not session:
            self._get_or_create_problem_id(args["solution_id"])
            session = SolutionAnalysisSession.query.filter_by(name=session_name).first()
        rec = SolutionRecommendation(
            session_id=session.id,
            name=args["name"],
            option_type=RecommendationOptionType(args["option_type"]),
            is_recommended=False,
        )
        db.session.add(rec)
        db.session.commit()
        return {
            "success": True,
            "result": {"id": rec.id, "name": rec.name, "entity_type": "option", "solution_id": args["solution_id"]},
            "message": f"Added option '{rec.name}' ({args['option_type']}) to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: mark_option_recommended                                       #
    # ------------------------------------------------------------------ #

    def _tool_mark_option_recommended(self, args: dict) -> dict:
        from app.models.solution_architect_models import SolutionRecommendation, SolutionAnalysisSession
        session = SolutionAnalysisSession.query.filter_by(solution_id=args["solution_id"]).first()
        if not session:
            return {"success": False, "error": "No options found for this solution — create options first."}
        options = SolutionRecommendation.query.filter_by(session_id=session.id).all()
        if not options:
            return {"success": False, "error": "No options found for this solution — create options first."}
        option_name = args["option_name"].lower()
        match = next(
            (o for o in options if option_name in o.name.lower() or o.name.lower() in option_name),
            None,
        )
        if not match:
            names = [o.name for o in options]
            return {"success": False, "error": f"Option '{args['option_name']}' not found. Available: {names}"}
        for o in options:
            o.is_recommended = False
        match.is_recommended = True
        db.session.commit()
        return {
            "success": True,
            "result": {"id": match.id, "name": match.name, "entity_type": "option"},
            "message": f"Marked '{match.name}' as the recommended option for solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: link_application_to_solution                                 #
    # ------------------------------------------------------------------ #

    def _tool_link_application_to_solution(self, args: dict) -> dict:
        app_r = self._resolver.resolve_application(args["application_name"])
        if not app_r["resolved"]:
            return self._clarify("application", app_r)
        from app.models.solution_models import Solution
        sol = Solution.query.get(args["solution_id"])
        if not sol:
            return {"success": False, "error": f"Solution {args['solution_id']} not found."}
        from app.models.application_component_fast import ApplicationComponent
        app_obj = ApplicationComponent.query.get(app_r["id"])
        if app_obj in sol.applications:
            return {
                "success": True,
                "result": {"application": app_r["name"], "entity_type": "application_link"},
                "message": f"'{app_r['name']}' is already linked to this solution.",
            }
        sol.applications.append(app_obj)
        db.session.commit()
        return {
            "success": True,
            "result": {"id": app_r["id"], "name": app_r["name"], "entity_type": "application_link", "solution_id": args["solution_id"]},
            "message": f"Linked application '{app_r['name']}' to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: link_vendor_product                                           #
    # ------------------------------------------------------------------ #

    def _tool_link_vendor_product(self, args: dict) -> dict:
        vp_r = self._resolver.resolve_vendor_product(args["vendor_product_name"])
        if not vp_r["resolved"]:
            return self._clarify("vendor_product", vp_r)
        from app.models.solution_models import Solution
        from app.models.vendor.vendor_organization import VendorProduct
        sol = Solution.query.get(args["solution_id"])
        if not sol:
            return {"success": False, "error": f"Solution {args['solution_id']} not found."}
        vp = VendorProduct.query.get(vp_r["id"])
        if vp in sol.vendor_products:
            return {
                "success": True,
                "result": {"vendor_product": vp_r["name"], "entity_type": "vendor_product_link"},
                "message": f"'{vp_r['name']}' is already linked to this solution.",
            }
        sol.vendor_products.append(vp)
        db.session.commit()
        return {
            "success": True,
            "result": {"id": vp_r["id"], "name": vp_r["name"], "entity_type": "vendor_product_link", "solution_id": args["solution_id"]},
            "message": f"Linked vendor product '{vp_r['name']}' to solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: run_inference_engine                                          #
    # ------------------------------------------------------------------ #

    def _tool_run_inference_engine(self, args: dict) -> dict:
        from app.models.solution_archimate_element import SolutionArchiMateElement
        from app.modules.architecture.services.inference_engine_service import ArchiMateInferenceEngine

        dry_run = args.get("dry_run", False)
        links = SolutionArchiMateElement.query.filter_by(solution_id=args["solution_id"]).all()
        if not links:
            return {
                "success": True,
                "result": {"elements_processed": 0},
                "message": "No ArchiMate elements linked to this solution. Link elements first.",
            }

        total_created = 0
        total_relationships = 0
        errors = []
        engine = ArchiMateInferenceEngine(architecture_id=0)

        for link in links:
            try:
                result = engine.repair(link.element_id, dry_run=dry_run)
                total_created += result.get("created", 0)
                total_relationships += result.get("relationships_created", 0)
            except Exception as e:
                errors.append(str(e))

        action = "Would create" if dry_run else "Created"
        return {
            "success": True,
            "result": {
                "elements_processed": len(links),
                "elements_created": total_created,
                "relationships_created": total_relationships,
                "dry_run": dry_run,
                "errors": errors[:3],
            },
            "message": (
                f"Inference engine ran on {len(links)} elements. "
                f"{action} {total_created} new elements and {total_relationships} relationships."
                + (f" {len(errors)} errors." if errors else "")
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: generate_blueprint_narrative (approve tier)                  #
    # ------------------------------------------------------------------ #

    def _tool_generate_blueprint_narrative(self, args: dict) -> dict:
        solution_id = args["solution_id"]
        section_id = args["section_id"]
        try:
            from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
                NarrativeGenerationError,
                generate_section_narrative,
            )
        except ImportError:
            logger.exception("generate_blueprint_narrative: narrative generator unavailable")
            return {
                "success": False,
                "error": "Narrative generation is not available in this build.",
                "error_code": "UNAVAILABLE",
            }

        try:
            result = generate_section_narrative(solution_id, section_id, self.user_id)
        except NarrativeGenerationError as e:
            logger.warning(
                "generate_blueprint_narrative failed for solution %s section %s: %s",
                solution_id, section_id, e,
            )
            return {"success": False, "error": str(e), "error_code": e.error_code}
        except Exception:
            logger.exception(
                "generate_blueprint_narrative: unexpected failure for solution %s section %s",
                solution_id, section_id,
            )
            return {
                "success": False,
                "error": "Narrative generation failed unexpectedly.",
                "error_code": "INTERNAL_ERROR",
            }

        return {
            "success": True,
            "result": {
                "solution_id": solution_id,
                "section_id": section_id,
                "narrative": result["narrative"],
                "word_count": result["word_count"],
            },
            "message": f"Generated narrative for section '{section_id}' of solution {solution_id}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: create_archimate_relationship                                 #
    # ------------------------------------------------------------------ #

    def _tool_create_archimate_relationship(self, args: dict) -> dict:
        src_r = self._resolver.resolve_archimate_element(args["source_element_name"])
        tgt_r = self._resolver.resolve_archimate_element(args["target_element_name"])
        if not src_r["resolved"]:
            return self._clarify("source ArchiMate element", src_r)
        if not tgt_r["resolved"]:
            return self._clarify("target ArchiMate element", tgt_r)
        # Validate against the ArchiMate 3.2 metamodel BEFORE writing.
        #
        # app/config/archimate_relationship_matrix.py has carried the full
        # specification matrix all along -- "the authoritative source for
        # validating ArchiMate relationships" -- and this path never called it.
        # The capability was built and never wired, the same way AI auto-mapping
        # was built and offered on one screen out of twenty-five.
        #
        # It matters more now than it did: the assistant can create all 54
        # element types, so without this it can wire a Node to a Goal by
        # realization and produce a model that renders correctly and is
        # nonsense. A wrong model that validates is worse than an incomplete
        # one -- the user this product exists for cannot tell the difference.
        #
        # On refusal we return what WOULD be valid rather than a bare error, so
        # the model corrects itself on the next turn instead of guessing again.
        try:
            from app.config.archimate_relationship_matrix import (
                get_valid_relationships,
                is_valid_relationship,
            )

            def _pascal(value: str) -> str:
                """snake_case or kebab-case to PascalCase, leaving PascalCase alone.

                str.capitalize() LOWERCASES the remainder, so a value already
                stored as "ApplicationComponent" became "Applicationcomponent"
                and matched nothing in the matrix. That refused
                ApplicationComponent -realization-> ApplicationService, which is
                legal -- a validator that blocks correct modelling is worse than
                no validator, because it teaches the assistant that the
                metamodel forbids things it permits.
                """
                text = str(value or "").replace("-", "_")
                if "_" not in text and text[:1].isupper():
                    return text  # already PascalCase
                return "".join(part[:1].upper() + part[1:] for part in text.split("_") if part)

            source_type = _pascal(src_r.get("type") or src_r.get("element_type"))
            target_type = _pascal(tgt_r.get("type") or tgt_r.get("element_type"))
            wanted = args["relationship_type"]
            if source_type and target_type and not is_valid_relationship(
                source_type, target_type, wanted
            ):
                allowed = get_valid_relationships(source_type, target_type)
                return {
                    "success": False,
                    "error": (
                        "ArchiMate 3.2 does not permit a %s relationship from a "
                        "%s to a %s." % (wanted, source_type, target_type)
                    ),
                    "valid_relationship_types": allowed,
                    "message": (
                        "Refused: %s -%s-> %s is not legal in ArchiMate 3.2. %s"
                        % (
                            src_r["name"], wanted, tgt_r["name"],
                            ("Valid here: %s." % ", ".join(allowed)) if allowed
                            else "No relationship is permitted between these two "
                                 "element types; the model may need an "
                                 "intermediate element.",
                        )
                    ),
                }
        except ImportError:
            logger.warning("relationship matrix unavailable; creating unvalidated")

        from app.modules.architecture.services.inference_engine_service import ArchiMateInferenceEngine
        engine = ArchiMateInferenceEngine(architecture_id=0)
        try:
            # rel_type / metadata, which is what the facade actually takes.
            #
            # This called get_or_create_relationship(relationship_type=...,
            # provenance=..., confidence=...) -- none of which are parameters.
            # Every invocation raised TypeError and was swallowed into
            # {"success": False}, so the assistant has NEVER once created a
            # relationship. It could produce elements and never connect them,
            # which is a bag of nodes rather than an architecture. Found by
            # calling the tool rather than by reading it.
            engine.graph.get_or_create_relationship(
                source_id=src_r["id"],
                target_id=tgt_r["id"],
                rel_type=args["relationship_type"],
                metadata={"provenance": "agent", "confidence": 0.9},
            )
            db.session.commit()
            return {
                "success": True,
                "result": {"source": src_r["name"], "target": tgt_r["name"], "type": args["relationship_type"]},
                "message": (
                    f"Created {args['relationship_type']} relationship: "
                    f"'{src_r['name']}' → '{tgt_r['name']}'."
                ),
            }
        except Exception as e:
            db.session.rollback()
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Tool: diagnose_chain (read-only)                                   #
    # ------------------------------------------------------------------ #

    def _tool_diagnose_chain(self, args: dict) -> dict:
        elem_r = self._resolver.resolve_archimate_element(args["element_name"])
        if not elem_r["resolved"]:
            return self._clarify("ArchiMate element", elem_r)
        from app.modules.architecture.services.inference_engine_service import ArchiMateInferenceEngine
        engine = ArchiMateInferenceEngine(architecture_id=0)
        result = engine.diagnose(elem_r["id"])
        return {
            "success": True,
            "result": result,
            "message": (
                f"Chain diagnosis for '{elem_r['name']}': "
                f"{result.get('completeness_score', 0):.0%} complete. "
                f"Missing: {result.get('missing_elements', [])}."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: explain_element (read-only)                                  #
    # ------------------------------------------------------------------ #

    def _tool_explain_element(self, args: dict) -> dict:
        elem_r = self._resolver.resolve_archimate_element(args["element_name"])
        if not elem_r["resolved"]:
            return self._clarify("ArchiMate element", elem_r)
        from app.modules.architecture.services.inference_engine_service import ArchiMateInferenceEngine
        engine = ArchiMateInferenceEngine(architecture_id=0)
        result = engine.explain(elem_r["id"])
        return {
            "success": True,
            "result": result,
            "message": f"Provenance chain for '{elem_r['name']}' traced.",
        }

    # ------------------------------------------------------------------ #
    # Tool: simulate_impact (read-only)                                  #
    # ------------------------------------------------------------------ #

    def _tool_simulate_impact(self, args: dict) -> dict:
        elem_r = self._resolver.resolve_archimate_element(args["element_name"])
        if not elem_r["resolved"]:
            return self._clarify("ArchiMate element", elem_r)
        from app.modules.architecture.services.inference_engine_service import ArchiMateInferenceEngine
        engine = ArchiMateInferenceEngine(architecture_id=0)
        result = engine.simulate_change_impact(elem_r["id"], scope="both")
        affected = result.get("affected_count", 0)
        return {
            "success": True,
            "result": result,
            "message": (
                f"Impact simulation for '{elem_r['name']}': "
                f"{affected} downstream elements affected across all layers."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: get_solution_summary (read-only)                             #
    # ------------------------------------------------------------------ #

    def _tool_get_solution_summary(self, args: dict) -> dict:
        from app.models.solution_models import Solution
        sol = Solution.query.get(args["solution_id"])
        if not sol:
            return {"success": False, "error": f"Solution {args['solution_id']} not found."}
        return {
            "success": True,
            "result": {
                "id": sol.id,
                "name": sol.name,
                "governance_status": sol.governance_status,
                "adm_phase": sol.adm_phase,
                "maturity_level": sol.maturity_current,
                "applications_count": _safe_count(sol.applications),
                "risks_count": _safe_count(sol.risks) if hasattr(sol, "risks") else None,
            },
            "message": (
                f"Solution '{sol.name}': phase={sol.adm_phase}, "
                f"CMM maturity={sol.maturity_current or 0}/5, governance={sol.governance_status}."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: get_completeness_score (read-only)                           #
    # ------------------------------------------------------------------ #

    def _tool_get_completeness_score(self, args: dict) -> dict:
        try:
            from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
                BlueprintCompletenessService,
            )
            svc = BlueprintCompletenessService()
            scores = svc.score_all(args["solution_id"])
            # Summarise: overall = mean of section overalls
            section_scores = {k: v.get("overall", 0) for k, v in scores.items()}
            overall = round(sum(section_scores.values()) / max(len(section_scores), 1))
            return {
                "success": True,
                "result": {"overall_pct": overall, "sections": section_scores},
                "message": f"Overall completeness: {overall}%. Lowest sections: "
                           + ", ".join(
                               f"{k}={v}%" for k, v in sorted(section_scores.items(), key=lambda x: x[1])[:3]
                           ),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Tool: update_solution_fields                                        #
    # ------------------------------------------------------------------ #

    def _tool_update_solution_fields(self, args: dict) -> dict:
        from app.models.solution_models import Solution
        sol = Solution.query.get(args["solution_id"])
        if not sol:
            return {"success": False, "error": f"Solution {args['solution_id']} not found."}
        updatable = ["solution_owner", "business_sponsor", "technical_lead", "description"]
        updated = []
        for field in updatable:
            if field in args and args[field]:
                setattr(sol, field, args[field])
                updated.append(field)
        if not updated:
            return {"success": False, "error": "No fields provided to update."}
        db.session.commit()
        return {
            "success": True,
            "result": {"updated_fields": updated},
            "message": f"Updated {', '.join(updated)} on solution {args['solution_id']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: update_solution_phase                                         #
    # ------------------------------------------------------------------ #

    def _tool_update_solution_phase(self, args: dict) -> dict:
        from app.models.solution_models import Solution
        sol = Solution.query.get(args["solution_id"])
        if not sol:
            return {"success": False, "error": f"Solution {args['solution_id']} not found."}
        old_phase = sol.adm_phase
        sol.adm_phase = args["phase"]
        db.session.commit()
        return {
            "success": True,
            "result": {"old_phase": old_phase, "new_phase": args["phase"]},
            "message": f"Advanced solution {args['solution_id']} from phase {old_phase} to {args['phase']}.",
        }

    # ------------------------------------------------------------------ #
    # Tool: search_archimate_elements (read-only)                        #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Tool: search_capabilities_by_problem (read-only, semantic)          #
    # ------------------------------------------------------------------ #

    def _tool_search_capabilities_by_problem(self, args: dict) -> dict:
        """
        Semantic search over the business capability catalog using SentenceTransformer
        cosine similarity. Falls back to keyword ILIKE if embeddings unavailable.
        Returns top-N capabilities ranked by relevance to the problem description.
        """
        from app.models.business_capabilities import BusinessCapability
        from app.models.application_capability import ApplicationCapabilityMapping

        query_text = args.get("problem_description", "")
        limit = min(args.get("limit", 10), 25)

        if not query_text.strip():
            return {"success": False, "error": "problem_description is required"}

        # The candidate pool is capped at 600 with no ORDER BY, so on a larger
        # capability model this searches an arbitrary subset. Count the real
        # total so the answer can say which, rather than implying the search
        # covered everything.
        _cap_q = BusinessCapability.query.filter(BusinessCapability.name.isnot(None))
        total_capabilities = _cap_q.count()
        CANDIDATE_POOL = 600
        caps = _cap_q.limit(CANDIDATE_POOL).all()

        if not caps:
            return {"success": False, "error": "No capabilities found in platform"}

        # --- Attempt semantic ranking via SentenceTransformer ---
        try:
            import numpy as np
            from app.services.vector_embedding_service import VectorEmbeddingService

            svc = VectorEmbeddingService()
            texts = [
                f"{c.name} {c.description or ''} {c.business_domain or ''}".strip()
                for c in caps
            ]
            query_vec = np.array(svc.embed_text(query_text))
            cap_vecs = np.array([svc.embed_text(t) for t in texts])

            # Cosine similarity (vectors already L2-normalised by all-MiniLM)
            scores = cap_vecs @ query_vec
            top_idx = np.argsort(scores)[::-1][:limit]

            rows = []
            for i in top_idx:
                c = caps[i]
                # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                app_count = ApplicationCapabilityMapping.query.filter_by(
                    business_capability_id=c.id
                ).count()
                rows.append({
                    "id": c.id,
                    "name": c.name,
                    "business_domain": c.business_domain,
                    "current_maturity": c.current_maturity_level,
                    "target_maturity": c.target_maturity_level,
                    "gap": max(0, (c.target_maturity_level or 3) - (c.current_maturity_level or 1)),
                    "strategic_importance": c.strategic_importance,
                    "supporting_apps": app_count,
                    "relevance_score": round(float(scores[i]), 3),
                })
            method = "semantic"

        except Exception as embed_err:
            logger.warning("Semantic capability search fell back to keyword: %s", embed_err)
            # Keyword fallback — split query into tokens, ILIKE each
            tokens = [t for t in query_text.lower().split() if len(t) > 3][:6]
            q = BusinessCapability.query
            if tokens:
                from sqlalchemy import or_
                conditions = [
                    BusinessCapability.name.ilike(f"%{t}%") for t in tokens
                ] + [
                    BusinessCapability.description.ilike(f"%{t}%") for t in tokens
                ]
                q = q.filter(or_(*conditions))
            caps_kw = q.limit(limit).all()
            rows = []
            for c in caps_kw:
                # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                app_count = ApplicationCapabilityMapping.query.filter_by(
                    business_capability_id=c.id
                ).count()
                rows.append({
                    "id": c.id,
                    "name": c.name,
                    "business_domain": c.business_domain,
                    "current_maturity": c.current_maturity_level,
                    "target_maturity": c.target_maturity_level,
                    "gap": max(0, (c.target_maturity_level or 3) - (c.current_maturity_level or 1)),
                    "strategic_importance": c.strategic_importance,
                    "supporting_apps": app_count,
                })
            method = "keyword"

        searched = len(caps)
        pool_note = (
            f" Ranked against {searched} of {total_capabilities} capabilities"
            f" ({method} search) - the remainder was not examined."
            if total_capabilities > searched
            else f" Ranked against all {searched} capabilities ({method} search)."
        )
        return {
            "success": True,
            "result": rows,
            "search_method": method,
            "capabilities_searched": searched,
            "capabilities_total": total_capabilities,
            # total=None: these are the top matches by relevance, not "all
            # matching rows", so there is no meaningful total to report.
            **self._coverage(rows, None, "relevant capabilities"),
            "message": (
                f"Top {len(rows)} capabilities by relevance to the problem."
                + pool_note
                + " Use link_capability_to_solution to attach the relevant ones."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: find_applications_by_capability (read-only)                   #
    # ------------------------------------------------------------------ #

    def _tool_find_applications_by_capability(self, args: dict) -> dict:
        """
        Returns all applications mapped to a named capability.
        Grounds Phase 4 in the real application catalog instead of invented names.
        """
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.application_component_fast import ApplicationComponent

        cap_name = args.get("capability_name", "")
        if not cap_name.strip():
            return {"success": False, "error": "capability_name is required"}

        cap_r = self._resolver.resolve_capability(cap_name)
        if not cap_r.get("resolved"):
            return {
                "success": False,
                "error": f"Capability '{cap_name}' not found. Use search_capabilities_by_problem to find the right name.",
            }

        cap_id = cap_r["id"]
        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        _map_q = ApplicationCapabilityMapping.query.filter_by(business_capability_id=cap_id)
        total_mapped = _map_q.count()
        mappings = _map_q.limit(30).all()

        if not mappings:
            return {
                "success": True,
                "result": [],
                "count": 0,
                "capability_name": cap_r.get("name", cap_name),
                "message": f"No applications currently mapped to '{cap_r.get('name', cap_name)}'. This is a coverage gap.",
            }

        app_ids = [m.application_component_id for m in mappings]
        apps = ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids)
        ).all()

        app_map = {a.id: a for a in apps}
        rows = []
        for m in mappings:
            a = app_map.get(m.application_component_id)
            if not a:
                continue
            rows.append({
                "id": a.id,
                "name": a.name,
                "deployment_status": a.deployment_status,
                "coverage_level": getattr(m, "coverage_level", None),
                "owner_team": getattr(a, "owner_team", None),
            })

        coverage = self._coverage(
            rows, total_mapped, f"application(s) mapped to '{cap_r.get('name', cap_name)}'"
        )
        return {
            "success": True,
            "result": rows,
            "capability_id": cap_id,
            "capability_name": cap_r.get("name", cap_name),
            **coverage,
            "message": (
                coverage["message"]
                + " Use link_application_to_solution to attach relevant ones to your solution."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: find_technical_capabilities (Phase 5 grounding)              #
    # ------------------------------------------------------------------ #

    def _tool_find_technical_capabilities(self, args: dict) -> dict:
        """
        Return L1/L2 technical capabilities from the ACM taxonomy, optionally
        filtered by domain or keyword. Each result includes how many applications
        in the catalog already cover it — gaps (0 apps) are flagged explicitly.
        """
        from app.models.technical_capability import TechnicalCapability

        domain = args.get("domain")
        query = args.get("query", "").strip().lower()
        limit = min(args.get("limit", 15), 30)

        q = TechnicalCapability.query.filter(
            TechnicalCapability.level.in_(["L1", "L2"])
        )
        if domain:
            q = q.filter(TechnicalCapability.acm_domain == domain)
        if query:
            q = q.filter(
                db.or_(
                    TechnicalCapability.name.ilike(f"%{query}%"),
                    TechnicalCapability.description.ilike(f"%{query}%"),
                )
            )

        total = q.count()
        caps = q.order_by(
            TechnicalCapability.acm_domain,
            TechnicalCapability.level_number,
            TechnicalCapability.code,
        ).limit(limit).all()

        rows = []
        for c in caps:
            app_count = db.session.execute(
                db.text(
                    "SELECT COUNT(*) FROM application_technical_capability_mapping "
                    "WHERE technical_capability_id = :cid"
                ),
                {"cid": c.id},
            ).scalar() or 0
            rows.append({
                "id": c.id,
                "name": c.name,
                "domain": c.acm_domain,
                "level": c.level,
                "code": c.code,
                "description": c.description,
                "apps_covering": app_count,
                "is_gap": app_count == 0,
            })

        gaps = [r for r in rows if r["is_gap"]]
        coverage = self._coverage(
            rows,
            total,
            "technical capabilities" + (f" in domain '{domain}'" if domain else ""),
        )
        return {
            "success": True,
            "result": rows,
            **coverage,
            "gaps_count": len(gaps),
            # gaps_count counts only the rows shown, so say so when truncated -
            # otherwise it reads as the gap count for the whole domain.
            "gaps_count_scope": "shown rows only" if coverage["truncated"] else "complete",
            "message": (
                coverage["message"]
                + f" {len(gaps)} of the {len(rows)} shown have zero app coverage (gaps). "
                "Use create_archimate_element (type=Node/SystemSoftware/TechnologyService) "
                "to model technology components that address these gaps."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: verify_codegen                                                #
    # ------------------------------------------------------------------ #

    def _tool_verify_codegen(self, args: dict) -> dict:
        from app.services.codegen_verifier_service import CodegenVerifierService

        solution_id = args.get("solution_id")
        if not solution_id and args.get("solution_name"):
            resolved = self._resolver.resolve_solution(args["solution_name"])
            if isinstance(resolved, dict) and resolved.get("clarification_needed"):
                return resolved
            solution_id = resolved

        if not solution_id:
            return {"success": False, "error": "Provide solution_id or solution_name."}

        result = CodegenVerifierService.verify_solution(solution_id)
        if not result.get("success"):
            return result

        r = result["result"]
        grade_emoji = {"A": "✅", "B": "✅", "C": "⚠️", "D": "⚠️", "F": "🔴"}.get(r["grade"], "❓")
        result["message"] = (
            f"{grade_emoji} Codegen Score: {r['score']}/100 — Grade {r['grade']} "
            f"({r['coverage_pct']}% element coverage). "
            f"{r['archimate_element_count']} ArchiMate elements → ~{r['expected_route_count']} expected routes. "
            f"Findings: {r['findings_summary']['CRITICAL']} CRITICAL, {r['findings_summary']['HIGH']} HIGH."
        )
        return result

    # ------------------------------------------------------------------ #
    # Tool: propose_rationalization                                        #
    # ------------------------------------------------------------------ #

    def _tool_propose_rationalization(self, args: dict) -> dict:
        from app.services.rationalization_proposal_service import RationalizationProposalService
        limit = min(args.get("limit", 10), 25)
        return RationalizationProposalService.generate_proposals(limit=limit)

    # ------------------------------------------------------------------ #
    # Tool: build_architecture_plan                                        #
    # ------------------------------------------------------------------ #

    def _tool_build_architecture_plan(self, args: dict) -> dict:
        from app.services.orchestration_planner_service import OrchestrationPlannerService
        goal = args.get("goal", "")
        if not goal:
            return {"success": False, "error": "goal is required."}
        solution_id = args.get("solution_id")
        return OrchestrationPlannerService.build_plan(goal=goal, solution_id=solution_id)

    # ------------------------------------------------------------------ #
    # Tool: poll_infrastructure                                            #
    # ------------------------------------------------------------------ #

    def _tool_poll_infrastructure(self, args: dict) -> dict:
        from app.services.infrastructure_polling_service import InfrastructurePollingService
        return InfrastructurePollingService.poll_infrastructure(
            include_abacus=args.get("include_abacus", True),
            include_llm=args.get("include_llm", True),
            additional_urls=args.get("additional_urls"),
        )

    # ------------------------------------------------------------------ #
    # Tool: infer_schema                                                   #
    # ------------------------------------------------------------------ #

    def _tool_infer_schema(self, args: dict) -> dict:
        from app.services.schema_inference_service import SchemaInferenceService

        input_text = args.get("input_text", "").strip()
        if not input_text:
            return {"success": False, "error": "input_text is required."}

        fmt = args.get("format", "auto")
        if fmt == "auto":
            lower = input_text.lower()
            fmt = "ddl" if "create table" in lower else "openapi"

        if fmt == "ddl":
            result = SchemaInferenceService.infer_from_ddl(input_text)
            count = result.get("table_count", 0)
        else:
            result = SchemaInferenceService.infer_from_openapi(input_text)
            count = result.get("schema_count", 0)

        if result.get("success"):
            result["message"] = (
                f"Inferred {count} DataObject(s) from {fmt.upper()}. "
                f"Call create_archimate_element for each item in 'create_args' to persist them."
            )
            if args.get("solution_id"):
                result["message"] += (
                    f" Then link to solution {args['solution_id']} via link_archimate_elements_to_solution."
                )
        return result

    # ------------------------------------------------------------------ #

    def _tool_validate_sap_clean_core(self, args: dict) -> dict:
        from app.services.sap_clean_core_service import SAPCleanCoreService

        # Portfolio-level scan
        if args.get("include_portfolio_scan"):
            return SAPCleanCoreService.quick_scan_portfolio(limit=20)

        # Resolve solution_id
        solution_id = args.get("solution_id")
        if not solution_id and args.get("solution_name"):
            resolved = self._resolver.resolve_solution(args["solution_name"])
            if isinstance(resolved, dict) and resolved.get("clarification_needed"):
                return resolved
            solution_id = resolved

        if not solution_id:
            return {
                "success": False,
                "error": (
                    "Provide solution_id or solution_name to validate, "
                    "or set include_portfolio_scan=true for a portfolio-wide SAP clean-core scan."
                ),
            }

        result = SAPCleanCoreService.validate_solution(solution_id)
        if not result.get("success"):
            return result

        r = result["result"]
        # Build a concise message the LLM can narrate directly
        f_counts = r["findings_summary"]
        tier_emoji = {"CLEAN_CORE_COMPLIANT": "✅", "AT_RISK": "⚠️", "NON_COMPLIANT": "🔴"}.get(
            r["compliance_tier"], "❓"
        )
        result["message"] = (
            f"{tier_emoji} SAP Clean-Core Score: {r['score']}/100 — {r['compliance_tier'].replace('_', ' ')}. "
            f"Upgrade risk: {r['upgrade_risk']}. "
            f"Findings: {f_counts['CRITICAL']} CRITICAL, {f_counts['HIGH']} HIGH, "
            f"{f_counts['MEDIUM']} MEDIUM, {f_counts['INFO']} INFO."
        )
        return result

    # ------------------------------------------------------------------ #

    def _tool_search_archimate_elements(self, args: dict) -> dict:
        try:
            from app.models.archimate_core import ArchiMateElement
        except ImportError:
            from app.models.models import ArchiMateElement

        limit = min(args.get("limit", 15), 50)
        q = ArchiMateElement.query

        if args.get("name_contains"):
            q = q.filter(ArchiMateElement.name.ilike(f"%{args['name_contains']}%"))
        if args.get("layer"):
            q = q.filter(ArchiMateElement.layer == args["layer"])
        if args.get("element_type"):
            q = q.filter(ArchiMateElement.type == args["element_type"])

        total = q.count()
        elements = q.limit(limit).all()
        rows = [{"id": e.id, "name": e.name, "type": e.type, "layer": e.layer} for e in elements]
        return {
            "success": True,
            "result": rows,
            **self._coverage(rows, total, "ArchiMate element(s)"),
        }


    # ------------------------------------------------------------------ #
    # Genome patch (ADR 0009 / 0010)                                      #
    # ------------------------------------------------------------------ #

    def _tool_propose_genome_patch(self, args: dict) -> dict:
        """Validate an LLM-proposed genome patch and QUEUE it for approval.

        Non-mutating with respect to the enterprise model: it only creates a
        pending approval (or rejects an invalid patch). The actual model write
        happens later, in `_tool_apply_genome_patch`, once a human approves —
        exactly the existing approve-tier queue/confirm mechanism.

        The LLM supplies the candidate patch as ``args["patch"]`` (or as the
        argument object itself). Whatever it emits is validated deterministically
        before anything is queued, so a hallucinated patch cannot reach the model.
        """
        from app.modules.genome.patch.proposer import propose_genome_patch

        request_text = args.get("request") or "propose a genome patch"

        supplied = args.get("patch")
        if isinstance(supplied, dict):
            # The model supplied the full patch -- pass it through verbatim.
            return propose_genome_patch(
                request_text=request_text,
                user_id=self.user_id,
                patch_source=lambda *_a, **_k: supplied,
            )

        # No patch supplied: synthesize one from the prose via the LLM, whose
        # default patch source reads organization_id + proposed_by from context
        # and forces target to the acting org.
        return propose_genome_patch(
            request_text=request_text,
            user_id=self.user_id,
            context={
                "organization_id": self._get_organization_id(),
                "proposed_by": str(self.user_id),
            },
        )

    def _tool_apply_genome_patch(self, args: dict) -> dict:
        """Apply an APPROVED genome patch to the model (with provenance).

        Reached only through the approval gate: AgentRunner/proposer queue an
        ``operation_type="tool_use"`` approval whose ``entity_type`` is
        ``apply_genome_patch`` and whose payload is the validated patch;
        ``AIChatApprovalService.approve_and_execute`` builds a ToolCall for it
        and dispatches here. `args` IS the patch dict.
        """
        from app.modules.genome.patch.applier import apply_genome_patch

        return apply_genome_patch(args, self.user_id)

    # ------------------------------------------------------------------ #
    # Governance / executive READ tools (Capability-Gap Register G1)       #
    #                                                                      #
    # These wrap services that already exist but had no AI binding, so the #
    # copilot could not answer the personas' headline questions. All are   #
    # read-only (mutates=False) and never write to db.session.             #
    # ------------------------------------------------------------------ #

    def _tool_get_investment_priorities(self, args: dict) -> dict:
        """CTO headline: ranked capability investment priorities for THIS org.

        Wraps InvestmentPrioritizationService.analyze_investment_priorities()
        via the architecture blueprint's own helpers. That service reads
        UnifiedCapability / UnifiedApplicationCapabilityMapping, NEITHER of
        which carries organization_id — so the raw analysis spans every org on
        the install. We reuse _org_scoped_investment_context() (the same
        org-name derivation the dashboard's AI-suggest endpoint uses) so only
        capabilities THIS org has actually mapped ever reach the model.
        """
        from app.modules.architecture.routes.architecture_routes import (
            _assemble_investment_priorities_context,
            _org_scoped_investment_context,
        )

        limit = int(args.get("limit", 25) or 25)

        analysis, mapping_count = _assemble_investment_priorities_context()
        if analysis is None:
            # Honest empty state — no fabricated numbers.
            return {
                "success": True,
                "result": {
                    "ranked": [],
                    "split": None,
                    "mapping_count": mapping_count,
                },
                "message": (
                    "No capability mappings exist yet, so investment priorities "
                    "cannot be computed. This is not zero priority — it is not "
                    "yet measurable."
                ),
            }

        org_context = _org_scoped_investment_context(analysis, limit=limit)
        if org_context is None:
            return {
                "success": True,
                "result": {"ranked": [], "split": None, "mapping_count": mapping_count},
                "message": "No capabilities are mapped in this organization yet.",
            }

        scores = org_context["capability_scores"]
        ranked = [
            {
                "capability_name": c.get("capability_name"),
                "priority_level": c.get("priority_level"),
                "investment_priority_score": c.get("investment_priority_score"),
                "estimated_cost": c.get("estimated_cost"),
                "timeframe_months": c.get("timeframe"),
            }
            for c in scores
        ]
        # The service's real posture buckets are the four priority tiers, not an
        # "invest/stall" pair — report what the service actually produces.
        split = {
            "critical": len(org_context["critical_investments"]),
            "high": len(org_context["high_investments"]),
            "medium": len(org_context["medium_investments"]),
            "low": len(org_context["low_investments"]),
        }
        return {
            "success": True,
            "result": {
                "ranked": ranked,
                "split": split,
                "recommendations": org_context.get("recommendations", []),
                "mapping_count": mapping_count,
            },
            "message": (
                f"{len(ranked)} capability investment priorities for this "
                f"organization — {split['critical']} critical, {split['high']} high, "
                f"{split['medium']} medium, {split['low']} low."
            ),
        }

    def _tool_get_executive_dashboard(self, args: dict) -> dict:
        """CTO/CIO one-call executive summary.

        Wraps ExecutiveDashboardService.get_executive_summary(), whose metric
        methods each query TenantMixin models (Solution, SolutionRisk,
        ARBReviewItem, ...) inside this request context and are therefore
        org-scoped by the ORM tenant filter. The service already returns None
        for pieces it cannot compute, so we pass its shape through unchanged —
        no metric is fabricated to fill a gap.
        """
        from app.modules.dashboard.v2.services.executive_dashboard_service import (
            ExecutiveDashboardService,
        )

        summary = ExecutiveDashboardService().get_executive_summary()
        return {
            "success": True,
            "result": {
                "portfolio_health": summary.get("architecture_health"),
                "portfolio_stats": summary.get("portfolio_stats"),
                "programme_progress": summary.get("programme_progress"),
                "arb_pipeline": summary.get("pending_decisions"),
                "top_risks": summary.get("risk_posture"),
                "capability_coverage": summary.get("capability_coverage"),
            },
            "message": (
                "Executive summary: portfolio health, ARB pipeline and risk "
                "posture. Fields that read null were not computable and must be "
                "shown as an em dash, never as zero."
            ),
        }

    def _tool_get_arb_status(self, args: dict) -> dict:
        """Read a solution's ARB review status, decision and conditions.

        Closes 'ARB is write-only to the AI' — the copilot could submit_for_arb
        but never read the outcome back. ARBReviewItem is a TenantMixin model,
        so this query is org-scoped automatically inside the request context.
        """
        from app.models.architecture_review_board import ARBReviewItem

        solution_id = args.get("solution_id")
        if not solution_id:
            return {"success": False, "error": "solution_id is required."}

        items = (
            ARBReviewItem.query.filter_by(solution_id=solution_id)
            .order_by(ARBReviewItem.submitted_at.desc().nullslast())
            .all()
        )
        if not items:
            return {
                "success": True,
                "result": {"solution_id": solution_id, "reviews": []},
                "message": (
                    f"Solution {solution_id} has no ARB review items in this "
                    f"organization."
                ),
            }

        reviews = [
            {
                "review_number": it.review_number,
                "title": it.title,
                "status": it.status,
                "decision": it.decision,
                "decision_rationale": it.decision_rationale,
                "conditions": it.conditions,
                "decision_date": it.decision_date.isoformat() if it.decision_date else None,
                "overall_score": it.overall_score,
            }
            for it in items
        ]
        latest = reviews[0]
        return {
            "success": True,
            "result": {"solution_id": solution_id, "reviews": reviews, "latest": latest},
            "message": (
                f"Solution {solution_id}: latest ARB review {latest['review_number']} "
                f"is '{latest['status']}'"
                + (f", decision '{latest['decision']}'." if latest["decision"] else ".")
            ),
        }

    # ------------------------------------------------------------------ #
    # Governance WRITE tool (Capability-Gap Register G2)                   #
    # ------------------------------------------------------------------ #

    def _tool_create_adr(self, args: dict) -> dict:
        """Author an Architecture Decision Record — solution_architect headline.

        Wraps ADRService.create_adr(). mutates=True / tier 'approve', so it
        flows through the existing confirmation gate. ArchitectureDecision is a
        TenantMixin model, so organization_id is auto-set from the acting org on
        flush — the ADR cannot land in another tenant.

        NOTE (brief deviation): the real service takes a required `rationale`
        and has NO `status` argument — every ADR is created status='proposed'
        and moves via approve/reject. This tool therefore drops `status` and
        adds `rationale` (+ optional decision_type) to match the real signature.
        """
        from app.services.adr_service import ADRService

        solution_id = args.get("solution_id")
        title = args.get("title")
        if not solution_id or not title:
            return {"success": False, "error": "solution_id and title are required."}

        adr = ADRService.create_adr(
            solution_id=solution_id,
            title=title,
            context=args.get("context") or "",
            decision=args.get("decision") or "",
            rationale=args.get("rationale") or "",
            decision_type=args.get("decision_type") or "technology_choice",
            consequences=args.get("consequences"),
        )
        return {
            "success": True,
            "result": {
                "id": adr.id,
                "title": adr.title,
                "status": adr.status,
                "solution_id": adr.solution_id,
                "decision_type": adr.decision_type,
            },
            "message": f"Created ADR {adr.id} '{adr.title}' (status={adr.status}).",
        }

    # ------------------------------------------------------------------ #
    # Tool: record_capability_maturity (WRITE — G2)                       #
    # ------------------------------------------------------------------ #
    def _tool_record_capability_maturity(self, args: dict) -> dict:
        """Record a maturity assessment on a business capability — EA/BA headline.

        Mirrors the write path in
        app/modules/capabilities/routes/maturity_routes.py::batch_update_maturity:
        it writes current_maturity_level / target_maturity_level, recomputes
        maturity_gap, and stamps maturity_assessment_date. mutates=True / tier
        'approve', so it flows through the confirmation gate.

        DEVIATION from the task brief: the brief cited current_maturity /
        target_maturity on app/models/capabilities.py. Those columns live on
        ArchiMateCapability (table archimate_capabilities). The canonical
        BusinessCapability (app/models/business_capabilities.py, table
        business_capability) — the one the maturity routes and the heatmap read
        and write — uses current_maturity_level / target_maturity_level, so this
        tool writes those. BusinessCapability is a TenantMixin model, so the ORM
        tenant filter scopes the lookup: a capability in another org is invisible
        and reads back as not-found, never cross-written.
        """
        from datetime import datetime

        from app.models.business_capabilities import BusinessCapability

        capability_id = args.get("capability_id")
        current = args.get("current_maturity")
        target = args.get("target_maturity")

        if capability_id is None or current is None:
            return {
                "success": False,
                "error": "capability_id and current_maturity are required.",
            }

        # Validate the 1-5 range; reject out-of-range rather than clamp.
        def _valid(v):
            return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 5

        if not _valid(current):
            return {
                "success": False,
                "error": "current_maturity must be an integer between 1 and 5.",
            }
        if target is not None and not _valid(target):
            return {
                "success": False,
                "error": "target_maturity must be an integer between 1 and 5.",
            }

        # Tenant filter is applied by the ORM (TenantMixin) — do not hand-write an
        # organization_id predicate (would double-filter). .filter_by().first()
        # rather than .get(), which is scoped only on an identity-map miss.
        cap = BusinessCapability.query.filter_by(id=capability_id).first()
        if cap is None:
            return {
                "success": False,
                "error": f"Business capability {capability_id} not found.",
            }

        cap.current_maturity_level = current
        if target is not None:
            cap.target_maturity_level = target
        # Recompute the gap only when both ends are real; never substitute 0.
        if cap.current_maturity_level is not None and cap.target_maturity_level is not None:
            cap.maturity_gap = cap.target_maturity_level - cap.current_maturity_level
        cap.maturity_assessment_date = datetime.utcnow()
        cap.updated_at = datetime.utcnow()
        db.session.commit()

        logger.info(
            "Agent recorded maturity for capability id=%s current=%s target=%s user=%s",
            cap.id, cap.current_maturity_level, cap.target_maturity_level, self.user_id,
        )
        return {
            "success": True,
            "result": {
                "id": cap.id,
                "name": cap.name,
                "current_maturity_level": cap.current_maturity_level,
                "target_maturity_level": cap.target_maturity_level,
                "maturity_gap": cap.maturity_gap,
            },
            "message": (
                f"Recorded maturity for '{cap.name}': "
                f"current={cap.current_maturity_level}"
                + (f", target={cap.target_maturity_level}" if cap.target_maturity_level is not None else "")
                + "."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: score_rationalization (WRITE — G2)                            #
    # ------------------------------------------------------------------ #
    def _tool_score_rationalization(self, args: dict) -> dict:
        """Compute + persist an app's TIME rationalization score — EA/portfolio.

        Wraps RationalizationScoringService.calculate_app_score(app_id, app=...)
        (app/services/rationalization_scoring_service.py:516), which creates or
        updates the ApplicationRationalizationScore row and flushes. Follows the
        persist pattern of the rationalization_score_app route
        (app/modules/applications/routes/rationalization_api_routes.py:3453),
        including the RationalizationBenefitsTracker auto-create.

        ApplicationComponent is a TenantMixin model, so the lookup is tenant-
        scoped by the ORM: an app in another org reads back as not-found and is
        never scored/written cross-org. If the service returns None (scoring
        failed / not decision-ready to a degree it could not score), that is
        surfaced honestly — no invented score.
        """
        from app.models.application_portfolio import ApplicationComponent
        from app.models.application_rationalization import (
            RationalizationBenefitsTracker,
        )
        from app.services.rationalization_scoring_service import (
            RationalizationScoringService,
        )

        app_id = args.get("app_id")
        if app_id is None:
            return {"success": False, "error": "app_id is required."}

        # Tenant filter via the ORM (TenantMixin) — .filter_by().first(), not .get().
        app_obj = ApplicationComponent.query.filter_by(id=app_id).first()
        if app_obj is None:
            return {"success": False, "error": f"Application {app_id} not found."}

        score = RationalizationScoringService.calculate_app_score(app_id, app=app_obj)
        if not score:
            # The service returned nothing — surface it honestly, do not fabricate.
            return {
                "success": False,
                "error": (
                    f"Scoring did not produce a result for application {app_id}. "
                    "This usually means the app lacks the data the scoring model needs."
                ),
            }

        # Auto-create the benefits tracker (mirrors the route).
        existing_tracker = RationalizationBenefitsTracker.query.filter_by(
            application_id=app_id, score_id=score.id
        ).first()
        if not existing_tracker:
            tracker = RationalizationBenefitsTracker(
                application_id=app_id,
                score_id=score.id,
                projected_annual_savings=score.estimated_annual_savings or 0,
            )
            db.session.add(tracker)
        db.session.commit()

        logger.info(
            "Agent scored rationalization app id=%s overall=%s action=%s user=%s",
            app_id, score.overall_health_score, score.rationalization_action, self.user_id,
        )
        return {
            "success": True,
            "result": {
                "score_id": score.id,
                "app_id": app_id,
                "app_name": app_obj.name,
                "overall_health_score": score.overall_health_score,
                "rationalization_action": score.rationalization_action,
                "disposition_action": score.disposition_action,
                "disposition_confidence": score.disposition_confidence,
            },
            "message": (
                f"Scored '{app_obj.name}': overall={score.overall_health_score}, "
                f"TIME={score.rationalization_action}, disposition={score.disposition_action}."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: merge_capabilities (WRITE — G3, duplication debt)             #
    # ------------------------------------------------------------------ #
    def _tool_merge_capabilities(self, args: dict) -> dict:
        """Merge one duplicate business capability into another — G3 headline.

        Register gap G3 (docs/CAPABILITY_GAP_REGISTER.md): the copilot could
        DETECT duplicate capabilities (propose_rationalization surfaces the
        clusters) but had no governed way to RESOLVE them. This is that resolver:
        the LLM PROPOSES a merge; because the tool is mutates=True / tier
        'approve', a human approves it through the existing confirmation gate
        before anything runs. There is no auto-merge path.

        REPOINT-THEN-RETIRE, reversible variant.
        ---------------------------------------------------------------
        This follows the repoint logic of the abacus consolidation route's
        merge_keep_target (app/modules/capabilities/routes/abacus_consolidation.py)
        — children, APQC process mappings and application-capability mappings are
        moved from the removed capability onto the kept one — but it deviates on
        two points, deliberately, and both are documented here because a reviewer
        must be able to see the safety reasoning without reading three files:

        1. STORE. The task brief cited
           capability_naming_service.merge_duplicate_capabilities. That service
           operates on UnifiedCapability + UnifiedApplicationCapabilityMapping.
           UnifiedCapability holds 0 rows in production (it is the canonical store
           with no producer — see ADR 0008 / the store-agreement gate) and uses
           HybridCapabilityTenantMixin, whose shared-reference semantics
           (organization_id IS NULL == shared) are the wrong isolation model for a
           destructive per-tenant merge. The store the maturity routes, the
           heatmap and the abacus route actually read and write — and the one the
           task's own org-scope requirement names — is BusinessCapability
           (business_capability, 461 rows in production), a plain TenantMixin
           model. So this tool merges BusinessCapability, exactly as
           _tool_record_capability_maturity chose the same store for the same
           reason.

        2. REMOVAL. merge_duplicate_capabilities (and merge_keep_target)
           HARD-delete the duplicate. The owner guardrail requires the reversible
           variant where one exists, and BusinessCapability carries a soft-delete
           (is_deprecated / deprecated_as_of / deprecation_notes). So the duplicate
           is SOFT-deleted — marked is_deprecated with a note naming the kept id —
           not physically removed. The row survives for audit and hand-reversal;
           its references have already been repointed onto the kept capability, so
           nothing is orphaned. The tool's return also carries a full before-state
           snapshot (the removed capability's key fields + every repointed
           reference id) so the action is auditable and reversible even by hand.

        Org scope, self-merge and non-existent ids
        ---------------------------------------------------------------
        BusinessCapability is TenantMixin, so the ORM tenant filter scopes both
        lookups: a capability in another org is invisible and reads back as
        not-found — never resolved, never cross-merged. Self-merge (keep ==
        remove) and a non-existent id are rejected before any write.
        """
        from datetime import datetime

        from app.models.business_capabilities import BusinessCapability

        keep_id = args.get("keep_capability_id")
        remove_id = args.get("remove_capability_id")
        rationale = args.get("rationale")

        if keep_id is None or remove_id is None:
            return {
                "success": False,
                "error": "keep_capability_id and remove_capability_id are required.",
            }
        if not isinstance(keep_id, int) or isinstance(keep_id, bool) or \
           not isinstance(remove_id, int) or isinstance(remove_id, bool):
            return {
                "success": False,
                "error": "keep_capability_id and remove_capability_id must be integers.",
            }
        # Refuse to merge a capability into itself.
        if keep_id == remove_id:
            return {
                "success": False,
                "error": "Cannot merge a capability into itself (keep and remove ids are the same).",
            }

        # Tenant filter is applied by the ORM (TenantMixin) — .filter_by().first(),
        # not .get() (which is scoped only on an identity-map miss). A foreign-org
        # or non-existent id reads back as None → rejected, never cross-written.
        keep_cap = BusinessCapability.query.filter_by(id=keep_id).first()
        if keep_cap is None:
            return {
                "success": False,
                "error": f"Capability to keep ({keep_id}) not found in your organization.",
            }
        remove_cap = BusinessCapability.query.filter_by(id=remove_id).first()
        if remove_cap is None:
            return {
                "success": False,
                "error": f"Capability to remove ({remove_id}) not found in your organization.",
            }
        # Already retired — nothing to do, and merging a soft-deleted row would be
        # misleading. Surface it honestly rather than silently re-merging.
        if getattr(remove_cap, "is_deprecated", False):
            return {
                "success": False,
                "error": f"Capability {remove_id} is already retired (deprecated); nothing to merge.",
            }

        from app.models.apqc_process import CapabilityProcessMapping
        from app.models.application_capability import ApplicationCapabilityMapping

        org_id = self._get_organization_id()

        # -- BEFORE-STATE SNAPSHOT (audit + hand-reversal) ----------------- #
        # Captured before any write, so the return records exactly what existed
        # and exactly which references moved.
        children = BusinessCapability.query.filter_by(
            parent_capability_id=remove_id
        ).all()
        apqc_mappings = CapabilityProcessMapping.query.filter_by(
            capability_id=remove_id
        ).all()
        # Not a TenantMixin model — scope by organization_id explicitly as
        # defence-in-depth (the removed capability is already org-scoped, so its
        # mappings are too, but we do not rely on that alone for a write).
        app_mappings = ApplicationCapabilityMapping.query.filter_by(
            business_capability_id=remove_id, organization_id=org_id
        ).all()

        before_state = {
            "removed_capability": {
                "id": remove_cap.id,
                "name": remove_cap.name,
                "code": remove_cap.code,
                "level": remove_cap.level,
                "business_domain": remove_cap.business_domain,
                "parent_capability_id": remove_cap.parent_capability_id,
                "archimate_id": remove_cap.archimate_id,  # model-safety-ok: direct field access
                "organization_id": remove_cap.organization_id,
            },
            "repointed_child_ids": [c.id for c in children],
            "repointed_apqc_mapping_ids": [m.id for m in apqc_mappings],
            "repointed_app_mapping_ids": [m.id for m in app_mappings],
        }

        # -- REPOINT ------------------------------------------------------- #
        # Children: reparent onto the kept capability.
        for child in children:
            child.parent_capability_id = keep_id

        # APQC process mappings: repoint, dropping any that would duplicate a
        # mapping the kept capability already has (no compound unique constraint,
        # but a duplicate mapping is still wrong data).
        keep_apqc_ids = {
            m.apqc_process_id
            for m in CapabilityProcessMapping.query.filter_by(capability_id=keep_id).all()
        }
        apqc_repointed = 0
        for mapping in apqc_mappings:
            if mapping.apqc_process_id in keep_apqc_ids:
                db.session.delete(mapping)
            else:
                mapping.capability_id = keep_id
                apqc_repointed += 1

        # Application-capability mappings: repoint, de-duping against the kept
        # capability's existing application links.
        keep_app_ids = {
            m.application_component_id
            for m in ApplicationCapabilityMapping.query.filter_by(
                business_capability_id=keep_id, organization_id=org_id
            ).all()
        }
        app_repointed = 0
        for mapping in app_mappings:
            if mapping.application_component_id in keep_app_ids:
                db.session.delete(mapping)
            else:
                mapping.business_capability_id = keep_id
                app_repointed += 1

        # -- RETIRE (soft delete — reversible) ----------------------------- #
        remove_cap.is_deprecated = True
        remove_cap.deprecated_as_of = datetime.utcnow()
        note = f"Merged into capability {keep_id} ('{keep_cap.name}') by AI copilot (user {self.user_id})."
        if rationale:
            note += f" Rationale: {rationale}"
        remove_cap.deprecation_notes = note
        remove_cap.updated_at = datetime.utcnow()

        db.session.commit()

        logger.info(
            "Agent merged capability id=%s into id=%s (children=%s apqc=%s apps=%s) user=%s",
            remove_id, keep_id, len(children), apqc_repointed, app_repointed, self.user_id,
        )
        return {
            "success": True,
            "result": {
                "kept_capability": {"id": keep_cap.id, "name": keep_cap.name},
                "removed_capability_id": remove_id,
                "removal_method": "soft_delete (is_deprecated=True)",
                "children_repointed": len(children),
                "apqc_mappings_repointed": apqc_repointed,
                "app_mappings_repointed": app_repointed,
                "before_state": before_state,
            },
            "message": (
                f"Merged '{remove_cap.name}' into '{keep_cap.name}': "
                f"repointed {len(children)} child capabilit(ies), "
                f"{app_repointed} application mapping(s) and {apqc_repointed} APQC "
                f"mapping(s); the duplicate was retired (soft-deleted, reversible). "
                f"A full before-state snapshot is in the result for audit."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: create_vendor (WRITE — G5, procurement)                       #
    # ------------------------------------------------------------------ #
    def _tool_create_vendor(self, args: dict) -> dict:
        """Register a vendor in the shared catalogue — procurement headline write.

        Wraps AIDataInteractionService.create_vendor()
        (app/modules/ai_chat/services/ai_data_interaction_service.py:295), which
        creates a VendorOrganization, runs the capability guardrails and commits.
        mutates=True / tier 'approve', so it flows through the confirmation gate.

        DEVIATION from the task brief, made as data/security architect: the brief
        said "vendor is tenant-scoped; must not write cross-org". It is NOT.
        VendorOrganization is DELIBERATELY not a TenantMixin model (ADR-0003,
        app/models/vendor/vendor_organization.py:252): it is shared reference data
        — Gartner position, market share, revenue — identical for every customer,
        with a GLOBALLY UNIQUE `name`. Giving it an organization_id would break
        that unique constraint and duplicate every row per org, and is pinned
        against by tests/test_vendor_tenancy_policy.py. So this tool does not (and
        must not) org-scope the write; the tenant-owned parts of vendor data live
        in vendor_contracts / VendorProductCapability, which other tools cover.
        The guardrails + the unique-name constraint are the write's safety, not a
        tenant predicate.
        """
        from app.modules.ai_chat.services.ai_data_interaction_service import (
            AIDataInteractionService,
        )

        name = (args.get("name") or "").strip()
        if not name:
            return {"success": False, "error": "name is required."}

        vendor_data = {"name": name}
        for key in (
            "display_name", "vendor_type", "website",
            "headquarters_location", "description", "strategic_tier",
        ):
            value = args.get(key)
            if value is not None:
                vendor_data[key] = value

        service = AIDataInteractionService(user_id=self.user_id)
        result = service.create_vendor(vendor_data)

        if not result.get("success"):
            # Surface the service's real error (e.g. duplicate unique name,
            # guardrails violation) honestly — never a fabricated success.
            return {"success": False, "error": result.get("error", "Vendor creation failed.")}

        logger.info(
            "Agent created vendor id=%s name=%r user=%s",
            result.get("vendor_id"), name, self.user_id,
        )
        return {
            "success": True,
            "result": {"id": result.get("vendor_id"), "name": name},
            "message": result.get("message", f"Created vendor '{name}'."),
        }

    # ------------------------------------------------------------------ #
    # Tool: extract_contract_from_document (READ/extract — G5, procurement)#
    # ------------------------------------------------------------------ #
    def _tool_extract_contract_from_document(self, args: dict) -> dict:
        """Extract structured contract terms from pasted text — procurement.

        Wraps contract_extraction_service.extract_contract_terms()
        (app/modules/procurement/contract_extraction_service.py:148), an
        LLM-backed, text-in / JSON-out extractor that returns exactly the
        EXTRACTED_FIELDS keys, each either its typed value or None — it never
        guesses. mutates=False / tier 'auto': it reads only and persists nothing;
        a human (or create_vendor / a contract form) applies the result.

        The service raises ContractExtractionError when the LLM call fails or the
        response cannot be parsed as JSON — that is surfaced here as an honest
        failure (no fabricated extraction), matching the brief's requirement that
        a missing/absent LLM key be reported rather than papered over.
        """
        from app.modules.procurement.contract_extraction_service import (
            ContractExtractionError,
            extract_contract_terms,
        )

        text = (args.get("contract_text") or "").strip()
        if not text:
            return {"success": False, "error": "contract_text is required."}

        try:
            extracted = extract_contract_terms(text)
        except ContractExtractionError as exc:
            logger.warning("Contract extraction failed for user=%s: %s", self.user_id, exc)
            return {
                "success": False,
                "error": (
                    f"Contract extraction could not run: {exc}. "
                    "No fields were extracted — nothing was fabricated."
                ),
            }

        populated = [k for k, v in extracted.items() if v is not None]
        return {
            "success": True,
            "result": extracted,
            "message": (
                f"Extracted {len(populated)} of {len(extracted)} contract field(s) "
                "from the text. Fields not stated in the document are null and must "
                "be filled in by hand — they were not guessed. Nothing was saved."
            ),
        }

    # ------------------------------------------------------------------ #
    # Tool: bulk_update_application_status (WRITE — G4)                    #
    # ------------------------------------------------------------------ #
    def _tool_bulk_update_application_status(self, args: dict) -> dict:
        """Set lifecycle_status on a SET of applications in one governed call.

        The portfolio/application-manager headline write. update_application_status
        is one app per call; this applies one lifecycle stage to many, under a
        single approval.

        DEVIATION from the task brief, made as data architect: the brief cited
        "the ApplicationComponent.lifecycle_status write update_application_status
        does". Those are two DIFFERENT columns. update_application_status
        (_tool_update_application_status) writes DEPLOYMENT_status; the canonical
        bulk lifecycle write is the PLT-020 route
        app/modules/applications/routes/list_views.py::api_bulk_lifecycle, which
        writes LIFECYCLE_status validated against APPLICATION_LIFECYCLE_STAGES
        (NOT app.models.constants.LifecycleStatus — a different vocabulary the UI
        never sends, which rejected every real click). This tool mirrors
        api_bulk_lifecycle: it writes lifecycle_status and validates against
        APPLICATION_LIFECYCLE_STAGES, exactly as the brief's own "validate against
        the real lifecycle enum" requires. ApplicationComponent is a TenantMixin
        model, so both the id/filter SELECT and the write carry the org predicate
        mechanically — apps in another org are invisible and read back as
        not-found, never cross-written.
        """
        from app.models.application_portfolio import (
            APPLICATION_LIFECYCLE_STAGES,
            ApplicationComponent,
        )

        new_status = (args.get("new_status") or "").strip().lower()
        if not new_status:
            return {"success": False, "error": "new_status is required."}
        if new_status not in APPLICATION_LIFECYCLE_STAGES:
            return {
                "success": False,
                "error": (
                    "new_status %r is not a valid lifecycle stage. Must be one of: %s"
                    % (new_status, ", ".join(APPLICATION_LIFECYCLE_STAGES))
                ),
            }

        app_ids = args.get("app_ids")
        filt = args.get("filter") or {}
        MAX_BATCH = 200

        # Resolve the target SET. Explicit ids win; otherwise a filter selects it.
        # The ORM tenant event scopes every SELECT below to the acting org.
        if app_ids:
            if not isinstance(app_ids, list):
                return {"success": False, "error": "app_ids must be a list of integers."}
            try:
                ids = [int(x) for x in app_ids]
            except (TypeError, ValueError):
                return {"success": False, "error": "app_ids must be a list of integers."}
            query = ApplicationComponent.query.filter(ApplicationComponent.id.in_(ids))
            requested_ids = ids
        elif filt:
            query = ApplicationComponent.query
            if filt.get("current_status"):
                query = query.filter(
                    ApplicationComponent.lifecycle_status == filt["current_status"]
                )
            if filt.get("component_type"):
                query = query.filter(
                    ApplicationComponent.component_type == filt["component_type"]
                )
            requested_ids = None
        else:
            return {
                "success": False,
                "error": "Provide either app_ids (list) or a filter to select applications.",
            }

        matched = query.order_by(ApplicationComponent.id).all()

        # Cap the batch — LOG the truncation in the result, never silently drop.
        truncated = False
        capped_note = None
        if len(matched) > MAX_BATCH:
            truncated = True
            capped_note = (
                "Batch capped at %d of %d matched applications; %d were NOT updated. "
                "Narrow the selection and run again for the rest."
                % (MAX_BATCH, len(matched), len(matched) - MAX_BATCH)
            )
            matched = matched[:MAX_BATCH]

        matched_by_id = {a.id: a for a in matched}
        results = []
        updated_count = 0

        # Per-app results: report every requested id, updated or skipped-with-reason.
        iter_ids = requested_ids if requested_ids is not None else [a.id for a in matched]
        for app_id in iter_ids:
            app_obj = matched_by_id.get(app_id)
            if not app_obj:
                results.append({
                    "id": app_id,
                    "updated": False,
                    "reason": "not found in your organization",
                })
                continue
            old_status = app_obj.lifecycle_status
            if old_status == new_status:
                results.append({
                    "id": app_id,
                    "name": app_obj.name,
                    "updated": False,
                    "reason": "already at '%s'" % new_status,
                })
                continue
            app_obj.lifecycle_status = new_status
            updated_count += 1
            results.append({
                "id": app_id,
                "name": app_obj.name,
                "updated": True,
                "old_status": old_status,
                "new_status": new_status,
            })

        if updated_count > 0:
            db.session.commit()
        else:
            db.session.rollback()

        logger.info(
            "Agent bulk lifecycle: %d updated → %r (user=%s, requested=%s, truncated=%s, rationale=%r)",
            updated_count, new_status, self.user_id,
            len(iter_ids), truncated, args.get("rationale"),
        )

        message = "Updated %d application(s) to lifecycle stage '%s'." % (
            updated_count, new_status,
        )
        if capped_note:
            message += " " + capped_note

        return {
            "success": updated_count > 0,
            "result": {
                "new_status": new_status,
                "updated_count": updated_count,
                "requested_count": len(iter_ids),
                "truncated": truncated,
                "cap_note": capped_note,
                "apps": results,
            },
            "message": message,
        }

    # ------------------------------------------------------------------ #
    # Tool: create_contract (WRITE — G8, procurement)                     #
    # ------------------------------------------------------------------ #
    def _tool_create_contract(self, args: dict) -> dict:
        """Create a procurement (commercial) vendor contract.

        Wraps the pure form-application helper behind the procurement route
        app/modules/procurement/crud_routes.py::contract_create — namely
        _apply_contract_form, which validates the vocabularies
        (CONTRACT_TYPES/CATEGORIES/STATUSES), enforces contract_name, defaults a
        missing start_date to today, and rejects an end/renewal date before the
        start. VendorContract is a TenantMixin model
        (app/models/application_portfolio.py:756); organization_id is set
        explicitly (defence-in-depth) to the acting org, so the contract is
        tenant-private.

        This is the PROCUREMENT/commercial contract (VendorContract family), NOT
        the API-interface contract in solutions_strategic.

        The route helper reads a Werkzeug form (form.get); the tool builds an
        equivalent plain dict from its args and reuses the SAME validation, so
        the AI path can never be more permissive than the HTTP form.
        """
        from app.models.application_portfolio import VendorContract
        from app.modules.procurement.crud_routes import _apply_contract_form

        vendor_id = args.get("vendor_id")
        form = {
            "contract_name": args.get("name") or args.get("contract_name") or "",
            "contract_number": args.get("contract_number") or "",
            "contract_description": args.get("description") or "",
            "vendor_id": str(vendor_id) if vendor_id not in (None, "") else "",
            "contract_type": args.get("contract_type") or "",
            "contract_category": args.get("contract_category") or "",
            "status": args.get("status") or "",
            "contract_value": args.get("value") if args.get("value") is not None
            else args.get("contract_value"),
            "annual_cost": args.get("annual_cost"),
            "currency": args.get("currency") or "USD",
            "start_date": args.get("start_date") or "",
            "end_date": args.get("end_date") or "",
            "renewal_date": args.get("renewal_date") or "",
            "auto_renewal": "on" if args.get("auto_renewal") else "",
            "contract_owner": args.get("contract_owner") or "",
        }

        contract = VendorContract(organization_id=self._get_organization_id())
        try:
            _apply_contract_form(contract, form)
            db.session.add(contract)
            db.session.commit()
        except ValueError as exc:
            # Honest validation failure — never a fabricated success.
            db.session.rollback()
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # e.g. duplicate contract_number (unique)
            db.session.rollback()
            return {"success": False, "error": str(exc)}

        logger.info(
            "Agent created contract id=%s name=%r vendor_id=%s user=%s",
            contract.id, contract.contract_name, contract.vendor_id, self.user_id,
        )
        return {
            "success": True,
            "result": {
                "id": contract.id,
                "contract_name": contract.contract_name,
                "vendor_id": contract.vendor_id,
                "status": contract.status,
                "start_date": str(contract.start_date) if contract.start_date else None,
                "end_date": str(contract.end_date) if contract.end_date else None,
            },
            "message": "Created contract '%s' (id=%s)." % (contract.contract_name, contract.id),
        }

    # ------------------------------------------------------------------ #
    # Tool: create_programme (WRITE — G8, transformation programmes)      #
    # ------------------------------------------------------------------ #
    def _tool_create_programme(self, args: dict) -> dict:
        """Create a business-first Transformation Programme.

        Calls the SAME entry point the /solutions/new-programme wizard's POST
        handler calls — ProgrammeSetupService.create_business_first_programme
        (app/modules/solutions_strategic/v2/routes/solution_wizard_routes.py) —
        with an equivalent ActorContext/ProgrammeIntake, so this tool can never
        create a programme the human wizard would refuse, and can never be more
        permissive. can_create_programme is checked at the door first (audit
        F-04: without it, TransformationProgrammeService's own NotAuthorised
        would only surface after the whole payload was built).
        """
        import uuid as uuid_lib
        from datetime import date

        from app.models.user import User
        from app.modules.transformation_room.domain import ActorContext, ProgrammeIntake, TransformationError
        from app.modules.transformation_room.programme_service import TransformationProgrammeService
        from app.modules.solutions_strategic.v2.services.programme_setup_service import ProgrammeSetupService

        user = _load_acting_user(self.user_id)
        if user is None:
            return {"success": False, "error": "Could not load the acting user."}
        if not TransformationProgrammeService.can_create_programme(user):
            return {
                "success": False,
                "error": (
                    "Only Enterprise Architects, Chief Architects and CTOs can create "
                    "programmes. Ask one of them to set the programme up, or ask an "
                    "organisation admin to give you that persona."
                ),
            }

        name = (args.get("name") or "").strip()
        objective = (args.get("objective") or "").strip()
        outcome_statement = (args.get("outcome_statement") or "").strip()
        metric_name = (args.get("metric_name") or "").strip()
        metric_unit = (args.get("metric_unit") or "").strip()
        if not (name and objective and outcome_statement and metric_name and metric_unit):
            return {"success": False, "error": "name, objective, outcome_statement, metric_name and metric_unit are required."}

        owner_id = args.get("owner_id") or self.user_id
        owner = User.query.filter_by(id=owner_id, organization_id=self._get_organization_id()).first()
        if owner is None:
            return {"success": False, "error": "owner_id must be a real user in your organization."}

        target_date_raw = args.get("target_date")
        target_date_reason = args.get("target_date_unavailable_reason")
        if target_date_raw:
            try:
                target_date = date.fromisoformat(target_date_raw)
            except ValueError:
                return {"success": False, "error": "target_date must be YYYY-MM-DD."}
        elif target_date_reason:
            target_date = None
        else:
            return {"success": False, "error": "Either target_date or target_date_unavailable_reason is required."}

        organization_id = self._get_organization_id()
        runtime_roles = {
            role
            for role in (
                getattr(user, "enterprise_role", None),
                "organization_admin" if getattr(user, "is_org_admin", False) else None,
                "platform_admin" if getattr(user, "is_platform_admin", False) else None,
            )
            if role
        }
        actor = ActorContext(
            user_id=user.id,
            organization_id=organization_id,
            roles=frozenset(runtime_roles),
            request_id=str(uuid_lib.uuid4()),
        )
        intake = ProgrammeIntake(
            name=name,
            objective=objective,
            owner_id=owner.id,
            target_date=target_date,
            target_date_unavailable_reason=target_date_reason,
            workstream_type=args.get("workstream_type") or "application_rationalisation",
            scope_expression={"business_units": args.get("business_units") or []},
            outcome={
                "statement": outcome_statement,
                "owner_id": owner.id,
                "direction": args.get("outcome_direction") or "increase",
                "measure": {
                    "metric_name": metric_name,
                    "unit": metric_unit,
                    "aggregation": args.get("metric_aggregation") or "sum",
                    "baseline_value": args.get("baseline_value"),
                    "unavailable_reason": args.get("baseline_unavailable_reason"),
                    "target_value": args.get("target_value"),
                },
            },
        )

        try:
            result = ProgrammeSetupService.create_business_first_programme(
                actor=actor,
                command_key=str(uuid_lib.uuid4()),
                request=intake,
            )
        except (TypeError, ValueError) as exc:
            return {"success": False, "error": str(exc)}
        except TransformationError as exc:
            return {"success": False, "error": exc.reason}
        except Exception as exc:
            logger.exception("Agent create_programme failed for user=%s: %s", self.user_id, exc)
            return {"success": False, "error": "An unexpected error occurred creating the programme."}

        programme_id = result.object_ids["programme_id"]
        workstream_id = result.object_ids["workstream_id"]
        logger.info(
            "Agent created programme id=%s name=%r user=%s", programme_id, name, self.user_id,
        )
        return {
            "success": True,
            "result": {
                "programme_id": programme_id,
                "workstream_id": workstream_id,
                "outcome_commitment_id": result.object_ids["outcome_commitment_id"],
                "redirect_url": f"/solutions/programmes/{programme_id}/workstreams/{workstream_id}/objective",
            },
            "message": "Created programme '%s' (id=%s)." % (name, programme_id),
        }

    # ------------------------------------------------------------------ #
    # Tool: upsert_license (WRITE — G8, procurement)                      #
    # ------------------------------------------------------------------ #
    def _tool_upsert_license(self, args: dict) -> dict:
        """Create or update a licence entitlement under a contract.

        Wraps the pure helpers behind
        app/modules/procurement/crud_routes.py::license_create / license_edit —
        _apply_license_form (product/type/metric + entitled/deployed/used counts
        and unit cost) and _recompute_compliance (compliance derived from the
        quantities, never trusted from the caller). LicenseEntitlement is a
        TenantMixin model (app/models/license_entitlement.py:18) and MUST belong
        to a contract; the contract_id is re-read through the org predicate so a
        licence cannot be hung off another organisation's contract. If a
        license_id is supplied it updates that entitlement (org-scoped lookup);
        otherwise it creates one.
        """
        from app.models.application_portfolio import VendorContract
        from app.models.license_entitlement import LicenseEntitlement
        from app.modules.procurement.crud_routes import (
            _apply_license_form,
        )

        org_id = self._get_organization_id()
        license_id = args.get("license_id")
        contract_id = args.get("contract_id")

        form = {
            "product_name": args.get("product") or args.get("product_name") or "",
            "license_type": args.get("license_type") or "named_user",
            "license_metric": args.get("license_metric") or "",
            "quantity_entitled": args.get("entitled") if args.get("entitled") is not None
            else args.get("quantity_entitled"),
            "quantity_deployed": args.get("deployed") if args.get("deployed") is not None
            else args.get("quantity_deployed"),
            "quantity_used": args.get("used") if args.get("used") is not None
            else args.get("quantity_used"),
            "unit_cost": args.get("unit_cost"),
        }
        # Normalise Nones to "" so _apply_license_form treats them as blanks (entitled 0; deployed/used not recorded).
        form = {k: ("" if v is None else v) for k, v in form.items()}

        if license_id is not None:
            entitlement = LicenseEntitlement.query.filter_by(
                id=license_id, organization_id=org_id
            ).first()
            if not entitlement:
                return {
                    "success": False,
                    "error": "Licence %s not found in your organization." % license_id,
                }
            if contract_id is not None:
                owned = VendorContract.query.filter_by(
                    id=int(contract_id), organization_id=org_id
                ).first()
                if not owned:
                    return {
                        "success": False,
                        "error": "Contract %s not found in your organization." % contract_id,
                    }
                entitlement.contract_id = int(contract_id)
            created = False
        else:
            if contract_id is None:
                return {"success": False, "error": "A licence must belong to a contract (contract_id)."}
            owned = VendorContract.query.filter_by(
                id=int(contract_id), organization_id=org_id
            ).first()
            if not owned:
                return {
                    "success": False,
                    "error": "Contract %s not found in your organization." % contract_id,
                }
            entitlement = LicenseEntitlement(
                organization_id=org_id, contract_id=int(contract_id)
            )
            created = True

        try:
            _apply_license_form(entitlement, form)
            if created:
                db.session.add(entitlement)
            db.session.commit()
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            return {"success": False, "error": "Quantities must be whole numbers and unit cost numeric: %s" % exc}
        except Exception as exc:
            db.session.rollback()
            return {"success": False, "error": str(exc)}

        logger.info(
            "Agent %s licence id=%s contract_id=%s user=%s",
            "updated" if not created else "created",
            entitlement.id, entitlement.contract_id, self.user_id,
        )
        return {
            "success": True,
            "result": {
                "id": entitlement.id,
                "contract_id": entitlement.contract_id,
                "product_name": entitlement.product_name,
                "quantity_entitled": entitlement.quantity_entitled,
                "quantity_deployed": entitlement.quantity_deployed,
                "quantity_used": entitlement.quantity_used,
                "compliance_status": entitlement.compliance_status,
                "created": created,
            },
            "message": "%s licence entitlement id=%s (compliance: %s)." % (
                "Created" if created else "Updated",
                entitlement.id, entitlement.compliance_status,
            ),
        }


# ------------------------------------------------------------------ #
# Lightweight helper (avoids importing ApplicationCapabilityMapping   #
# at module level to prevent circular imports)                        #
# ------------------------------------------------------------------ #

def _safe_count(relationship_attr) -> int:
    """Count a SQLAlchemy relationship safely whether it's dynamic or a loaded list."""
    try:
        return relationship_attr.count()
    except TypeError:
        # list.count() requires an argument — it's a loaded list
        return len(list(relationship_attr))
    except Exception:
        return 0


def ApplicationCapabilityMapping_count(capability_id: int) -> int:
    try:
        from app.models.application_capability import ApplicationCapabilityMapping
        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        return ApplicationCapabilityMapping.query.filter_by(
            business_capability_id=capability_id
        ).count()
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# One creation path for every ArchiMate element type, installed per type.
#
# The dispatcher in execute() resolves a tool by getattr(self, "_tool_" + name),
# so each element type needs a real attribute. They are installed here rather
# than written out 58 times: the behaviour is identical, and the thing that
# differs per type — what the element MEANS and what it is confused with — lives
# in archimate_specs.py where it can be reviewed as domain content instead of
# being buried in near-duplicate code.
#
# This is still a DEDICATED path per type, not a generic create_archimate_element
# with a type argument. The difference is the one the coverage gate exists to
# enforce: the model chooses `create_business_process` because its description
# told it when a process differs from a function, rather than guessing a string.
def _install_archimate_element_tools() -> int:
    from .archimate_specs import ELEMENT_SPECS

    def _make(element_type: str, spec: dict):
        def _create(self, args: dict) -> dict:
            from app.models.archimate_core import ArchiMateElement

            name = (args.get("name") or "").strip()
            if not name:
                return {
                    "success": False,
                    "error": "A %s needs a name." % element_type.replace("_", " "),
                }

            # Everything beyond name/description is kept as custom_properties
            # rather than silently dropped: an assistant that quietly discards
            # what a user told it is worse than one that refuses.
            extras = {
                key: value
                for key, value in args.items()
                if key not in ("name", "description", "solution_id") and value
            }
            element = ArchiMateElement(
                organization_id=self._get_organization_id(),
                name=name[:100],
                type=element_type,
                layer=spec["layer"],
                description=args.get("description") or "",
                scope="enterprise",
                custom_properties={"ai_generated": True, **extras},
            )
            db.session.add(element)
            db.session.commit()
            logger.info(
                "Agent created %s id=%s layer=%s", element_type, element.id, spec["layer"]
            )
            return {
                "success": True,
                "result": {
                    "id": element.id,
                    "name": element.name,
                    "entity_type": element_type,
                    "layer": spec["layer"],
                },
                "message": "Added %s '%s' to the %s layer."
                           % (element_type.replace("_", " "), element.name, spec["layer"]),
            }

        _create.__name__ = "_tool_create_%s" % element_type
        _create.__doc__ = spec["definition"]
        return _create

    installed = 0
    for element_type, spec in ELEMENT_SPECS.items():
        attribute = "_tool_create_%s" % element_type
        if hasattr(ToolExecutor, attribute):
            continue  # a hand-written tool for this type wins
        setattr(ToolExecutor, attribute, _make(element_type, spec))
        installed += 1
    return installed


_ARCHIMATE_TOOLS_INSTALLED = _install_archimate_element_tools()
