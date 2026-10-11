#!/usr/bin/env python
"""Every persona vocabulary in the product must reconcile with VALID_ROLES.

Archie carries four separate lists of "who a person can be", and on 30 Aug 2026
all four disagreed:

  app/models/user.py VALID_ROLES                 9  what a user can actually BE
  admin/v2 _VALID_ROLES (SSO settings screen)    6  missing cto, procurement,
                                                    application_manager
  auth/sso.py DEFAULT_GROUP_ROLE_MAP             6  the same three missing
  ai_chat ARCHITECT_PERSONAS                    15  7 that cannot be assigned

The consequences were not cosmetic. The admin SSO screen's list is BOTH its
dropdown and its validator (`role_name not in _VALID_ROLES` -> "Invalid role"),
so an administrator at an SSO-only customer could not map an IdP group to a
CTO, a procurement user or an application manager at all -- three of the nine
personas the product ships, each with its own sidebar zone, permission set and
governed AI charter. Nothing was broken enough to fail: every page returned
200, every test passed, and three personas were simply unprovisionable.

This gate holds two invariants:

  1. Every role in VALID_ROLES is provisionable -- it appears in the SSO
     default group map. A persona nobody can be assigned is a persona that
     does not exist for a customer who onboards through their IdP.
  2. Every AI charter persona is either in VALID_ROLES, reachable through
     PERSONA_ALIASES, or listed in ASPIRATIONAL below with a reason. A charter
     for a persona the platform cannot assign is dead capability that reads,
     to anyone opening the file, as a shipped feature.

ASPIRATIONAL is the honest escape hatch: these are charters written ahead of
the roles, kept deliberately. Promoting any of them to a first-class persona is
a PRODUCT decision (it changes onboarding, permissions and navigation), not an
engineering one -- so the gate records the gap rather than pretending it is
closed. Note there is no security_architect in ANY list, while the solution
blueprint ships a Security Viewpoint.

    python scripts/check_persona_vocabularies.py            # list mismatches
    python scripts/check_persona_vocabularies.py --count    # trailing = count

Proven-against: the "CTO" entry removed from DEFAULT_GROUP_ROLE_MAP -- red at 1
naming 'cto' as unprovisionable, green at 0 when restored.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Charters written ahead of the roles. Each is a persona the AI can speak as
# only if a human is deliberately routed to it; none can be an enterprise_role.
# Listed, not silently tolerated, so the gap stays visible in review.
ASPIRATIONAL = {
    "integration_architect": "charter written ahead of the role",
    "systems_architect": "charter written ahead of the role",
    "business_analyst": "analyst, not an architecture persona with a sidebar",
    "product_analyst": "analyst, not an architecture persona with a sidebar",
}


def _valid_roles(root: str) -> list[str]:
    path = os.path.join(root, "app", "models", "user.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    constants: dict[str, str] = {}
    roles: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            constants[target.id] = node.value.value
        elif target.id == "VALID_ROLES" and isinstance(node.value, ast.List):
            for element in node.value.elts:
                if isinstance(element, ast.Name) and element.id in constants:
                    roles.append(constants[element.id])
                elif isinstance(element, ast.Constant):
                    roles.append(element.value)
    return roles


def _block(root: str, relpath: str, pattern: str) -> list[str]:
    path = os.path.join(root, *relpath.split("/"))
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return []
    match = re.search(pattern, source, re.S)
    if not match:
        return []
    return re.findall(r'"([a-z_]+)"', match.group(1))


def scan(root: str) -> list[str]:
    problems = []
    roles = set(_valid_roles(root))
    if not roles:
        return ["app/models/user.py: could not read VALID_ROLES"]

    provisionable = set(
        _block(root, "app/auth/sso.py", r"DEFAULT_GROUP_ROLE_MAP = \{(.*?)\n\}")
    )
    for role in sorted(roles - provisionable):
        problems.append(
            "app/auth/sso.py: [persona-vocab] %r is in VALID_ROLES but no default "
            "IdP group maps to it -- an SSO-only customer cannot provision it" % role
        )

    charters = set(
        _block(root, "app/modules/ai_chat/services/architect_persona_charters.py",
               r"ARCHITECT_PERSONAS = \((.*?)\)")
    )
    aliases = set(
        _block(root, "app/modules/ai_chat/services/architect_persona_charters.py",
               r"PERSONA_ALIASES: Dict\[str, str\] = \{(.*?)\n\}")
    )
    for persona in sorted(charters - roles - aliases - set(ASPIRATIONAL)):
        problems.append(
            "architect_persona_charters.py: [persona-vocab] charter %r is not in "
            "VALID_ROLES, not aliased, and not listed as aspirational -- no user "
            "can ever be it" % persona
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    parser.add_argument("--root", default=ROOT)
    args = parser.parse_args()

    problems = scan(os.path.abspath(args.root))
    if not args.count:
        for line in problems:
            print("  " + line)
        if problems:
            print()
            print(
                "Add the role to app/auth/sso.py's DEFAULT_GROUP_ROLE_MAP so it can be\n"
                "provisioned, or add the charter to ASPIRATIONAL in this file with the\n"
                "reason it has no role yet. Promoting an aspirational charter to a real\n"
                "persona is a product decision, not a lint fix."
            )
    print(len(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
