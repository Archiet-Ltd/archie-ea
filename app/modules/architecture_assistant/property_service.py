"""Property Service — templates, visibility, scoring, and save for element properties."""

import json
import logging
import re

from app.config.property_templates import PROPERTY_TEMPLATES
from app.models.acm_property_template import AcmPropertyTemplate

logger = logging.getLogger(__name__)

_NUMERIC_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))(?:\s*(.+))?$")
_PROPERTY_TEMPLATE_META = {
    (row["archimate_type"], row["property_key"]): row for row in PROPERTY_TEMPLATES
}


class PropertyValidationError(ValueError):
    """Raised when a typed property value cannot be stored."""


def _coerce_number(raw, unit=None):
    if isinstance(raw, bool):
        raise PropertyValidationError("Expected a number, got a boolean.")
    if isinstance(raw, (int, float)):
        return int(raw) if isinstance(raw, int) or float(raw).is_integer() else float(raw)
    text = str(raw or "").strip()
    if not text:
        return None
    match = _NUMERIC_RE.match(text.replace(",", ""))
    if not match:
        raise PropertyValidationError("Enter a number for this property.")
    number_text, parsed_unit = match.groups()
    if unit and parsed_unit and parsed_unit.strip() != unit:
        raise PropertyValidationError(f"Enter a number in {unit}.")
    number = float(number_text)
    return int(number) if number.is_integer() else number


def _coerce_boolean(raw):
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if text in ("", "none"):
        return None
    if text in ("true", "1", "yes", "y", "on"):
        return True
    if text in ("false", "0", "no", "n", "off"):
        return False
    raise PropertyValidationError("Enter true or false for this property.")


def _coerce_enum(raw, options):
    if raw in (None, ""):
        return None
    if raw in (options or []):
        return raw
    raise PropertyValidationError("Choose one of the allowed values for this property.")


def _coerce_multi_select(raw):
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def tiers_up_to(tier):
    """Returns list of required tiers up to and including the given tier."""
    order = ["standard", "important", "differentiating"]
    try:
        return order[:order.index(tier) + 1]
    except ValueError:
        return ["standard"]


def is_visible(template, properties):
    """Returns True if the property should be shown based on conditional logic.

    Handles both raw values and {value, source} dict format.
    """
    if template.conditional_on_key is None:
        return True
    parent_val = properties.get(template.conditional_on_key)
    # Handle {value, source} format
    if isinstance(parent_val, dict):
        parent_val = parent_val.get("value")
    if isinstance(parent_val, bool):
        return str(parent_val).lower() == template.conditional_on_value.lower()
    if parent_val is None:
        return False
    return str(parent_val).lower() == str(template.conditional_on_value).lower()


def template_query(organization_id=None):
    """Every property template one organisation may read.

    Shared platform templates (``organization_id`` NULL) plus that
    organisation's own governed definitions, never another organisation's.
    ``organization_id`` defaults to the request's tenant; with no tenant at all
    only the shared templates are returned. Every reader of
    ``AcmPropertyTemplate`` goes through this so an organisation's own
    definitions never reach another organisation.
    """
    from app.models.acm_property_template import AcmPropertyTemplate

    if organization_id is None:
        from app.middleware.tenant_context import current_org_id

        organization_id = current_org_id()
    shared = AcmPropertyTemplate.organization_id.is_(None)
    if organization_id is None:
        return AcmPropertyTemplate.query.filter(shared)
    return AcmPropertyTemplate.query.filter(
        shared | (AcmPropertyTemplate.organization_id == organization_id)
    )


