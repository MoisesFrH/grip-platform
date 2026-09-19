"""
Safety / risk detection layer (architecture doc §16).

This runs BEFORE intent classification and BEFORE any call to Gemini
(architecture doc §4, step 8). It is deliberately deterministic, not
LLM-based: a missed crisis signal is far more costly than an
over-triggered one, and a keyword/pattern check is fast, free, fully
auditable, and does not depend on an external API being up.

What this module does NOT do, on purpose:
  - It does not diagnose, interpret symptoms, or produce any clinical
    judgment about the message. It only decides "does this need a human,
    and how urgently" — a routing decision, not a clinical one.
  - It does not contain or fabricate a clinical protocol. GRIP (or any
    tenant) supplies its own protocol text/action per category in
    TENANT_CONFIG["safety_protocol"]; where a tenant hasn't configured
    one, the fallback is a generic, safe "escalate to a human now" action
    with no invented clinical content (see resolve_protocol_action).
  - Categories/keywords here are a starting, tenant-extensible taxonomy,
    not a claim of clinical completeness. A tenant adds its own terms via
    TENANT_CONFIG["safety_protocol"]["extra_keywords"] without a
    deployment.

Any message matched here forces:
  - no automatic (category A) resolution attempt,
  - no free-form Gemini generation,
  - an immediate handoff at the category's priority.
"""

import enum
from dataclasses import dataclass, field

from app.models.tenant import Tenant
from app.services.text_utils import compile_keyword_pattern, normalize


class SafetyCategory(str, enum.Enum):
    SUICIDE_OR_SELF_HARM = "SUICIDE_OR_SELF_HARM"
    IMMEDIATE_RISK = "IMMEDIATE_RISK"
    ABUSE = "ABUSE"
    SYMPTOMS = "SYMPTOMS"
    DIAGNOSIS_REQUEST = "DIAGNOSIS_REQUEST"
    TREATMENT_REQUEST = "TREATMENT_REQUEST"
    MEDICATION = "MEDICATION"
    CLINICAL_OTHER = "CLINICAL_OTHER"


class SafetyPriority(str, enum.Enum):
    CRITICAL = "CRITICAL"  # architecture doc's SAFETY_RISK intent
    HIGH = "HIGH"  # architecture doc's CLINICAL_QUESTION intent


CATEGORY_PRIORITY: dict[SafetyCategory, SafetyPriority] = {
    SafetyCategory.SUICIDE_OR_SELF_HARM: SafetyPriority.CRITICAL,
    SafetyCategory.IMMEDIATE_RISK: SafetyPriority.CRITICAL,
    SafetyCategory.ABUSE: SafetyPriority.CRITICAL,
    SafetyCategory.SYMPTOMS: SafetyPriority.HIGH,
    SafetyCategory.DIAGNOSIS_REQUEST: SafetyPriority.HIGH,
    SafetyCategory.TREATMENT_REQUEST: SafetyPriority.HIGH,
    SafetyCategory.MEDICATION: SafetyPriority.HIGH,
    SafetyCategory.CLINICAL_OTHER: SafetyPriority.HIGH,
}

# Evaluated in this order so a message touching more than one category
# (common: "tengo un dolor y ademas quiero hacerme daño") resolves to the
# single most severe one, per architecture doc's "no votación / no
# promedio de confianza" rule.
CATEGORY_EVAL_ORDER: list[SafetyCategory] = [
    SafetyCategory.SUICIDE_OR_SELF_HARM,
    SafetyCategory.IMMEDIATE_RISK,
    SafetyCategory.ABUSE,
    SafetyCategory.DIAGNOSIS_REQUEST,
    SafetyCategory.TREATMENT_REQUEST,
    SafetyCategory.MEDICATION,
    SafetyCategory.SYMPTOMS,
    SafetyCategory.CLINICAL_OTHER,
]

