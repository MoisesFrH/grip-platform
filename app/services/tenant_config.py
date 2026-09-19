"""
Typed, defensive access into Tenant.config (the TENANT_CONFIG JSON blob from
architecture doc §17).

Every tool function in app/services/tools.py reads through this module
instead of touching tenant.config directly, so a tenant with an incomplete
config never crashes a request — it gets an explicit "not configured"
result that the LLM is instructed to turn into "no tengo ese dato, te
conecto con el equipo" instead of inventing an answer.
"""

from typing import Any

from app.models.tenant import Tenant


def _section(tenant: Tenant, key: str, default: Any) -> Any:
    return tenant.config.get(key, default)


def get_location_config(tenant: Tenant) -> dict:
    return _section(
        tenant,
        "location",
        {"configured": False},
    )


def get_services_config(tenant: Tenant) -> list[dict]:
    return _section(tenant, "services", [])


def get_therapists_config(tenant: Tenant) -> list[dict]:
    return _section(tenant, "therapists", [])


def get_booking_config(tenant: Tenant) -> dict:
    return _section(tenant, "booking", {"configured": False})


def get_first_appointment_config(tenant: Tenant) -> dict:
    return _section(tenant, "first_appointment", {"configured": False})


def get_evaluations_config(tenant: Tenant) -> list[dict]:
    return _section(tenant, "evaluations", [])


def get_payment_config(tenant: Tenant) -> dict:
    return _section(tenant, "payment", {"configured": False})


def get_general_info_config(tenant: Tenant) -> dict:
    return _section(
        tenant,
        "general_info",
        {"business_name": tenant.name, "description": None},
    )
