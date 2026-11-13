"""
Clause Extraction Service

Analyzes VendorContract records to extract structured clause data including:
- Named entities (dates, money, parties, services, requirements, obligations)
- Relations (REQUIRES, PROVIDES, OBLIGATES, DEADLINE, PENALTY, CONDITION)
- Obligations timeline with urgency scoring
- Compliance requirement mapping
- Confidence scoring based on data completeness
"""

import json
import logging
from datetime import datetime, timedelta

from app.models.application_portfolio import VendorContract
from app.services import vendor_register
from app.services.vendor_register import days_until, renewal_band

logger = logging.getLogger(__name__)

# Expected fields for confidence scoring
EXPECTED_FIELDS = [
    "contract_name",
    "contract_description",
    "contract_number",
    "contract_type",
    "contract_category",
    "pricing_model",
    "contract_value",
    "annual_cost",
    "start_date",
    "end_date",
    "renewal_date",
    "auto_renewal",
    "notice_period_days",
    "license_type",
    "license_quantity",
    "usage_restrictions",
    "support_level",
    "support_hours",
    "sla_response_time",
    "maintenance_included",
    "compliance_requirements",
    "security_requirements",
    "data_location_requirements",
    "status",
    "contract_owner",
    "legal_approver",
    "business_approver",
    "vendor_risk",
    "contract_risk",
    "exit_complexity",
]


def _deadline_urgency(days):
    """A dated obligation's urgency, from the one renewal banding: critical or warning when it is near or
    past, info otherwise."""
    band = renewal_band(days)
    return "critical" if band == "expired" else band if band in ("critical", "warning") else "info"