class PropertyService:
    """Manages element property templates, scoring, and persistence."""

    def _template_for(self, archimate_type, key, organization_id=None):
        return template_query(organization_id).filter_by(
            archimate_type=archimate_type,
            property_key=key,
        ).first()

    def _template_unit(self, archimate_type, key):
        meta = _PROPERTY_TEMPLATE_META.get((archimate_type, key), {})
        return meta.get("unit")

    @staticmethod
    def _legacy_properties_dict(record):
        raw = getattr(record, "properties", None)
        if not raw:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _coerce_value(self, *, archimate_type, key, raw_value, organization_id=None):
        template = self._template_for(archimate_type, key, organization_id=organization_id)
        if template is None:
            return {"value": raw_value}

        unit = self._template_unit(archimate_type, key)

        if template.property_type == "number":
            coerced = _coerce_number(raw_value, unit=unit)
        elif template.property_type == "boolean":
            coerced = _coerce_boolean(raw_value)
        elif template.property_type == "enum":
            coerced = _coerce_enum(raw_value, template.enum_options)
        elif template.property_type == "multi-select":
            coerced = _coerce_multi_select(raw_value)
        else:
            coerced = None if raw_value is None else str(raw_value).strip()

        entry = {"value": coerced}
        if unit:
            entry["unit"] = unit
        return entry

    def _set_record_property(self, record, *, archimate_type, key, value, source="user"):
        props = dict(getattr(record, "acm_properties", None) or {})
        entry = self._coerce_value(
            archimate_type=archimate_type,
            key=key,
            raw_value=value,
            organization_id=getattr(record, "organization_id", None),
        )
        entry["source"] = source
        props[key] = entry
        record.acm_properties = props
        if hasattr(record, "properties"):
            legacy = self._legacy_properties_dict(record)
            legacy[key] = entry.get("value")
            record.properties = json.dumps(legacy)
        return entry

    def set_element_property(self, element, key, value, source="user"):
        """Write one typed property onto an element through the canonical writer."""
        return self._set_record_property(
            element,
            archimate_type=getattr(element, "type", None),
            key=key,
            value=value,
            source=source,
        )

    def merge_element_properties(self, element, updates, source="user"):
        """Write multiple element properties through the canonical writer."""
        for key, value in (updates or {}).items():
            self.set_element_property(element, key, value, source=source)
        return getattr(element, "acm_properties", None) or {}

    def merge_typed_properties(self, existing, archimate_type, updates, source="user"):
        """Merge a dict of typed property updates for a staged record."""
        class _Record:
            pass

        record = _Record()
        record.acm_properties = dict(existing or {})
        for key, value in (updates or {}).items():
            self._set_record_property(record, archimate_type=archimate_type, key=key, value=value, source=source)
        return record.acm_properties

    def get_templates_for_type(self, archimate_type, tier="standard", domain=None, tag_filter=None):
        """Get property templates for an element type, filtered by tier."""
        query = template_query().filter_by(
            archimate_type=archimate_type,
        ).filter(
            AcmPropertyTemplate.required_for_tier.in_(tiers_up_to(tier))
        )

        if domain:
            query = query.filter(
                (AcmPropertyTemplate.acm_domain == domain) |
                (AcmPropertyTemplate.acm_domain.is_(None))
            )

        if tag_filter:
            query = query.filter(AcmPropertyTemplate.property_key.like(tag_filter + "%"))

        templates = []
        for template in query.order_by(AcmPropertyTemplate.sort_order).all():
            data = template.to_dict()
            unit = self._template_unit(template.archimate_type, template.property_key)
            if unit:
                data["unit"] = unit
            templates.append(data)
        return templates

    def calculate_element_score(self, archimate_type, properties, tier="standard", domain=None, tag_filter=None):
        """Calculate property completeness for a single element. Returns float 0.0-1.0."""
        query = template_query().filter_by(
            archimate_type=archimate_type,
        ).filter(
            AcmPropertyTemplate.required_for_tier.in_(tiers_up_to(tier))
        )

        if tag_filter:
            query = query.filter(AcmPropertyTemplate.property_key.like(tag_filter + "%"))

        templates = query.all()
        visible = [t for t in templates if is_visible(t, properties)]

        if not visible:
            return 1.0

        filled = 0
        for t in visible:
            val = properties.get(t.property_key)
            if isinstance(val, dict):
                val = val.get("value")
            if val not in (None, "", "TBD"):
                filled += 1

        return filled / len(visible)

    # Sensible defaults when AcmPropertyTemplate.default_value is NULL
    _FALLBACK_DEFAULTS = {
        "ApplicationComponent": {
            "deployment_model": "cloud-native", "build_or_buy": "build",
            "availability_target": "99.9%", "hosting_target": "Cloud (TBD)",
            "technology_stack": "TBD", "estimated_users": "TBD",
            "scalability_pattern": "horizontal", "api_style": "REST",
            "team_owner": "TBD",
        },
        "ApplicationService": {
            "deployment_model": "cloud-native", "build_or_buy": "build",
            "availability_target": "99.9%", "api_style": "REST",
            "hosting_target": "Cloud (TBD)", "technology_stack": "TBD",
            "estimated_users": "TBD", "scalability_pattern": "horizontal",
            "team_owner": "TBD",
        },
        "ApplicationFunction": {
            "deployment_model": "cloud-native", "build_or_buy": "build",
        },
        "ApplicationInterface": {
            "interface_type": "REST-API", "authentication": "OAuth2",
            "rate_limit": "1000 req/min", "data_format": "JSON",
            "versioning_strategy": "URL-path",
        },
        "DataObject": {
            "data_classification": "internal", "contains_pii": False,
            "retention_period": "7 years", "retention_justification": "regulatory",
            "encryption_at_rest": "AES-256", "encryption_in_transit": "TLS-1.3",
            "backup_strategy": "daily", "refresh_frequency": "daily",
            "estimated_volume_initial": "TBD", "estimated_growth_monthly": "TBD",
            "data_owner": "TBD",
        },
        "BusinessObject": {
            "data_classification": "internal",
        },
        "Node": {
            "network_zone": "private", "managed_service": True,
            "dr_strategy": "active-passive", "compute_spec": "TBD",
            "storage_spec": "TBD", "license_model": "open-source",
            "support_tier": "standard",
        },
        "SystemSoftware": {
            "network_zone": "private", "managed_service": True,
            "license_model": "open-source", "support_tier": "standard",
        },
        "CommunicationNetwork": {
            "network_zone": "private",
        },
        "BusinessProcess": {
            "automation_level": "semi-automated",
            "process_frequency": "On demand", "responsible_team": "TBD",
        },
        "Requirement": {
            "priority": "should-have",
        },
        "Constraint": {
            "priority": "must-have",
        },
        "Principle": {
            "priority": "should-have",
        },
    }

    def get_default_properties(self, archimate_type, tier="standard"):
        """Return default property values for an element type.

        First reads from AcmPropertyTemplate.default_value, then falls back
        to hardcoded sensible defaults. Used to pre-fill baseline/NFR proposals
        so the architect starts with sensible values, not blanks.
        """
        props = {}

        # Start with hardcoded fallbacks for this type
        fallbacks = self._FALLBACK_DEFAULTS.get(archimate_type, {})
        for key, val in fallbacks.items():
            props[key] = {"value": val, "source": "default"}

        # Override with template defaults where they exist
        try:
            templates = template_query().filter_by(
                archimate_type=archimate_type,
            ).filter(
                AcmPropertyTemplate.required_for_tier.in_(tiers_up_to(tier))
            ).all()
            for t in templates:
                if t.default_value is not None and t.default_value != "":
                    props[t.property_key] = self._coerce_value(
                        archimate_type=archimate_type,
                        key=t.property_key,
                        raw_value=t.default_value,
                    ) | {"source": "default"}
        except Exception as e:
            logger.debug("Property template query skipped: %s", e)

        return props

    def merge_properties(self, existing, updates, archimate_type=None, source="user"):
        """Merge property updates, coercing via templates when a type is known."""
        if archimate_type:
            return self.merge_typed_properties(existing, archimate_type, updates, source=source)
        merged = dict(existing) if existing else {}
        for key, value in (updates or {}).items():
            merged[key] = {"value": value, "source": source}
        return merged

    @staticmethod
    def acm_slot_is_fillable(raw):
        """True if a default/LLM/suggested value may be written (never overwrites user)."""
        if raw is None:
            return True
        if isinstance(raw, dict):
            if raw.get("source") == "user":
                return False
            v = raw.get("value")
            if v is None:
                return True
            if isinstance(v, bool):
                return False
            s = str(v).strip()
            return s in ("", "TBD")
        s = str(raw).strip()
        return s in ("", "TBD")

    def merge_template_defaults_only(self, existing, archimate_type, tier="standard"):
        """Apply get_default_properties only where slots are empty. Preserves user edits."""
        props = dict(existing) if existing else {}
        defaults = self.get_default_properties(archimate_type, tier=tier)
        for key, entry in defaults.items():
            if self.acm_slot_is_fillable(props.get(key)):
                props[key] = entry
        return props


