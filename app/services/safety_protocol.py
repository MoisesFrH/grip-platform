"""
Resolves WHAT TO DO once safety.check_message has flagged a category —
never WHAT TO SAY clinically. GRIP (or any tenant) is the only source of
actual protocol content; this module's fallback is intentionally inert:
escalate to a human, say nothing clinical, invent nothing.

TENANT_CONFIG["safety_protocol"]["actions"] shape, per category:
    {
      "SUICIDE_OR_SELF_HARM": {
        "patient_message": "<tenant-approved text>",
        "handoff_note": "<what the agent should be told>",
        "notify": ["on_call_psychologist"]   # tenant-defined routing hint
      },
      ...
    }
A tenant is never required to configure this before launch — the fallback
keeps the system safe (always escalates, never diagnoses, never invents a
message) while GRIP fills in their real protocol.
"""

from dataclasses import dataclass

from app.models.tenant import Tenant
from app.services.safety import SafetyCategory, SafetyPriority

GENERIC_PATIENT_MESSAGE = (
    "Gracias por contarme esto. Voy a pasar tu mensaje directamente a una "
    "persona de nuestro equipo para que pueda ayudarte lo antes posible."
)


@dataclass
class ProtocolAction:
    configured: bool
    category: SafetyCategory
    priority: SafetyPriority
    patient_message: str
    handoff_note: str
    notify: list[str]


def resolve_protocol_action(tenant: Tenant, category: SafetyCategory, priority: SafetyPriority) -> ProtocolAction:
    protocol_config = tenant.config.get("safety_protocol", {})
    actions = protocol_config.get("actions", {})
    tenant_action = actions.get(category.value)

    if tenant_action:
        return ProtocolAction(
            configured=True,
            category=category,
            priority=priority,
            patient_message=tenant_action.get("patient_message", GENERIC_PATIENT_MESSAGE),
            handoff_note=tenant_action.get(
                "handoff_note", f"Safety category {category.value} detected; no handoff note configured."
            ),
            notify=tenant_action.get("notify", []),
        )

    # Fallback: safe by construction. No clinical content, no invented
    # protocol — just "a human needs to see this now", flagged so GRIP
    # knows this category still needs configuring.
    return ProtocolAction(
        configured=False,
        category=category,
        priority=priority,
        patient_message=GENERIC_PATIENT_MESSAGE,
        handoff_note=(
            f"Safety category {category.value} detected (priority {priority.value}). "
            "No tenant-specific protocol is configured for this category yet — "
            "reviewing agent should treat this as an unconfigured safety escalation."
        ),
        notify=[],
    )
