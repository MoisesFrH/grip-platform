"""
Deterministic first pass of the hybrid classifier (architecture doc §6).
Cheap, fast, no API call — this alone resolves the common case. Only when
it comes back empty or ambiguous does the classifier reach for Gemini
(app/services/intent_classifier.py).

Deliberately excludes CLINICAL_QUESTION and SAFETY_RISK: those categories
are owned entirely by app/services/safety.py, which runs first and wins
outright when it fires (see intents.CLASSIFIABLE_INTENTS).

Non-exhaustive by design — a tenant with different vocabulary (a
restaurant, a hotel) gets its own keyword set later via TENANT_CONFIG,
the same extensibility pattern used in safety.py.
"""

from dataclasses import dataclass

from app.services.intents import Intent
from app.services.text_utils import compile_keyword_pattern, normalize

DEFAULT_KEYWORDS: dict[Intent, list[str]] = {
    Intent.GENERAL_INFO: [
        "informacion general", "que es grip", "quienes son", "que hacen ustedes",
        "cuentenme sobre", "a que se dedican",
    ],
    Intent.LOCATION: [
        "donde quedan", "donde estan ubicados", "direccion", "como llego",
        "como llegar", "ubicacion", "donde es el consultorio",
    ],
    Intent.SERVICES: [
        "que servicios", "servicios ofrecen", "que servicios tienen", "que ofrecen",
    ],
    Intent.THERAPISTS: [
        "que terapeutas", "quienes son los terapeutas", "que psicologos tienen",
        "lista de terapeutas", "con quien puedo atenderme",
    ],
    Intent.THERAPIST_AVAILABILITY: [
        "tiene disponibilidad", "tiene cupo", "esta disponible", "cuando tiene espacio",
        "que tan pronto me puede atender", "disponibilidad de",
    ],
    Intent.BOOKING_GUIDANCE: [
        "como reservo", "como agendo", "como saco una cita", "como uso la app",
        "como programo una cita", "como hago para reservar",
    ],
    Intent.FIRST_APPOINTMENT: [
        "primera cita", "primera vez que voy", "que debo llevar", "que necesito para mi primera",
        "es mi primera consulta",
    ],
    Intent.EVALUATIONS: [
        "evaluacion psicologica", "que evaluaciones", "test psicologico", "prueba psicologica",
    ],
    Intent.PAYMENT_INFO: [
        "como pago", "metodos de pago", "formas de pago", "cuanto cuesta", "cual es el precio",
        "cuanto vale la sesion",
    ],
    Intent.PAYMENT_ISSUE: [
        "problema con el pago", "me cobraron mal", "no me llego el pago", "reembolso",
        "me cobraron dos veces", "el pago no se proceso",
    ],
    Intent.APPOINTMENT_CHANGE: [
        "cambiar mi cita", "reprogramar", "mover mi cita", "otro horario para mi cita",
        "cambiar la fecha de mi cita", "reagendar",
    ],
    Intent.APPOINTMENT_CANCEL: [
        "cancelar mi cita", "anular la cita", "ya no puedo ir a mi cita", "cancelar la consulta",
    ],
    Intent.APPOINTMENT_PROBLEM: [
        "problema con mi reserva", "no me aparece la cita", "error al reservar",
        "no me llego la confirmacion de la cita",
    ],
    Intent.HUMAN_REQUEST: [
        "quiero hablar con una persona", "quiero hablar con alguien", "necesito un humano",
        "atencion humana", "hablar con alguien del equipo", "pasame con una persona",
    ],
}


@dataclass
class DeterministicMatch:
    intent: Intent
    matched_terms: list[str]


def deterministic_candidates(text: str) -> list[DeterministicMatch]:
    """Returns every intent whose keywords matched, in no particular
    order. Zero results = genuinely unclear; more than one = ambiguous
    (e.g. a message that mentions both a price and a cancellation).
    Either case is handed to Gemini for disambiguation by the caller."""
    if not text or not text.strip():
        return []

    normalized = normalize(text)
    matches: list[DeterministicMatch] = []

    for intent, keywords in DEFAULT_KEYWORDS.items():
        pattern = compile_keyword_pattern(keywords)
        found = pattern.findall(normalized)
        if found:
            matches.append(DeterministicMatch(intent=intent, matched_terms=sorted(set(found))))

    return matches