# Non-exhaustive, pattern-level phrases — general expressions of intent or
# topic, not a catalog of methods/means. Spanish first (GRIP's language),
# with light English coverage since a tenant's `language` can vary.
DEFAULT_KEYWORDS: dict[SafetyCategory, list[str]] = {
    SafetyCategory.SUICIDE_OR_SELF_HARM: [
        "suicid", "quitarme la vida", "acabar con mi vida", "no quiero vivir",
        "no quiero seguir viviendo", "matarme", "hacerme dano", "lastimarme",
        "cortarme", "autolesion", "kill myself", "end my life", "hurt myself",
        "self harm", "self-harm",
    ],
    SafetyCategory.IMMEDIATE_RISK: [
        "crisis", "emergencia", "ataque de panico", "no puedo mas",
        "no doy mas", "estoy en peligro", "me quiere hacer dano",
        "tiene un arma", "sobredosis", "overdose", "in danger", "emergency",
    ],
    SafetyCategory.ABUSE: [
        "me pega", "me golpea", "me maltrata", "abuso sexual", "me esta abusando",
        "violencia", "violacion", "me esta lastimando", "abuse", "being abused",
    ],
    SafetyCategory.SYMPTOMS: [
        "tengo ansiedad", "tengo depresion", "me siento deprimido",
        "no puedo dormir", "insomnio", "ataques de panico", "sintomas de",
        "me siento muy mal emocionalmente",
    ],
    SafetyCategory.DIAGNOSIS_REQUEST: [
        "que tengo", "que me pasa", "es esto normal", "es grave lo que tengo",
        "tengo algun trastorno", "tengo diagnostico de", "podria tener",
    ],
    SafetyCategory.TREATMENT_REQUEST: [
        "que tratamiento", "como me trato", "que hago para curarme",
        "que terapia me recomiendas", "cual es el tratamiento",
    ],
    SafetyCategory.MEDICATION: [
        "medicamento", "pastillas", "dosis", "medicacion", "antidepresivo",
        "ansiolitico", "que pastilla", "puedo tomar",
    ],
    SafetyCategory.CLINICAL_OTHER: [
        "terapeuta recomendado para mi caso", "que terapeuta me conviene",
        "cual terapeuta es mejor para mi problema",
    ],
}


@dataclass
class SafetyCheckResult:
    risk_detected: bool
    category: SafetyCategory | None = None
    priority: SafetyPriority | None = None
    matched_terms: list[str] = field(default_factory=list)

    @property
    def requires_immediate_human(self) -> bool:
        return self.priority == SafetyPriority.CRITICAL


def _tenant_keyword_map(tenant: Tenant) -> dict[SafetyCategory, list[str]]:
    """Merges the default taxonomy with a tenant's own additions from
    TENANT_CONFIG["safety_protocol"]["extra_keywords"] (architecture doc
    §17). A tenant can only ADD keywords here, never remove a default
    category's coverage — safety keyword removal, if ever needed, is a
    deliberate config-review action outside this code path."""
    protocol_config = tenant.config.get("safety_protocol", {})
    extra = protocol_config.get("extra_keywords", {})

    merged: dict[SafetyCategory, list[str]] = {}
    for category, defaults in DEFAULT_KEYWORDS.items():
        tenant_extra = extra.get(category.value, [])
        merged[category] = [*defaults, *tenant_extra]
    return merged


def check_message(tenant: Tenant, text: str) -> SafetyCheckResult:
    """
    The single entry point the message-router calls, before intent
    classification, on every inbound patient message.
    """
    if not text or not text.strip():
        return SafetyCheckResult(risk_detected=False)

    normalized = normalize(text)
    keyword_map = _tenant_keyword_map(tenant)

    for category in CATEGORY_EVAL_ORDER:
        pattern = compile_keyword_pattern(keyword_map[category])
        matches = pattern.findall(normalized)
        if matches:
            return SafetyCheckResult(
                risk_detected=True,
                category=category,
                priority=CATEGORY_PRIORITY[category],
                matched_terms=sorted(set(matches)),
            )

    return SafetyCheckResult(risk_detected=False)
