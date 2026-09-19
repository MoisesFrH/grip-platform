"""
Seeds a realistic GRIP tenant so the Gemini integration, tools and routing
can be exercised end to end. Safe to re-run: upserts by slug.

Usage:
    .venv/bin/python -m scripts.seed_grip
"""

from app.core.db import SessionLocal
from app.models.tenant import Tenant

GRIP_CONFIG = {
    "general_info": {
        "business_name": "GRIP",
        "description": (
            "GRIP es un centro de psicología que ofrece terapia individual, "
            "de pareja y evaluaciones psicológicas, presencial y online."
        ),
    },
    "location": {
        "configured": True,
        "address": "Paseo de los Notarios 25, Santo Domingo, República Dominicana",
        "phone": "+1 809-482-0072",
        "hours": "Lunes a viernes, de 8:00 a.m. a 8:00 p.m.",
        "maps_link": "https://maps.google.com/?q=Paseo+de+los+Notarios+25,+Santo+Domingo,+Rep%C3%BAblica+Dominicana",
    },
    "services": [
        {"name": "Terapia individual adultos", "description": "Sesiones de 50 minutos, presencial u online."},
        {"name": "Terapia de pareja", "description": "Sesiones de 60 minutos, presencial u online."},
        {"name": "Evaluación psicológica", "description": "Batería de pruebas + informe, 2-3 sesiones."},
    ],
    "therapists": [
        {
            "name": "María Fernanda Ruiz",
            "specialties": ["Ansiedad", "Duelo", "Terapia cognitivo-conductual"],
            "modality": "Presencial y online",
            "availability": "🟢",
        },
        {
            "name": "Andrés Salas",
            "specialties": ["Terapia de pareja", "Comunicación familiar"],
            "modality": "Presencial",
            "availability": "🟡",
        },
        {
            "name": "Lucía Torres",
            "specialties": ["Evaluación psicológica", "TDAH en adultos"],
            "modality": "Online",
            "availability": "🔴",
        },
    ],
    "booking": {
        "configured": True,
        "booking_url": "https://booking.grip-centro.com",
        "instructions": (
            "Puedes reservar directamente desde nuestra app en "
            "booking.grip-centro.com eligiendo servicio, terapeuta y horario disponible."
        ),
    },
    "first_appointment": {
        "configured": True,
        "instructions": (
            "Llega 10 minutos antes para completar una ficha breve. No necesitas traer "
            "ningún estudio previo, solo tu documento de identidad."
        ),
    },
    "evaluations": [
        {"name": "Evaluación de ansiedad y depresión", "duration": "2 sesiones"},
        {"name": "Evaluación neuropsicológica breve", "duration": "3 sesiones"},
    ],
    "payment": {
        "configured": True,
        "methods": ["Tarjeta de crédito/débito", "Transferencia bancaria", "Efectivo en recepción"],
        "notes": "El pago se realiza al finalizar cada sesión o por adelantado al reservar en línea.",
    },
    # Only SUICIDE_OR_SELF_HARM configured on purpose, so the seed exercises
    # both paths: a tenant-provided action, and the generic fallback for
    # every other category GRIP hasn't reviewed yet (architecture doc §16).
    "safety_protocol": {
        "extra_keywords": {
            "IMMEDIATE_RISK": ["se quiere ir de la casa y no sabemos donde esta"],
        },
        "actions": {
            "SUICIDE_OR_SELF_HARM": {
                "patient_message": (
                    "Gracias por confiarme esto. Voy a conectarte ahora mismo con el equipo "
                    "clínico de guardia de GRIP; alguien te va a escribir en los próximos minutos."
                ),
                "handoff_note": "Riesgo de autolesión/suicidio detectado por palabras clave. Escalar al psicólogo de guardia de inmediato.",
                "notify": ["on_call_psychologist", "clinical_director"],
            }
        },
    },
}


def seed() -> Tenant:
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == "grip").one_or_none()
        if tenant is None:
            tenant = Tenant(
                name="GRIP",
                slug="grip",
                whatsapp_number="whatsapp:+593999999999",
                timezone="America/Guayaquil",
                language="es",
            )
            db.add(tenant)

        tenant.config = GRIP_CONFIG
        db.commit()
        db.refresh(tenant)
        print(f"Seeded tenant: {tenant.id} ({tenant.slug})")
        return tenant
    finally:
        db.close()


if __name__ == "__main__":
    seed()