# ── Governed definitions an organisation adds to its own metamodel ──────────
#
# An organisation defines a typed property for one element type: a type from
# GOVERNED_PROPERTY_TYPES, optional allowed values and a mandatory flag. The
# definition is an ordinary AcmPropertyTemplate row carrying the organisation,
# so every template reader above sees it through template_query() and no
# second definition store exists. Values stay in the element's acm_properties
# as {"value", "source"}; validate_governed_updates() is called by the element
# and proposal property writers before they store anything.

GOVERNED_PROPERTY_TYPES = ("text", "number", "boolean", "date", "enum")

GOVERNED_TYPE_LABELS = {
    "text": "Text",
    "number": "Number",
    "boolean": "Yes or no",
    "date": "Date",
    "enum": "One of a list",
}

_TRUE_WORDS = {"true", "yes", "y", "1"}
_FALSE_WORDS = {"false", "no", "n", "0"}
_EMPTY_VALUES = (None, "", "TBD")


class PropertyDefinitionError(ValueError):
    """A governed property definition that cannot be saved, with the reason."""


def metamodel_element_types():
    """Every ArchiMate 3.2 element type, from the relationship validity matrix."""
    from app.services.archimate_validity_service import _TYPE_LAYER

    return sorted(_TYPE_LAYER)


