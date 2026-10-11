"""The AI-system profile — the 5th genome projection (PILLAR 6).

An *AI system* is a first-class modelled element of the architecture, not a
side table. Each one is an ``ArchiMateElement`` (``type='ApplicationComponent'``)
carrying an ``ai_system`` marker and a profile in ``custom_properties``. This
module owns:

  * the profile vocabulary (autonomy, data-sensitivity, governance fields);
  * the read side — turning an ``ArchiMateElement`` back into a typed profile,
    honestly (a value that was never recorded reads ``"unknown"``, never a
    fabricated default);
  * ``model_currency`` — current / stale / retired — reusing the model-currency
    facts already in ``app.modules.ai_chat.services.model_defaults``.

New fields (risk class, release state) are stored as typed properties on the
element's ``acm_properties``, not in ``custom_properties`` — see
``ai_system_classification.py``. The owner is read from the one owner record
(``ApplicationOwner``), not from a profile key.

Model currency reuses the stale-models denylist
-------------------------------------------------
The stale-models denylist lives, as prose, in the module docstring of
``app/modules/ai_chat/services/model_defaults.py`` — the six Claude/Gemini ids
that were "RETIRED and now return 404" (``claude-3-5-sonnet-20241022``,
``claude-3-sonnet-20240229``, ``claude-3-opus-*``/``-sonnet``/``-haiku``,
``gemini-1.5-flash``). That extract does not ship the
``scripts/check_stale_models.py`` denylist file the larger codebase carries, so
``RETIRED_MODEL_IDS`` below transcribes exactly those ids and is the single place
to keep in step with it. ``DEFAULT_MODELS``/``ECONOMY_MODELS``/``PRICING_PER_1K``
are imported live (not copied), so a model the operators actually run today is
classed *current* off the same source of truth the rest of the app uses.

Currency is a pure function of the recorded ``model_id`` — no network, no LLM —
so the slice and emitter stay deterministic.
"""

from __future__ import annotations

from app.modules.ai_chat.services.model_defaults import (
    DEFAULT_MODELS,
    ECONOMY_MODELS,
    PRICING_PER_1K,
)

# --- The marker that makes an ArchiMateElement an AI system -----------------
AI_SYSTEM_MARKER = "ai_system"
AI_SYSTEM_ELEMENT_TYPE = "ApplicationComponent"
AI_SYSTEM_ELEMENT_LAYER = "application"

UNKNOWN = "unknown"
NOT_SET = "not set"

# --- Controlled vocabularies (the profile's enums) --------------------------
AUTONOMY_LEVELS = (
    "assisted",
    "human-in-loop",
    "supervised-autonomous",
    "autonomous",
)
# Autonomy levels the org should not run without a governing approval gate.
HIGH_AUTONOMY_LEVELS = frozenset({"supervised-autonomous", "autonomous"})

DATA_SENSITIVITY = (
    "public",
    "internal",
    "confidential",
    "regulated",
)

MODEL_CURRENCY = ("current", "stale", "retired", UNKNOWN)

# --- Stale-models denylist (transcribed from model_defaults.py docstring) ---
# Keep in step with app/modules/ai_chat/services/model_defaults.py. Ids here
# 404 at the provider — a system still pointed at one is an architectural risk.
RETIRED_MODEL_IDS = frozenset(
    {
        "claude-3-5-sonnet-20241022",
        "claude-3-sonnet-20240229",
        "claude-3-opus-20240229",
        "claude-3-opus",
        "claude-3-sonnet",
        "claude-3-haiku",
        "claude-3-haiku-20240307",
        "gemini-1.5-flash",
    }
)


def _current_model_ids() -> frozenset[str]:
    """Model ids an operator can legitimately be running today, off the same
    source of truth the rest of the app defaults and prices against."""
    ids: set[str] = set()
    ids.update(DEFAULT_MODELS.values())
    ids.update(ECONOMY_MODELS.values())
    ids.update(PRICING_PER_1K.keys())
    return frozenset(i for i in ids if i)


def model_currency(model_id: str | None) -> str:
    """Classify a recorded model id as current / stale / retired / unknown.

    * ``unknown`` — nothing recorded. Never a guess.
    * ``retired`` — on the stale-models denylist (returns 404 at the provider).
    * ``current`` — a model the app defaults to, prices, or runs as an economy
      tier today (read live from ``model_defaults``).
    * ``stale`` — a real, recorded id that is neither current nor retired: an
      older model still resolvable but off the supported set.
    """
    mid = (model_id or "").strip()
    if not mid:
        return UNKNOWN
    if mid in RETIRED_MODEL_IDS:
        return "retired"
    if mid in _current_model_ids():
        return "current"
    return "stale"


def _norm_enum(value, allowed: tuple[str, ...]) -> str:
    """Return *value* if it is a recognised enum member, else ``"unknown"``.

    Honesty rule: an unrecorded or unrecognised value is surfaced as
    ``"unknown"``, never coerced to a plausible default.
    """
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in allowed else UNKNOWN


