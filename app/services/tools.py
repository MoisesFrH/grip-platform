"""
The complete, closed set of functions Gemini is allowed to call (architecture
doc §9). Nothing else exists as a tool: there is no update_appointment,
no send_message, no anything that mutates data or reaches outside a single
tenant's own read-only config. Every function here:

  - takes only `tenant` (plus optional filters the caller supplies) and
    returns plain structured data — it never returns prose;
  - reads exclusively from TENANT_CONFIG via app.services.tenant_config,
    so Gemini cannot be the source of any factual claim about location,
    services, therapists, availability, evaluations, booking or payment;
  - returns an explicit {"configured": False} shape when the tenant hasn't
    set that section up, so the model is grounded into saying "no tengo
    ese dato" instead of inventing one.

Adding a new capability means adding a function + schema pair here and
registering it in INTENT_TOOL_MAP — never widening what an existing
intent's tool set can do implicitly.
"""

from typing import Any, Callable

from app.models.tenant import Tenant
from app.services import tenant_config as cfg

ToolFn = Callable[..., dict[str, Any]]


def get_general_info(tenant: Tenant) -> dict:
    info = cfg.get_general_info_config(tenant)
    return {"configured": info.get("description") is not None, **info}


def get_location(tenant: Tenant) -> dict:
    return cfg.get_location_config(tenant)


def get_services(tenant: Tenant) -> dict:
    services = cfg.get_services_config(tenant)
    return {"configured": bool(services), "services": services}


def get_therapists(tenant: Tenant) -> dict:
    therapists = cfg.get_therapists_config(tenant)
    # Strip the availability field here: THERAPISTS intent answers "who do
    # you have and what do they work on", not "who is free right now" —
    # that is a separate tool/intent so the model can't casually surface a
    # semaphore value in the wrong context.
    public_fields = [
        {k: v for k, v in t.items() if k != "availability"} for t in therapists
    ]
    return {"configured": bool(therapists), "therapists": public_fields}


def get_therapist_availability(tenant: Tenant, therapist_name: str | None = None) -> dict:
    """
    Returns the availability semaphore already computed by the backend
    (architecture doc §10/§14 — the color is never decided by the LLM).

    For the MVP, before the real availability-calculation service exists,
    the semaphore is a static value maintained in TENANT_CONFIG by staff;
    this function is the single seam that later gets swapped to call the
    live calculation service without touching the Gemini integration.
    """
    therapists = cfg.get_therapists_config(tenant)
    if not therapists:
        return {"configured": False, "therapists": []}

    if therapist_name:
        matches = [t for t in therapists if t.get("name", "").lower() == therapist_name.lower()]
        if not matches:
            return {"configured": True, "found": False, "therapists": []}
        therapists = matches

    return {
        "configured": True,
        "found": True,
        "therapists": [
            {
                "name": t.get("name"),
                "availability": t.get("availability", "UNKNOWN"),
                "modality": t.get("modality"),
            }
            for t in therapists
        ],
    }


def get_booking_instructions(tenant: Tenant) -> dict:
    return cfg.get_booking_config(tenant)


def get_first_appointment_guidelines(tenant: Tenant) -> dict:
    return cfg.get_first_appointment_config(tenant)


def get_evaluations(tenant: Tenant) -> dict:
    evaluations = cfg.get_evaluations_config(tenant)
    return {"configured": bool(evaluations), "evaluations": evaluations}


def get_payment_info(tenant: Tenant) -> dict:
    return cfg.get_payment_config(tenant)


TOOL_FUNCTIONS: dict[str, ToolFn] = {
    "get_general_info": get_general_info,
    "get_location": get_location,
    "get_services": get_services,
    "get_therapists": get_therapists,
    "get_therapist_availability": get_therapist_availability,
    "get_booking_instructions": get_booking_instructions,
    "get_first_appointment_guidelines": get_first_appointment_guidelines,
    "get_evaluations": get_evaluations,
    "get_payment_info": get_payment_info,
}

# JSON-schema function declarations, in the shape the Gemini API expects
# (types.Tool(function_declarations=[...])). Kept as plain dicts so this
# module has no hard dependency on the google-genai SDK types.
TOOL_SCHEMAS: dict[str, dict] = {
    "get_general_info": {
        "name": "get_general_info",
        "description": "General information about the business (what it is, what it does).",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_location": {
        "name": "get_location",
        "description": "Physical address, how to get there, and parking/access notes.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_services": {
        "name": "get_services",
        "description": "The list of services offered, with their descriptions.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_therapists": {
        "name": "get_therapists",
        "description": "The list of therapists and their published specialties/areas of work. Does not include availability.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_therapist_availability": {
        "name": "get_therapist_availability",
        "description": (
            "The current availability semaphore (high/limited/low) for one therapist or all "
            "of them, as computed by the backend. Never guess or estimate this value yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "therapist_name": {
                    "type": "string",
                    "description": "Optional: filter to a single therapist by name.",
                }
            },
        },
    },
    "get_booking_instructions": {
        "name": "get_booking_instructions",
        "description": "How to book an appointment (the booking app/URL and step-by-step guidance).",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_first_appointment_guidelines": {
        "name": "get_first_appointment_guidelines",
        "description": "What a patient should know/bring/expect for their first appointment.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_evaluations": {
        "name": "get_evaluations",
        "description": "The list of evaluations offered and general information about them.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_payment_info": {
        "name": "get_payment_info",
        "description": "General payment methods/instructions (not for resolving a specific payment problem).",
        "parameters": {"type": "object", "properties": {}},
    },
}

# Category-A intents only (architecture doc §6): the automation categories
# B and C never reach Gemini for free generation at all, so they are not
# represented here. An intent not in this map gets an empty tool list,
# which forces the Gemini client to refuse rather than answer untooled.
INTENT_TOOL_MAP: dict[str, list[str]] = {
    "GENERAL_INFO": ["get_general_info"],
    "LOCATION": ["get_location"],
    "SERVICES": ["get_services"],
    "THERAPISTS": ["get_therapists"],
    "THERAPIST_AVAILABILITY": ["get_therapist_availability", "get_therapists"],
    "BOOKING_GUIDANCE": ["get_booking_instructions"],
    "FIRST_APPOINTMENT": ["get_first_appointment_guidelines"],
    "EVALUATIONS": ["get_evaluations"],
    "PAYMENT_INFO": ["get_payment_info"],
}


def run_tool(name: str, tenant: Tenant, **kwargs) -> dict:
    if name not in TOOL_FUNCTIONS:
        # Defensive: should be unreachable, since the model is restricted to
        # allowed_function_names at the API level (see gemini_client.py).
        return {"error": f"unknown_tool:{name}"}
    return TOOL_FUNCTIONS[name](tenant, **kwargs)
