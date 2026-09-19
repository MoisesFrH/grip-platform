"""
The hybrid classifier from architecture doc §6/§8:

    WhatsApp message -> Contact identification -> Conversation state
    -> Safety/sensitivity check -> Intent classification
    -> Automation classification (A/B/C) -> Action

This module is the "Safety/sensitivity check" + "Intent classification"
steps, fused into one entry point (`classify_intent`) because the safety
result has to be able to override the intent outright before anything
else runs — there is no code path where a deterministic or Gemini intent
guess can outrank a safety signal.

Order of authority, strictly:
  1. app.services.safety.check_message — if it fires, it wins. Full stop.
  2. app.services.intent_rules.deterministic_candidates — cheap, free,
     resolves the common single-keyword case with no API call.
  3. Gemini disambiguation (GeminiClient.classify_intent) — only reached
     when the deterministic pass is empty or ambiguous, and even then
     bounded to the real candidates (or the full non-clinical menu on a
     true zero-match), never a free choice.
  4. OTHER_UNCLEAR — the fail-closed default when Gemini is unavailable,
     errors, or itself can't decide.
"""

from dataclasses import dataclass, field

from app.models.tenant import Tenant
from app.services.intent_rules import DeterministicMatch, deterministic_candidates
from app.services.intents import (
    CLASSIFIABLE_INTENTS,
    INTENT_METADATA,
    Intent,
    IntentCategory,
    IntentPriority,
)
from app.services.safety import SafetyCategory, SafetyPriority, check_message

_VALID_INTENT_VALUES = {i.value for i in Intent}

# The menu Gemini sees on a true zero-match message: every classifiable
# intent except OTHER_UNCLEAR itself (that's always appended separately
# by GeminiClient.classify_intent as the escape valve).
_FULL_MENU = [i.value for i in CLASSIFIABLE_INTENTS if i != Intent.OTHER_UNCLEAR]


@dataclass
class IntentClassificationResult:
    primary_intent: Intent
    category: IntentCategory
    priority: IntentPriority
    requires_handoff: bool
    source: str  # "safety" | "deterministic" | "gemini" | "fallback_no_client" | "fallback_error"
    matched_keywords: list[str] = field(default_factory=list)
    secondary_intent: Intent | None = None
    safety_category: SafetyCategory | None = None


def _result_for(
    intent: Intent,
    *,
    source: str,
    matched_keywords: list[str],
    secondary_intent: Intent | None = None,
    safety_category: SafetyCategory | None = None,
) -> IntentClassificationResult:
    meta = INTENT_METADATA[intent]
    return IntentClassificationResult(
        primary_intent=intent,
        category=meta.category,
        priority=meta.priority,
        requires_handoff=meta.requires_handoff,
        source=source,
        matched_keywords=matched_keywords,
        secondary_intent=secondary_intent,
        safety_category=safety_category,
    )


def classify_intent(tenant: Tenant, text: str, gemini_client=None) -> IntentClassificationResult:
    # --- 1. Safety always goes first and, if triggered, always wins ---
    safety_result = check_message(tenant, text)
    det_matches: list[DeterministicMatch] = deterministic_candidates(text)

    if safety_result.risk_detected:
        primary = (
            Intent.SAFETY_RISK
            if safety_result.priority == SafetyPriority.CRITICAL
            else Intent.CLINICAL_QUESTION
        )
        # Brief §15's exact scenario: a clinical/safety signal plus an
        # ordinary administrative request in the same message. The
        # administrative part is preserved as a secondary intent so the
        # human agent sees the full picture, but it never changes the
        # primary routing or the handoff decision.
        secondary = det_matches[0].intent if det_matches else None
        return _result_for(
            primary,
            source="safety",
            matched_keywords=safety_result.matched_terms,
            secondary_intent=secondary,
            safety_category=safety_result.category,
        )

    # --- 2. Exactly one clean deterministic match: done, no API call ---
    if len(det_matches) == 1:
        match = det_matches[0]
        return _result_for(match.intent, source="deterministic", matched_keywords=match.matched_terms)

    # --- 3. Zero or multiple matches: bounded Gemini disambiguation ---
    all_matched_terms = sorted({term for m in det_matches for term in m.matched_terms})
    candidate_names = [m.intent.value for m in det_matches] if det_matches else _FULL_MENU

    if gemini_client is None:
        return _result_for(Intent.OTHER_UNCLEAR, source="fallback_no_client", matched_keywords=all_matched_terms)

    try:
        chosen_name = gemini_client.classify_intent(candidate_intents=candidate_names, user_message=text)
    except Exception:
        # Fail closed: an unclear intent forces a clarifying retry / human
        # handoff (architecture §6), never a silent guess.
        return _result_for(Intent.OTHER_UNCLEAR, source="fallback_error", matched_keywords=all_matched_terms)

    chosen = Intent(chosen_name) if chosen_name in _VALID_INTENT_VALUES else Intent.OTHER_UNCLEAR
    return _result_for(chosen, source="gemini", matched_keywords=all_matched_terms)