class ClauseExtractionService:
    """Extracts structured clause data from VendorContract records."""

    def analyze_vendor_contracts(self, vendor_id):
        """Main entry point. Analyze all contracts for a vendor.

        Args:
            vendor_id: ID of the VendorOrganization.

        Returns:
            Dict with entities, relations, timeline, compliance_map, summary.
        """
        contracts = VendorContract.query.filter_by(vendor_id=vendor_id).all()

        if not contracts:
            return {
                "contracts_analyzed": 0,
                "entities": [],
                "relations": [],
                "timeline": [],
                "compliance_map": {},
                "summary": {
                    "total_contracts": 0,
                    "active_count": 0,
                    "total_annual_spend": None,
                    "upcoming_renewals": 0,
                    "high_risk_count": 0,
                    "avg_confidence": 0,
                },
            }

        all_entities = []
        all_relations = []
        confidences = []

        for contract in contracts:
            entities = self._extract_entities(contract)
            relations = self._extract_relations(contract)
            confidence = self._calculate_confidence(contract)

            all_entities.extend(entities)
            all_relations.extend(relations)
            confidences.append(confidence)

        timeline = self._build_obligations_timeline(contracts)
        compliance_map = self._map_compliance_requirements(contracts)
        summary = self._build_summary(contracts, all_entities, all_relations, confidences)

        return {
            "contracts_analyzed": len(contracts),
            "entities": all_entities,
            "relations": all_relations,
            "timeline": timeline,
            "compliance_map": compliance_map,
            "summary": summary,
        }

    def _extract_entities(self, contract):
        """Extract typed entities from a contract's fields."""
        entities = []
        source = contract.contract_name or contract.contract_number or f"Contract #{contract.id}"

        # DATE entities
        if contract.start_date:
            entities.append(
                {
                    "type": "DATE",
                    "label": "Contract Start",
                    "value": contract.start_date.isoformat(),
                    "source": source,
                }
            )
        if contract.end_date:
            entities.append(
                {
                    "type": "DATE",
                    "label": "Contract End",
                    "value": contract.end_date.isoformat(),
                    "source": source,
                }
            )
        if contract.renewal_date:
            entities.append(
                {
                    "type": "DATE",
                    "label": "Renewal Date",
                    "value": contract.renewal_date.isoformat(),
                    "source": source,
                }
            )

        # MONEY entities
        # a contract that states no currency is in the organisation's, as the contract ledger reads it
        from app.services.application_cost_accessor import get_reporting_currency

        currency = contract.currency or get_reporting_currency(contract.organization_id)
        if contract.contract_value:
            entities.append(
                {
                    "type": "MONEY",
                    "label": "Total Contract Value",
                    "value": f"{currency} {contract.contract_value:,.2f}",
                    "source": source,
                }
            )
        if contract.annual_cost:
            entities.append(
                {
                    "type": "MONEY",
                    "label": "Annual Cost",
                    "value": f"{currency} {contract.annual_cost:,.2f}",
                    "source": source,
                }
            )

        # PARTY entities
        if contract.contract_owner:
            entities.append(
                {
                    "type": "PARTY",
                    "label": "Contract Owner",
                    "value": contract.contract_owner,
                    "source": source,
                }
            )
        if contract.legal_approver:
            entities.append(
                {
                    "type": "PARTY",
                    "label": "Legal Approver",
                    "value": contract.legal_approver,
                    "source": source,
                }
            )
        if contract.business_approver:
            entities.append(
                {
                    "type": "PARTY",
                    "label": "Business Approver",
                    "value": contract.business_approver,
                    "source": source,
                }
            )

        # SERVICE entities
        if contract.support_level:
            entities.append(
                {
                    "type": "SERVICE",
                    "label": "Support Level",
                    "value": contract.support_level.replace("_", " ").title(),
                    "source": source,
                }
            )
        if contract.support_hours:
            entities.append(
                {
                    "type": "SERVICE",
                    "label": "Support Hours",
                    "value": contract.support_hours,
                    "source": source,
                }
            )
        if contract.sla_response_time:
            entities.append(
                {
                    "type": "SERVICE",
                    "label": "SLA Response Time",
                    "value": contract.sla_response_time,
                    "source": source,
                }
            )

        # REQUIREMENT entities
        for item in self._safe_parse_json(contract.compliance_requirements):
            entities.append(
                {
                    "type": "REQUIREMENT",
                    "label": "Compliance Requirement",
                    "value": str(item),
                    "source": source,
                }
            )
        for item in self._safe_parse_json(contract.security_requirements):
            entities.append(
                {
                    "type": "REQUIREMENT",
                    "label": "Security Requirement",
                    "value": str(item),
                    "source": source,
                }
            )
        if contract.data_location_requirements:
            entities.append(
                {
                    "type": "REQUIREMENT",
                    "label": "Data Location",
                    "value": contract.data_location_requirements,
                    "source": source,
                }
            )

        # OBLIGATION entities
        if contract.auto_renewal is not None:
            entities.append(
                {
                    "type": "OBLIGATION",
                    "label": "Auto-Renewal",
                    "value": "Yes" if contract.auto_renewal else "No",
                    "source": source,
                }
            )
        if contract.notice_period_days:
            entities.append(
                {
                    "type": "OBLIGATION",
                    "label": "Notice Period",
                    "value": f"{contract.notice_period_days} days",
                    "source": source,
                }
            )
        if contract.maintenance_included is not None:
            entities.append(
                {
                    "type": "OBLIGATION",
                    "label": "Maintenance Included",
                    "value": "Yes" if contract.maintenance_included else "No",
                    "source": source,
                }
            )
        for item in self._safe_parse_json(contract.usage_restrictions):
            entities.append(
                {
                    "type": "OBLIGATION",
                    "label": "Usage Restriction",
                    "value": str(item),
                    "source": source,
                }
            )

        return entities

    def _extract_relations(self, contract):
        """Extract relation triples from a contract."""
        relations = []
        source = contract.contract_name or contract.contract_number or f"Contract #{contract.id}"

        # REQUIRES: contract requires compliance/security requirements
        for item in self._safe_parse_json(contract.compliance_requirements):
            relations.append(
                {
                    "type": "REQUIRES",
                    "subject": source,
                    "object": str(item),
                    "detail": "Compliance requirement",
                }
            )
        for item in self._safe_parse_json(contract.security_requirements):
            relations.append(
                {
                    "type": "REQUIRES",
                    "subject": source,
                    "object": str(item),
                    "detail": "Security requirement",
                }
            )

        # PROVIDES: contract provides support/maintenance
        if contract.support_level:
            relations.append(
                {
                    "type": "PROVIDES",
                    "subject": source,
                    "object": f"{contract.support_level} support",
                    "detail": f"Hours: {contract.support_hours or 'Not specified'}",
                }
            )
        if contract.maintenance_included:
            relations.append(
                {
                    "type": "PROVIDES",
                    "subject": source,
                    "object": "Maintenance services",
                    "detail": "Included in contract",
                }
            )

        # OBLIGATES: contract obligates parties
        if contract.contract_owner:
            relations.append(
                {
                    "type": "OBLIGATES",
                    "subject": source,
                    "object": contract.contract_owner,
                    "detail": "Contract owner / governance lead",
                }
            )
        if contract.legal_approver:
            relations.append(
                {
                    "type": "OBLIGATES",
                    "subject": source,
                    "object": contract.legal_approver,
                    "detail": "Legal approval authority",
                }
            )

        # DEADLINE: contract has deadlines
        if contract.end_date:
            relations.append(
                {
                    "type": "DEADLINE",
                    "subject": source,
                    "object": contract.end_date.isoformat(),
                    "detail": "Contract expiration",
                }
            )
        if contract.renewal_date:
            relations.append(
                {
                    "type": "DEADLINE",
                    "subject": source,
                    "object": contract.renewal_date.isoformat(),
                    "detail": "Renewal deadline",
                }
            )
        if contract.end_date and contract.notice_period_days:
            notice_date = contract.end_date - timedelta(days=contract.notice_period_days)
            relations.append(
                {
                    "type": "DEADLINE",
                    "subject": source,
                    "object": notice_date.isoformat(),
                    "detail": f"Notice period start ({contract.notice_period_days} days before end)",
                }
            )

        # PENALTY: exit complexity
        if contract.exit_complexity:
            relations.append(
                {
                    "type": "PENALTY",
                    "subject": source,
                    "object": f"{contract.exit_complexity} exit complexity",
                    "detail": f"Vendor risk: {contract.vendor_risk or 'unassessed'}, "
                    f"Contract risk: {contract.contract_risk or 'unassessed'}",
                }
            )

        # CONDITION: conditional terms
        if contract.auto_renewal and contract.notice_period_days:
            relations.append(
                {
                    "type": "CONDITION",
                    "subject": source,
                    "object": "Auto-renewal",
                    "detail": f"Renews automatically unless {contract.notice_period_days}-day notice given",
                }
            )

        return relations

    def _build_obligations_timeline(self, contracts):
        """Build chronological timeline of obligation dates."""
        now = datetime.utcnow().date()
        events = []

        def when(contract, target):
            """Days to a dated obligation and its urgency; a contract that is not active has neither."""
            if not vendor_register.is_marked_active(contract):
                return None, None
            days = days_until(target, now)
            return days, _deadline_urgency(days)

        for contract in contracts:
            source = (
                contract.contract_name or contract.contract_number or f"Contract #{contract.id}"
            )

            if contract.start_date:
                events.append(
                    {
                        "date": contract.start_date.isoformat(),
                        "sort_date": contract.start_date,
                        "description": f"{source} — Contract start",
                        "event_type": "start",
                        "source": source,
                    }
                )
            if contract.end_date:
                days_remaining, urgency = when(contract, contract.end_date)
                events.append(
                    {
                        "date": contract.end_date.isoformat(),
                        "sort_date": contract.end_date,
                        "description": f"{source} — Contract expiration",
                        "event_type": "end",
                        "urgency": urgency,
                        "days_remaining": days_remaining,
                        "source": source,
                    }
                )
            if contract.renewal_date:
                days_remaining, urgency = when(contract, contract.renewal_date)
                events.append(
                    {
                        "date": contract.renewal_date.isoformat(),
                        "sort_date": contract.renewal_date,
                        "description": f"{source} — Renewal deadline",
                        "event_type": "renewal",
                        "urgency": urgency,
                        "days_remaining": days_remaining,
                        "source": source,
                    }
                )
            if contract.end_date and contract.notice_period_days:
                notice_date = contract.end_date - timedelta(days=contract.notice_period_days)
                days_remaining, urgency = when(contract, notice_date)
                events.append(
                    {
                        "date": notice_date.isoformat(),
                        "sort_date": notice_date,
                        "description": f"{source} — Notice period begins ({contract.notice_period_days}d)",
                        "event_type": "notice",
                        "urgency": urgency,
                        "days_remaining": days_remaining,
                        "source": source,
                    }
                )

        events.sort(key=lambda e: e["sort_date"])

        # Remove sort helper before returning
        for event in events:
            event.pop("sort_date", None)

        return events

    def _map_compliance_requirements(self, contracts):
        """Group compliance/security/data requirements by category."""
        compliance_map = {
            "compliance": {"items": [], "sources": []},
            "security": {"items": [], "sources": []},
            "data_location": {"items": [], "sources": []},
        }

        for contract in contracts:
            source = (
                contract.contract_name or contract.contract_number or f"Contract #{contract.id}"
            )

            for item in self._safe_parse_json(contract.compliance_requirements):
                item_str = str(item)
                if item_str not in compliance_map["compliance"]["items"]:
                    compliance_map["compliance"]["items"].append(item_str)
                if source not in compliance_map["compliance"]["sources"]:
                    compliance_map["compliance"]["sources"].append(source)

            for item in self._safe_parse_json(contract.security_requirements):
                item_str = str(item)
                if item_str not in compliance_map["security"]["items"]:
                    compliance_map["security"]["items"].append(item_str)
                if source not in compliance_map["security"]["sources"]:
                    compliance_map["security"]["sources"].append(source)

            if contract.data_location_requirements:
                loc = contract.data_location_requirements
                if loc not in compliance_map["data_location"]["items"]:
                    compliance_map["data_location"]["items"].append(loc)
                if source not in compliance_map["data_location"]["sources"]:
                    compliance_map["data_location"]["sources"].append(source)

        return compliance_map

    def _calculate_confidence(self, contract):
        """Calculate confidence score as ratio of populated fields."""
        populated = 0
        for field_name in EXPECTED_FIELDS:
            value = getattr(contract, field_name, None)
            if value is not None and value != "" and value != []:
                populated += 1
        return round(populated / len(EXPECTED_FIELDS), 2)

    def _build_summary(self, contracts, entities, relations, confidences):
        """Build aggregate summary statistics."""
        now = datetime.utcnow().date()
        # active is the ledger's one definition; the annual spend is read from the one contract ledger:
        # active, in the organisation's currency
        from app.services import vendor_register

        active_count = sum(1 for c in contracts if vendor_register.is_active(c, now))

        ledger = vendor_register.contract_ledger(contracts[0].organization_id) if contracts else None
        in_ledger = {c.id for c in ledger["counted"]} if ledger else set()
        total_annual, _ = vendor_register.stated_annual_cost([c for c in contracts if c.id in in_ledger])
        upcoming_renewals = sum(1 for c in contracts if vendor_register.contract_renewal_due(c, now))
        high_risk = sum(
            1
            for c in contracts
            if c.contract_risk in ("high", "critical") or c.vendor_risk in ("high", "critical")
        )
        avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0

        return {
            "total_contracts": len(contracts),
            "active_count": active_count,
            "total_annual_spend": total_annual,
            "upcoming_renewals": upcoming_renewals,
            "high_risk_count": high_risk,
            "avg_confidence": avg_confidence,
            "entity_count": len(entities),
            "relation_count": len(relations),
        }

    @staticmethod
    def _safe_parse_json(text):
        """Safely parse a JSON text field, returning empty list on failure."""
        if not text:
            return []
        if isinstance(text, list):
            return text
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
            return [result]
        except (json.JSONDecodeError, TypeError):
            return []
