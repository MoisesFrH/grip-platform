"""
Seeds the ND Neurodiversa tenant so the Gemini integration, tools and routing
can be exercised end to end. Safe to re-run: upserts by slug.
 
The tenant's slug stays "grip" on purpose. It is an internal identifier (the
CRM and dashboard send it on every request, the database and the existing
rows reference it), so renaming it would break running deployments for no
visible benefit. Everything a patient or staff member SEES uses the name
below.
 
Only facts the center has confirmed are configured. Anything missing
(services, therapists, booking, payment, hours, ...) is left out on purpose:
tenant_config returns "not configured" for those and the bot answers "no
tengo ese dato, te conecto con el equipo" instead of inventing it. Add each
section here as the center confirms it. Safety actions are left out for the
same reason: the generic fallback (hand off to a human, priority flagged)
applies until the center writes its own protocol.
 
This script runs on every deploy (see Procfile), so editing this file and
redeploying is how the live tenant gets updated.
 
Usage:
    .venv/bin/python -m scripts.seed_grip
"""
 
from app.core.db import SessionLocal
from app.services import agenda, payments
from app.models.tenant import Tenant
 
TENANT_NAME = "ND Neurodiversa"
TENANT_TIMEZONE = "America/Santo_Domingo"
 
ND_CONFIG = {
    "general_info": {
        "business_name": TENANT_NAME,
        "description": (
            "ND Neurodiversa es un centro de psicología y atención al neurodesarrollo "
            "ubicado en Moca, República Dominicana."
        ),
    },
    "location": {
        "configured": True,
        "address": "C/ Antonio Pasica 16, Moca, República Dominicana",
        "phone": "+1 829-828-0305",
        "email": "nd.neurodiversa@gmail.com",
        "instagram": "https://www.instagram.com/nd_neurodiversa/",
        "maps_link": "https://maps.google.com/?q=C/+Antonio+Pasica+16,+Moca,+Rep%C3%BAblica+Dominicana",
    },
    "services": [
        {
            "name": "Evaluación y diagnóstico",
            "description": (
                "Valoraciones del neurodesarrollo y pruebas diagnósticas "
                "(incluidas en programas como SeNaSa Integra)."
            ),
        },
        {
            "name": "Atención psicológica",
            "description": "Consultas y apoyo emocional para niños y familias.",
        },
        {
            "name": "Terapias",
            "description": (
                "Intervenciones en áreas como lenguaje, terapia ocupacional, "
                "conducta y aprendizaje."
            ),
        },
        {
            "name": "Acompañamiento familiar",
            "description": "Guía integral para padres y cuidadores.",
        },
    ],
    # Registration is always done by a person. The bot only describes services
    # and, when someone wants therapy, collects route/name/concern for the
    # secretary (app.services.intake).
    "intake": {"enabled": True},
    # Extra trigger phrases on top of the built-in safety keywords. This only
    # makes the bot hand off to a human sooner; it never says anything to the
    # patient on its own.
    "safety_protocol": {
        "extra_keywords": {
            "IMMEDIATE_RISK": ["se quiere ir de la casa y no sabemos donde esta"],
        },
    },
}
 
 
def seed() -> Tenant:
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == "grip").one_or_none()
        if tenant is None:
            tenant = Tenant(
                name=TENANT_NAME,
                slug="grip",
                whatsapp_number="whatsapp:+593999999999",
                timezone=TENANT_TIMEZONE,
                language="es",
            )
            db.add(tenant)
        else:
            # Existing deployments were created under the previous name.
            tenant.name = TENANT_NAME
            tenant.timezone = TENANT_TIMEZONE
 
        tenant.config = ND_CONFIG
        db.flush()
        # Therapists and the price list live in their own tables (this script
        # overwrites tenant.config on every deploy). Only missing rows are
        # added; amounts filled in by the clinic are never touched.
        agenda.seed_therapists(db, tenant)
        payments.ensure_price_items(db, tenant)
        db.commit()
        db.refresh(tenant)
        print(f"Seeded tenant: {tenant.id} ({tenant.slug}) — {tenant.name}")
        return tenant
    finally:
        db.close()
 
 
if __name__ == "__main__":
    seed()