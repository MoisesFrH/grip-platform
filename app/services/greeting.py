"""
Welcome message for a bare greeting ("hola", "buenas tardes", ...).

Only a message that is *entirely* a greeting counts: "hola quiero una cita"
is not a bare greeting and goes through normal classification. Deterministic,
no LLM, and it never replaces the safety check (a message with any other
word is not a bare greeting, so safety still sees everything that matters).
"""

import re

from app.models.tenant import Tenant
from app.services.text_utils import normalize

_STARTERS = {"hola", "ola", "buenas", "buenos", "buen", "hey", "saludos", "hello", "hi", "alo"}
_FILLERS = {"dias", "dia", "tardes", "noches", "que", "tal", "como", "estas", "estan",
            "esta", "todos", "a", "de", "tarde", "noche", "equipo", "nd", "neurodiversa"}


def is_greeting(text: str) -> bool:
    if not text or not text.strip():
        return False
    cleaned = re.sub(r"[^a-z\s]", " ", normalize(text))
    cleaned = re.sub(r"(.)\1{2,}", r"\1", cleaned)  # "holaaa" -> "hola"
    tokens = cleaned.split()
    if not tokens or len(tokens) > 6:
        return False
    return tokens[0] in _STARTERS and all(t in _STARTERS or t in _FILLERS for t in tokens)


def first_name(full_name: str | None) -> str | None:
    """"ana maría pérez" -> "Ana". None for an empty name."""
    parts = (full_name or "").split()
    if not parts:
        return None
    first = parts[0].strip(" ,.;:")
    return first.capitalize() if first.islower() or first.isupper() else first


def welcome_message(tenant: Tenant, *, name: str | None = None, next_appointment: str | None = None) -> str:
    """Welcome for a bare greeting. With a name known from the CRM the
    greeting is personal and, if the contact has an upcoming appointment,
    reminds them of it (`next_appointment`, e.g. "mañana a las 3:00 p. m. con
    Esther Jimenez"). Without a name it is the generic first-contact welcome."""
    who = first_name(name)
    if who is None:
        return (
            f"¡Hola! Te damos la bienvenida a {tenant.name} 💙 Soy el asistente virtual.\n\n"
            "¿En qué te puedo ayudar? Puedo contarte sobre nuestros servicios, dónde "
            "estamos o ayudarte a solicitar una cita."
        )
    text = f"¡Hola, {who}! Qué gusto saludarte de nuevo 💙 Soy el asistente virtual de {tenant.name}."
    if next_appointment:
        text += f"\n\nTe recuerdo que tu próxima cita es {next_appointment}."
    text += "\n\n¿En qué te puedo ayudar hoy?"
    return text