def normalise_element_type(element_type):
    """``application_component`` and ``ApplicationComponent`` name one type."""
    from app.services.archimate_validity_service import _normalize_type

    return _normalize_type(element_type or "")


def _snake(pascal):
    out = []
    for i, ch in enumerate(pascal):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def property_key_for(display_name):
    """A stable key from a display name: lower case words joined by ``_``."""
    words = re.findall(r"[a-z0-9]+", (display_name or "").lower())
    key = "_".join(words)[:64]
    if key and key[0].isdigit():
        key = ("p_" + key)[:64]
    return key


def _raw_value(raw):
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def is_empty_value(raw):
    value = _raw_value(raw)
    if isinstance(value, str):
        value = value.strip()
    return value in _EMPTY_VALUES


def coerce_governed_value(template, raw):
    """``(value, None)`` when *raw* fits the definition, else ``(None, reason)``.

    An empty value is ``(None, None)`` for an optional property and a refusal
    for a mandatory one. A value that does not parse is refused with a reason;
    it is never stored as text or as ``0``.
    """
    name = template.display_name or template.property_key
    value = _raw_value(raw)
    if isinstance(value, str):
        value = value.strip()
    if value in _EMPTY_VALUES:
        if template.is_mandatory:
            return None, "%s needs a value." % name
        return None, None

    kind = template.property_type
    if kind == "number":
        if isinstance(value, bool):
            return None, "%s must be a number." % name
        try:
            number = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None, "%s must be a number." % name
        if number != number or number in (float("inf"), float("-inf")):
            return None, "%s must be a number." % name
        return (int(number) if number.is_integer() else number), None
    if kind == "boolean":
        if isinstance(value, bool):
            return value, None
        word = str(value).strip().lower()
        if word in _TRUE_WORDS:
            return True, None
        if word in _FALSE_WORDS:
            return False, None
        return None, "%s must be yes or no." % name
    if kind == "date":
        from datetime import date

        try:
            return date.fromisoformat(str(value)).isoformat(), None
        except ValueError:
            return None, "%s must be a date written as YYYY-MM-DD." % name
    if kind == "enum":
        options = [str(o) for o in (template.enum_options or [])]
        if str(value) not in options:
            return None, "%s must be one of: %s." % (name, ", ".join(options))
        return str(value), None
    return str(value), None


