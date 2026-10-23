"""
One-sentence "what we will work on" line for appointment reminders, written
from the patient's CRM clinical notes.

This is a deliberate, narrow exception to "the bot never reads clinical
notes": the text is generated ahead of time by the reminder job / CRM button,
never by the WhatsApp bot while chatting, and it is constrained hard:

  * only the most recent few notes, trimmed, are sent to the model;
  * no name, phone or any identifier is sent with them;
  * the prompt forbids diagnoses, medication, names and negative incidents —
    only the therapeutic goal, in warm general terms;
  * the output is validated (one sentence, short, no digits) and the whole
    thing FAILS CLOSED: any problem returns None and the reminder goes out
    with a generic line instead. A reminder is never blocked by this module.

Set tenant.config["reminders"]["include_summary"] = false to switch it off.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.clinical_note import ClinicalNote, ClinicalNoteType
from app.models.contact import Contact
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

GENERIC_SUMMARY = "los objetivos que acordamos junto a tu terapeuta"

# Which notes feed the summary. RECOMMENDATION notes are the most natural to
# share; remove NOTE from this tuple to use recommendations only.
SUMMARY_NOTE_TYPES = (ClinicalNoteType.NOTE, ClinicalNoteType.RECOMMENDATION)
MAX_NOTES = 5
MAX_NOTE_CHARS = 600
MAX_WORDS = 35

_PROMPT = """Eres asistente de un centro de terapia infantil y neurodiversidad en República Dominicana.
A partir de las notas internas de abajo, escribe UNA sola frase corta en español que complete esta oración:
"En esta sesión vamos a trabajar en ..."
Ejemplo de frase: "la comunicación, la atención y el juego compartido". No repitas el inicio de la oración.

Reglas estrictas:
- Máximo 30 palabras, una sola oración, sin saltos de línea.
- Solo el objetivo terapéutico (por ejemplo: comunicación, atención, regulación emocional, motricidad, lectoescritura).
- PROHIBIDO: diagnósticos, nombres de personas, medicamentos, resultados de pruebas, conflictos familiares,
  incidentes, conductas negativas, cifras o fechas.
- Si las notas no permiten identificar un objetivo claro, responde exactamente: NINGUNO

Notas internas:
{notes}
"""


def _clean(text: str | None) -> str | None:
    """Validates the model output. Returns the sentence, or None if it is
    unusable (fail closed)."""
    if not text:
        return None
    sentence = " ".join(text.strip().strip('"“”').split())
    if not sentence or sentence.upper().startswith("NINGUNO"):
        return None
    if len(sentence.split()) > MAX_WORDS or re.search(r"\d", sentence):
        return None
    if sentence.count(".") > 1 or "\n" in text.strip():
        return None
    sentence = sentence.rstrip(".")
    for lead in ("en esta sesión vamos a trabajar en ", "vamos a trabajar en ", "trabajaremos en "):
        if sentence.lower().startswith(lead):
            sentence = sentence[len(lead):]
    # Lower-case the first letter so it fits after "vamos a trabajar en ".
    return sentence[0].lower() + sentence[1:]


def _gemini_generate(prompt: str) -> str | None:
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 120},
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.gemini_api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError):
        logger.warning("Reminder summary generation failed", exc_info=True)
        return None


def summarize_notes(notes: list[str], *, generate: Callable[[str], str | None] | None = None) -> str | None:
    """`notes`: plain note texts, newest first. Returns the sentence or None."""
    texts = [n.strip()[:MAX_NOTE_CHARS] for n in notes if n and n.strip()][:MAX_NOTES]
    if not texts:
        return None
    prompt = _PROMPT.format(notes="\n".join(f"- {t}" for t in texts))
    try:
        return _clean((generate or _gemini_generate)(prompt))
    except Exception:
        logger.warning("Reminder summary generation failed", exc_info=True)
        return None


def summary_enabled(tenant: Tenant) -> bool:
    return bool((tenant.config or {}).get("reminders", {}).get("include_summary", True))


def summary_for_contact(
    db: Session, tenant: Tenant, contact: Contact, *, generate: Callable[[str], str | None] | None = None
) -> str | None:
    if not summary_enabled(tenant):
        return None
    rows = (
        db.query(ClinicalNote)
        .filter(
            ClinicalNote.tenant_id == tenant.id,
            ClinicalNote.contact_id == contact.id,
            ClinicalNote.note_type.in_(SUMMARY_NOTE_TYPES),
        )
        .order_by(ClinicalNote.created_at.desc())
        .limit(MAX_NOTES)
        .all()
    )
    return summarize_notes([r.content for r in rows], generate=generate)