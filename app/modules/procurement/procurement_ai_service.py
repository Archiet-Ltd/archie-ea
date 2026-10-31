"""Procurement AI assist: renewal briefs, remediation, licence position, spend.

Four advisory endpoints for the Procurement persona, sharing this one service
file the way the ARB reviewer pre-brief
(app/modules/architecture/services/arb_review_ai_service.py) shares its own.
Each generate_* function assembles a strictly-real context (the row's own
columns, its directly-linked rows, or a real ORM aggregate query - never an
inferred or invented value), prompts the LLM for structured JSON, and
validates the response before returning it. Nothing here writes to the
database: every response is advisory input for a human who still acts (or
doesn't) through the existing procurement screens.

Follows the prompt -> JSON -> validate pattern already used twice in this
module (contract_extraction_service.py) and once in architecture
(arb_review_ai_service.py): markdown-fence tolerance, no silent fallback to a
fabricated result on a parse failure (CLAUDE.md's never-invent-data rule).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.services.llm_service import LLMService

from .services import get_days_until_renewal, get_spend_by_category, get_spend_summary

logger = logging.getLogger(__name__)

MAX_CONTEXT_ROWS = 20

VALID_STANCES = {"renew", "renegotiate", "consolidate", "exit"}


class ProcurementAIError(Exception):
    """Raised when the LLM call fails or its response cannot be trusted.

    Never caught to fabricate a fallback value - per CLAUDE.md, a screen that
    shows a plausible-looking value when the real one is missing is worse
    than one that shows nothing, because the reader cannot tell the two
    apart. Callers must surface this as an explicit failure (502).
    """


def _strip_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.strip().startswith("```"))
    return raw.strip()


def _parse_json_object(raw: str) -> Dict[str, Any]:
    """Shared JSON-object parse step for all four endpoints.

    Raises ProcurementAIError (never returns a fabricated fallback) when the
    response is not valid JSON, or is valid JSON that is not an object.
    """
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise ProcurementAIError(f"LLM response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ProcurementAIError("LLM response was not a JSON object")

    return data


def _require_keys(data: Dict[str, Any], keys: set) -> None:
    missing = keys - data.keys()
    if missing:
        raise ProcurementAIError(f"LLM response missing required keys: {sorted(missing)}")


def _as_str_list(data: Dict[str, Any], key: str) -> List[str]:
    value = data[key]
    if not isinstance(value, list):
        raise ProcurementAIError(f"LLM response field {key!r} was not a list")
    return [str(x) for x in value]


# ---------------------------------------------------------------------------
# 1. Renewal brief - /procurement/api/contracts/<id>/ai-renewal-brief
# ---------------------------------------------------------------------------


def _build_renewal_brief_context(contract) -> Dict[str, Any]:
    days_to_expiry = get_days_until_renewal(contract)

    context: Dict[str, Any] = {
        "contract_name": contract.contract_name,
        "contract_number": contract.contract_number,
        "vendor_name": contract.vendor.name if contract.vendor else None,
        "contract_type": contract.contract_type,
        "contract_category": contract.contract_category,
        "status": contract.status,
        "contract_value": contract.contract_value,
        "annual_cost": contract.annual_cost,
        "currency": contract.currency,
        "start_date": contract.start_date.isoformat() if contract.start_date else None,
        "end_date": contract.end_date.isoformat() if contract.end_date else None,
        "renewal_date": contract.renewal_date.isoformat() if contract.renewal_date else None,
        "days_to_expiry": days_to_expiry,
        "auto_renewal": contract.auto_renewal,
        "notice_period_days": contract.notice_period_days,
        "vendor_risk": contract.vendor_risk,
        "contract_risk": contract.contract_risk,
        "exit_complexity": contract.exit_complexity,
    }

    # licence_entitlements is a lazy="dynamic" backref, so .all() is a real
    # query scoped to this contract - not a value invented for the prompt.
    licences = contract.license_entitlements.all()
    if licences:
        context["licences"] = [
            {
                "product_name": lic.product_name,
                "quantity_entitled": lic.quantity_entitled,
                "quantity_deployed": lic.quantity_deployed,
                "compliance_status": lic.compliance_status,
            }
            for lic in licences[:MAX_CONTEXT_ROWS]
        ]

    return context


def _build_renewal_brief_prompt(context: Dict[str, Any]) -> str:
    return (
        "You are assisting a Procurement lead who is deciding how to handle "
        "an upcoming vendor contract renewal. Below is the real, verified "
        "context for one contract - do not invent facts not present in it.\n\n"
        f"Contract context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce a renewal brief. Respond ONLY with a single JSON object with "
        "exactly these keys:\n"
        '- "summary": string, 2-4 sentences on the renewal situation\n'
        '- "stance": one of "renew", "renegotiate", "consolidate", "exit" - '
        "your suggested starting position\n"
        '- "leverage_points": array of strings, facts from the context the '
        "Procurement lead could use in a renewal negotiation\n"
        '- "risks": array of strings, risks of the recommended stance or of '
        "inaction\n"
        '- "questions_for_vendor": array of strings, open questions to raise "'
        "with the vendor before deciding\n"
        '- "rationale": string explaining the stance\n\n'
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _parse_renewal_brief(raw: str) -> Dict[str, Any]:
    data = _parse_json_object(raw)
    required = {"summary", "stance", "leverage_points", "risks", "questions_for_vendor", "rationale"}
    _require_keys(data, required)

    if data["stance"] not in VALID_STANCES:
        raise ProcurementAIError(f"LLM response has an invalid stance: {data['stance']!r}")

    if not isinstance(data["summary"], str) or not isinstance(data["rationale"], str):
        raise ProcurementAIError("LLM response summary/rationale were not strings")

    return {
        "summary": data["summary"],
        "stance": data["stance"],
        "leverage_points": _as_str_list(data, "leverage_points"),
        "risks": _as_str_list(data, "risks"),
        "questions_for_vendor": _as_str_list(data, "questions_for_vendor"),
        "rationale": data["rationale"],
    }


def generate_renewal_brief(contract) -> Dict[str, Any]:
    """Generate an AI renewal brief for a vendor contract.

    Advisory only - nothing is written back to the contract. Raises
    ProcurementAIError (or lets an LLMService exception propagate) rather
    than fabricating a fallback brief.
    """
    context = _build_renewal_brief_context(contract)
    prompt = _build_renewal_brief_prompt(context)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_renewal_brief(raw)


# ---------------------------------------------------------------------------
# 2. Compliance remediation - /procurement/api/compliance/violations/<id>/ai-remediation
# ---------------------------------------------------------------------------


def _build_remediation_context(license_entitlement) -> Dict[str, Any]:
    contract = license_entitlement.contract
    context: Dict[str, Any] = {
        "product_name": license_entitlement.product_name,
        "license_type": license_entitlement.license_type,
        "license_metric": license_entitlement.license_metric,
        "quantity_entitled": license_entitlement.quantity_entitled,
        "quantity_deployed": license_entitlement.quantity_deployed,
        "quantity_used": license_entitlement.quantity_used,
        "unit_cost": float(license_entitlement.unit_cost) if license_entitlement.unit_cost else None,
        "compliance_status": license_entitlement.compliance_status,
    }
    if contract is not None:
        context["contract"] = {
            "contract_name": contract.contract_name,
            "vendor_name": contract.vendor.name if contract.vendor else None,
            "end_date": contract.end_date.isoformat() if contract.end_date else None,
            "renewal_date": contract.renewal_date.isoformat() if contract.renewal_date else None,
        }
    return context


def _build_remediation_prompt(context: Dict[str, Any]) -> str:
    return (
        "You are assisting a Procurement lead remediating a software licence "
        "compliance issue. Below is the real, verified context for one "
        "licence entitlement - do not invent facts not present in it.\n\n"
        f"Licence context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce a remediation plan. Respond ONLY with a single JSON object "
        "with exactly these keys:\n"
        '- "summary": string, 1-3 sentences describing the compliance issue\n'
        '- "options": array of objects, each with exactly "option" (string, a '
        'short remediation option name) and "tradeoff" (string, its cost or '
        "risk tradeoff); provide 2-4 options\n"
        '- "recommended_option": string - MUST be exactly equal to one of the '
        '"option" values in the options array above\n'
        '- "rationale": string explaining the recommendation\n\n'
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _parse_remediation(raw: str) -> Dict[str, Any]:
    data = _parse_json_object(raw)
    required = {"summary", "options", "recommended_option", "rationale"}
    _require_keys(data, required)

    if not isinstance(data["options"], list) or not data["options"]:
        raise ProcurementAIError("LLM response field 'options' was not a non-empty list")

    options: List[Dict[str, str]] = []
    for item in data["options"]:
        if not isinstance(item, dict) or "option" not in item or "tradeoff" not in item:
            raise ProcurementAIError(
                "LLM response 'options' entries must each have 'option' and 'tradeoff'"
            )
        options.append({"option": str(item["option"]), "tradeoff": str(item["tradeoff"])})

    recommended = str(data["recommended_option"])
    if recommended not in {o["option"] for o in options}:
        raise ProcurementAIError(
            f"LLM response recommended_option {recommended!r} is not one of the offered options"
        )

    if not isinstance(data["summary"], str) or not isinstance(data["rationale"], str):
        raise ProcurementAIError("LLM response summary/rationale were not strings")

    return {
        "summary": data["summary"],
        "options": options,
        "recommended_option": recommended,
        "rationale": data["rationale"],
    }


def generate_remediation(license_entitlement) -> Dict[str, Any]:
    """Generate an AI remediation plan for a non-compliant licence entitlement.

    Advisory only - nothing is written back to the licence. Raises
    ProcurementAIError (or lets an LLMService exception propagate) rather
    than fabricating a fallback plan.
    """
    context = _build_remediation_context(license_entitlement)
    prompt = _build_remediation_prompt(context)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_remediation(raw)


# ---------------------------------------------------------------------------
# 3. Licence position - /procurement/api/licenses/ai-position
# ---------------------------------------------------------------------------


def _build_licenses_position_context(organization_id: int) -> Dict[str, Any]:
    from app.models.license_entitlement import LicenseEntitlement

    licences = LicenseEntitlement.query.filter_by(organization_id=organization_id).all()

    over_deployed = [lic for lic in licences if lic.compliance_status == "over_deployed"]
    unused = [
        lic
        for lic in licences
        if lic.quantity_entitled and lic.quantity_used is not None
        and lic.quantity_used < lic.quantity_entitled
    ]

    return {
        "total_licences": len(licences),
        "over_deployed_count": len(over_deployed),
        "unused_entitlement_count": len(unused),
        "over_deployed": [
            {
                "product_name": lic.product_name,
                "quantity_entitled": lic.quantity_entitled,
                "quantity_deployed": lic.quantity_deployed,
            }
            for lic in over_deployed[:MAX_CONTEXT_ROWS]
        ],
        "unused_entitlements": [
            {
                "product_name": lic.product_name,
                "quantity_entitled": lic.quantity_entitled,
                "quantity_used": lic.quantity_used,
            }
            for lic in unused[:MAX_CONTEXT_ROWS]
        ],
    }


def _build_licenses_position_prompt(context: Dict[str, Any]) -> str:
    return (
        "You are assisting a Procurement lead reviewing their organization's "
        "software licence position. Below is the real, verified aggregate "
        "context - do not invent facts not present in it.\n\n"
        f"Licence position context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce a licence position summary. Respond ONLY with a single JSON "
        "object with exactly these keys:\n"
        '- "summary": string, 2-4 sentences on the overall licence position\n'
        '- "anomalies": array of strings, notable patterns visible in the '
        "context (e.g. concentration of over-deployment, large unused "
        "entitlement)\n"
        '- "recommended_actions": array of strings, concrete next actions\n\n'
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _parse_licenses_position(raw: str) -> Dict[str, Any]:
    data = _parse_json_object(raw)
    required = {"summary", "anomalies", "recommended_actions"}
    _require_keys(data, required)

    if not isinstance(data["summary"], str):
        raise ProcurementAIError("LLM response summary was not a string")

    return {
        "summary": data["summary"],
        "anomalies": _as_str_list(data, "anomalies"),
        "recommended_actions": _as_str_list(data, "recommended_actions"),
    }


def generate_licenses_position(organization_id: int) -> Dict[str, Any]:
    """Generate an AI summary of the org's org-wide licence position.

    Advisory only. Raises ProcurementAIError (or lets an LLMService
    exception propagate) rather than fabricating a fallback summary.
    """
    context = _build_licenses_position_context(organization_id)
    prompt = _build_licenses_position_prompt(context)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_licenses_position(raw)


# ---------------------------------------------------------------------------
# 4. Spend recommendations - /procurement/api/spend/ai-recommendations
# ---------------------------------------------------------------------------


def _build_spend_context(organization_id: int) -> Dict[str, Any]:
    # Reuses the exact aggregates the spend_analytics page itself computes
    # (get_spend_by_category / get_spend_summary in services.py) rather than
    # running a third parallel query over the same VendorContract rows.
    spend_summary = get_spend_summary(organization_id)
    spend_by_category = get_spend_by_category(organization_id)

    return {
        "total_contracts": spend_summary["total_contracts"],
        "total_value": spend_summary["total_value"],
        "total_annual_cost": spend_summary["total_annual_cost"],
        "spend_by_category": spend_by_category,
        "spend_by_type": {
            k: v["annual_cost"] for k, v in spend_summary["by_type"].items()
        },
        "top_vendors": [
            {"vendor_name": name, "annual_cost": data["annual_cost"]}
            for name, data in spend_summary["top_vendors"]
        ],
    }


def _build_spend_prompt(context: Dict[str, Any]) -> str:
    return (
        "You are assisting a Procurement lead reviewing vendor spend. Below "
        "is the real, verified spend aggregate context - do not invent facts "
        "not present in it.\n\n"
        f"Spend context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce spend recommendations. Respond ONLY with a single JSON "
        "object with exactly these keys:\n"
        '- "summary": string, 2-4 sentences on the overall spend picture\n'
        '- "recommendations": array of objects, each with exactly "title" '
        '(string), "detail" (string), and "category" (string - the spend '
        "category or vendor the recommendation concerns); provide 1-5 "
        "recommendations\n\n"
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _parse_spend_recommendations(raw: str) -> Dict[str, Any]:
    data = _parse_json_object(raw)
    required = {"summary", "recommendations"}
    _require_keys(data, required)

    if not isinstance(data["summary"], str):
        raise ProcurementAIError("LLM response summary was not a string")

    if not isinstance(data["recommendations"], list):
        raise ProcurementAIError("LLM response field 'recommendations' was not a list")

    recommendations: List[Dict[str, str]] = []
    for item in data["recommendations"]:
        if not isinstance(item, dict) or not {"title", "detail", "category"} <= item.keys():
            raise ProcurementAIError(
                "LLM response 'recommendations' entries must each have "
                "'title', 'detail' and 'category'"
            )
        recommendations.append(
            {
                "title": str(item["title"]),
                "detail": str(item["detail"]),
                "category": str(item["category"]),
            }
        )

    return {"summary": data["summary"], "recommendations": recommendations}


def generate_spend_recommendations(organization_id: int) -> Dict[str, Any]:
    """Generate AI spend recommendations from the org's real spend aggregates.

    Advisory only. Raises ProcurementAIError (or lets an LLMService
    exception propagate) rather than fabricating a fallback result.
    """
    context = _build_spend_context(organization_id)
    prompt = _build_spend_prompt(context)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_spend_recommendations(raw)