class GovernedPropertyService:
    """Define governed properties and read which elements are missing them."""

    def definitions(self, organization_id):
        """This organisation's own definitions, by element type then name."""
        from app.models.acm_property_template import AcmPropertyTemplate

        return (
            AcmPropertyTemplate.query.filter(AcmPropertyTemplate.organization_id == organization_id)
            .order_by(AcmPropertyTemplate.archimate_type, AcmPropertyTemplate.display_name)
            .all()
        )

    def definition(self, organization_id, definition_id):
        """One of this organisation's definitions, or ``None`` (also for another's)."""
        from app.models.acm_property_template import AcmPropertyTemplate

        return AcmPropertyTemplate.query.filter(
            AcmPropertyTemplate.id == definition_id,
            AcmPropertyTemplate.organization_id == organization_id,
        ).first()

    def define(
        self,
        organization_id,
        *,
        archimate_type,
        display_name,
        property_type,
        allowed_values=None,
        mandatory=False,
        help_text=None,
    ):
        """Save one governed definition; raises PropertyDefinitionError with the reason."""
        from app import db
        from app.models.acm_property_template import AcmPropertyTemplate

        if organization_id is None:
            raise PropertyDefinitionError("No organisation is selected.")
        archimate_type = normalise_element_type((archimate_type or "").strip())
        if archimate_type not in metamodel_element_types():
            raise PropertyDefinitionError("Choose an element type.")
        display_name = (display_name or "").strip()
        if not display_name:
            raise PropertyDefinitionError("Give the property a name.")
        if len(display_name) > 128:
            raise PropertyDefinitionError("The name is longer than 128 characters.")
        key = property_key_for(display_name)
        if not key:
            raise PropertyDefinitionError("The name needs at least one letter or digit.")
        if property_type not in GOVERNED_PROPERTY_TYPES:
            raise PropertyDefinitionError("Choose a value type.")

        options = None
        if property_type == "enum":
            options = []
            for item in allowed_values or []:
                item = str(item).strip()
                if item and item not in options:
                    options.append(item[:256])
            if len(options) < 2:
                raise PropertyDefinitionError("A list needs at least two allowed values.")
        elif allowed_values and any(str(v).strip() for v in allowed_values):
            raise PropertyDefinitionError("Allowed values apply only to a list.")

        clash = template_query(organization_id).filter(
            AcmPropertyTemplate.archimate_type == archimate_type,
            AcmPropertyTemplate.property_key == key,
        ).first()
        if clash is not None:
            raise PropertyDefinitionError(
                "%s already has a property called %s." % (archimate_type, clash.display_name)
            )

        row = AcmPropertyTemplate(
            organization_id=organization_id,
            archimate_type=archimate_type,
            property_key=key,
            display_name=display_name,
            property_type=property_type,
            enum_options=options,
            required_for_tier="standard",
            is_mandatory=bool(mandatory),
            help_text=(help_text or "").strip() or None,
            sort_order=1000,
        )
        db.session.add(row)
        db.session.commit()
        return row

    def governed_templates(self, archimate_type, organization_id=None):
        """This organisation's governed definitions for one element type, by key."""
        from app.models.acm_property_template import AcmPropertyTemplate

        if organization_id is None:
            from app.middleware.tenant_context import current_org_id

            organization_id = current_org_id()
        if organization_id is None or not archimate_type:
            return {}
        rows = AcmPropertyTemplate.query.filter(
            AcmPropertyTemplate.organization_id == organization_id,
            AcmPropertyTemplate.archimate_type == normalise_element_type(archimate_type),
        ).all()
        return {row.property_key: row for row in rows}

    def validate_updates(self, archimate_type, updates, organization_id=None):
        """Check a property patch against the organisation's governed definitions.

        Returns ``(values, errors)``: *values* is the patch with every governed
        key coerced to its declared type (other keys pass through unchanged),
        *errors* the plain reasons for every refused key. A caller stores
        nothing when *errors* is not empty.
        """
        governed = self.governed_templates(archimate_type, organization_id)
        values = dict(updates or {})
        errors = []
        for key, raw in (updates or {}).items():
            template = governed.get(key)
            if template is None:
                continue
            value, error = coerce_governed_value(template, raw)
            if error:
                errors.append(error)
            else:
                values[key] = value
        return values, errors

    def missing_values(self, template, organization_id):
        """Elements of the definition's type in this organisation with no value.

        Returns dicts of ``id``, ``name`` and ``type``, by name. Only the
        organisation's own elements are read (the explicit predicate keeps this
        correct outside a request as well).
        """
        from app import db
        from app.models import ArchiMateElement

        if template is None or organization_id is None:
            return []
        if template.organization_id is not None and template.organization_id != organization_id:
            # Another organisation's definition says nothing about this one.
            return []
        type_names = {template.archimate_type, _snake(template.archimate_type)}
        rows = db.session.execute(
            db.select(
                ArchiMateElement.id,
                ArchiMateElement.name,
                ArchiMateElement.type,
                ArchiMateElement.acm_properties,
            )
            .where(
                ArchiMateElement.organization_id == organization_id,
                ArchiMateElement.type.in_(sorted(type_names)),
                ArchiMateElement.deleted_at.is_(None),
            )
            .order_by(ArchiMateElement.name, ArchiMateElement.id)
        ).all()
        missing = []
        for element_id, name, element_type, props in rows:
            if is_empty_value((props or {}).get(template.property_key)):
                missing.append({"id": element_id, "name": name, "type": element_type})
        return missing