def profile_from_element(element) -> dict:
    """Project one AI-system ``ArchiMateElement`` into a typed profile dict.

    Reads ``custom_properties`` for the original profile fields and
    ``acm_properties`` for the typed-property fields (risk class, release
    state). The owner is read from the one owner record
    (``ApplicationOwner``), never from a profile key.

    Never invents a value. Governance sub-fields default to ``"unknown"`` when
    the element carries no recorded value, so a system with no governance
    recorded is visibly ungoverned-unknown rather than falsely "approved".
    """
    props = element.custom_properties or {}
    ai = props.get(AI_SYSTEM_MARKER) or {}
    if not isinstance(ai, dict):
        ai = {}

    gov = ai.get("governance") or {}
    if not isinstance(gov, dict):
        gov = {}

    model_id = (ai.get("model_id") or "").strip() or None

    def _tri(val):
        # A tri-state governance flag: True / False / "unknown" (unrecorded).
        if val is True or val is False:
            return val
        return UNKNOWN

    profile = {
        "archimate_element_id": element.id,
        "name": element.name or UNKNOWN,
        "provider": (ai.get("provider") or "").strip() or UNKNOWN,
        "model_id": model_id or UNKNOWN,
        "purpose": (ai.get("purpose") or "").strip() or UNKNOWN,
        "autonomy_level": _norm_enum(ai.get("autonomy_level"), AUTONOMY_LEVELS),
        "data_sensitivity": _norm_enum(ai.get("data_sensitivity"), DATA_SENSITIVITY),
        "governance": {
            "approval_gate": _tri(gov.get("approval_gate")),
            "human_review": _tri(gov.get("human_review")),
        },
        "model_currency": model_currency(model_id),
        # New fields: read from typed properties (acm_properties), not custom_properties.
        "risk_class": _read_risk_class(element),
        "release_state": _read_release_state(element),
        "owner": _read_owner(element),
    }
    profile["risk_flags"] = risk_flags(profile)
    return profile


def risk_flags(profile: dict) -> list[str]:
    """Deterministic architectural-risk flags for one AI-system profile.

    A flag fires only on a *recorded* condition — never on ``"unknown"``, which
    would be inventing a finding. Flags:

      * ``retired-model``            — running a denylisted, 404-ing model.
      * ``ungoverned-high-autonomy`` — high autonomy AND approval gate off.
      * ``regulated-no-human-review``— regulated data AND human review off.
    """
    flags: list[str] = []
    gov = profile.get("governance") or {}

    if profile.get("model_currency") == "retired":
        flags.append("retired-model")

    if (
        profile.get("autonomy_level") in HIGH_AUTONOMY_LEVELS
        and gov.get("approval_gate") is False
    ):
        flags.append("ungoverned-high-autonomy")

    if (
        profile.get("data_sensitivity") == "regulated"
        and gov.get("human_review") is False
    ):
        flags.append("regulated-no-human-review")

    return flags


def build_custom_properties(
    *,
    provider: str | None = None,
    model_id: str | None = None,
    purpose: str | None = None,
    autonomy_level: str | None = None,
    data_sensitivity: str | None = None,
    approval_gate=None,
    human_review=None,
) -> dict:
    """Assemble the ``custom_properties['ai_system']`` payload for storage.

    Only actually-supplied values are written; anything omitted is simply
    absent, so the read side surfaces it as ``"unknown"``. A tri-state
    governance flag is stored only when explicitly True/False.
    """
    ai: dict = {}
    if provider:
        ai["provider"] = provider.strip()
    if model_id:
        ai["model_id"] = model_id.strip()
    if purpose:
        ai["purpose"] = purpose.strip()
    if autonomy_level:
        ai["autonomy_level"] = autonomy_level.strip().lower()
    if data_sensitivity:
        ai["data_sensitivity"] = data_sensitivity.strip().lower()

    gov: dict = {}
    if approval_gate is True or approval_gate is False:
        gov["approval_gate"] = approval_gate
    if human_review is True or human_review is False:
        gov["human_review"] = human_review
    if gov:
        ai["governance"] = gov

    return {AI_SYSTEM_MARKER: ai}


# --- Typed-property readers (risk class, release state, owner) --------------


def _read_risk_class(element) -> str:
    """Read the risk class from the element's typed properties (acm_properties).

    Returns the risk tier or ``"not set"`` if none recorded.
    """
    from .ai_system_classification import PROPKEY_RISK_CLASS, RISK_TIERS

    acm = getattr(element, "acm_properties", None) or {}
    entry = acm.get(PROPKEY_RISK_CLASS)
    if entry is None:
        return NOT_SET
    if isinstance(entry, dict):
        val = entry.get("value")
        return val if val in RISK_TIERS else NOT_SET
    val = str(entry) if entry else ""
    return val if val in RISK_TIERS else NOT_SET


def _read_release_state(element) -> str:
    """Read the release state from the element's typed properties.

    Returns ``"draft"``, ``"released"``, or ``"not set"``.
    """
    from .ai_system_classification import PROPKEY_RELEASE_STATE, RELEASE_STATES

    acm = getattr(element, "acm_properties", None) or {}
    entry = acm.get(PROPKEY_RELEASE_STATE)
    if entry is None:
        return NOT_SET
    if isinstance(entry, dict):
        val = entry.get("value")
        return val if val in RELEASE_STATES else NOT_SET
    val = str(entry) if entry else ""
    return val if val in RELEASE_STATES else NOT_SET


def _read_owner(element) -> dict:
    """Read the owner from the one owner record (ApplicationOwner).

    Returns a dict with ``user_id``, ``ownership_type``, ``user_name`` or
    ``None`` if no owner is recorded. Never reads from custom_properties.
    """
    from app.models.application_owner import ApplicationOwner

    org_id = getattr(element, "organization_id", None)
    if org_id is None:
        return None

    owners = ApplicationOwner.get_owners_for_element(
        element_type="ai_system",
        element_id=element.id,
        organization_id=org_id,
    )
    if not owners:
        return None

    # Return the primary owner, or the first owner if no primary.
    primary = None
    for o in owners:
        if o.ownership_type == "primary":
            primary = o
            break
    if primary is None:
        primary = owners[0]

    return {
        "user_id": primary.user_id,
        "ownership_type": primary.ownership_type,
        "user_name": getattr(primary.user, "display_name", None)
        or f"{getattr(primary.user, 'first_name', '') or ''} {getattr(primary.user, 'last_name', '') or ''}".strip()
        or None,
    }
