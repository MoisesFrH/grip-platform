"""
Intent taxonomy from architecture doc §6 — the table there is the single
source of truth for category, priority and handoff behaviour; this module
is its code form, not a reinterpretation of it.
"""

import enum
from dataclasses import dataclass


class Intent(str, enum.Enum):
    # Category A — bot answers automatically via a grounded Gemini tool call.
    GENERAL_INFO = "GENERAL_INFO"
    LOCATION = "LOCATION"
    SERVICES = "SERVICES"
    THERAPISTS = "THERAPISTS"
    THERAPIST_AVAILABILITY = "THERAPIST_AVAILABILITY"
    BOOKING_GUIDANCE = "BOOKING_GUIDANCE"
    FIRST_APPOINTMENT = "FIRST_APPOINTMENT"
    EVALUATIONS = "EVALUATIONS"
    PAYMENT_INFO = "PAYMENT_INFO"

    # Category B — bot collects data, then creates an administrative task/handoff.
    PAYMENT_ISSUE = "PAYMENT_ISSUE"
    APPOINTMENT_CHANGE = "APPOINTMENT_CHANGE"
    APPOINTMENT_CANCEL = "APPOINTMENT_CANCEL"
    APPOINTMENT_PROBLEM = "APPOINTMENT_PROBLEM"

    # Category C — no automatic resolution attempt; always a human handoff.
    CLINICAL_QUESTION = "CLINICAL_QUESTION"
    SAFETY_RISK = "SAFETY_RISK"
    HUMAN_REQUEST = "HUMAN_REQUEST"

    # Neither A, B nor C — could not be classified even after Gemini
    # disambiguation; one clarifying retry, then handoff.
    OTHER_UNCLEAR = "OTHER_UNCLEAR"


class IntentCategory(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    UNCLEAR = "UNCLEAR"


class IntentPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class IntentMeta:
    category: IntentCategory
    priority: IntentPriority
    requires_handoff: bool


INTENT_METADATA: dict[Intent, IntentMeta] = {
    Intent.GENERAL_INFO: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.LOCATION: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.SERVICES: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.THERAPISTS: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.THERAPIST_AVAILABILITY: IntentMeta(IntentCategory.A, IntentPriority.MEDIUM, False),
    Intent.BOOKING_GUIDANCE: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.FIRST_APPOINTMENT: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.EVALUATIONS: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.PAYMENT_INFO: IntentMeta(IntentCategory.A, IntentPriority.LOW, False),
    Intent.PAYMENT_ISSUE: IntentMeta(IntentCategory.B, IntentPriority.MEDIUM, True),
    Intent.APPOINTMENT_CHANGE: IntentMeta(IntentCategory.B, IntentPriority.MEDIUM, True),
    Intent.APPOINTMENT_CANCEL: IntentMeta(IntentCategory.B, IntentPriority.MEDIUM, True),
    Intent.APPOINTMENT_PROBLEM: IntentMeta(IntentCategory.B, IntentPriority.MEDIUM, True),
    Intent.CLINICAL_QUESTION: IntentMeta(IntentCategory.C, IntentPriority.HIGH, True),
    Intent.SAFETY_RISK: IntentMeta(IntentCategory.C, IntentPriority.CRITICAL, True),
    Intent.HUMAN_REQUEST: IntentMeta(IntentCategory.C, IntentPriority.MEDIUM, True),
    Intent.OTHER_UNCLEAR: IntentMeta(IntentCategory.UNCLEAR, IntentPriority.LOW, True),
}

# The intents a deterministic keyword pass and Gemini disambiguation are
# ever allowed to land on. CLINICAL_QUESTION and SAFETY_RISK are
# deliberately excluded — those are only ever set by the safety layer
# (app/services/safety.py), never guessed from keywords or handed to
# Gemini as a menu option, since that is precisely the "no absolute
# freedom for the LLM on clinical matters" boundary from architecture §9.
CLASSIFIABLE_INTENTS: list[Intent] = [
    i for i in Intent if i not in (Intent.CLINICAL_QUESTION, Intent.SAFETY_RISK)
]
