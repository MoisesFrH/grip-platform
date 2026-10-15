"""
On-demand, doctor-only "Resumen con IA" for a patient's chart.

This is a DELIBERATE, narrow exception to the rule that clinical content never
reaches the LLM. The rule still holds for everything patient-facing: the
WhatsApp bot (orchestrator.py, gemini_client.py, tools.py) never reads
clinical notes. This module is a separate path that only runs when a doctor
clicks "Resumen con IA" in the CRM (POST /crm/patients/{id}/summary, gated by
require_doctor). It is never triggered automatically, never stores the
result, and never runs from a WhatsApp message.

Data minimization — what is sent to Gemini:
  - Notes: type, author, date, content.
  - Appointments: date, therapist, status, reminder answer.
  NOT sent: name, phone, WhatsApp transcripts, or anything else about the
  contact. The model does not need to know who the patient is to summarize
  what the notes say.

Guardrails live in the system instruction: summarize only what is written,
never diagnose, never invent, say so when information is scarce.

`client` is injectable so tests use a fake instead of the network (same
pattern as voice_reminders.synthesize_speech).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.appointment import Appointment
from app.models.contact import Contact
from app.models.tenant import Tenant
from app.services import clinical_notes

_CLINIC_TZ = ZoneInfo("America/Santo_Domingo")
_MAX_NOTES = 40  # keeps the prompt bounded for very long histories

SYSTEM_INSTRUCTION = (
    "Eres un asistente que prepara un resumen ejecutivo para el profesional "
    "clínico de un centro de psicología y neurodesarrollo. Recibirás las "
    "notas que los profesionales escribieron sobre un paciente y su historial "
    "de citas. Reglas estrictas:\n"
    "- Resume SOLO lo que está escrito en las notas. No inventes datos, "
    "fechas ni antecedentes.\n"
    "- No diagnostiques, no sugieras diagnósticos ni tratamientos nuevos. Si "
    "una nota recomienda algo, puedes reportarlo como recomendación del "
    "profesional que la escribió.\n"
    "- Si la información es escasa o hay contradicciones, dilo claramente.\n"
    "- Escribe en español, claro y breve (máximo unas 150 palabras), con "
    "estas secciones en texto plano, cada una con su título seguido de dos "
    "puntos: Lo esencial, Última sesión, Pendientes, Atención.\n"
    "- En 'Atención' menciona solo señales presentes en las notas o el "
    "historial de citas (por ejemplo ausencias repetidas); si no hay, "
    "escribe 'Sin alertas en las notas'.\n"
    "- Sin markdown, sin viñetas con símbolos especiales."
)

NO_NOTES_MESSAGE = (
    "Este paciente todavía no tiene notas clínicas, así que no hay nada que resumir."
)


class SummaryUnavailableError(Exception):
    """No Gemini API key configured."""


class SummaryGenerationError(Exception):
    """Gemini could not be reached or returned nothing usable."""


@dataclass
class PatientSummary:
    summary: str
    generated_at: datetime
    notes_count: int


def _fmt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CLINIC_TZ).strftime("%Y-%m-%d")


def build_summary_prompt(notes, appointments: list[Appointment]) -> str:
    """Pure function (easy to test): turns notes + appointments into the
    minimal text sent to the model. Oldest note first, so it reads as a
    timeline."""
    lines = ["NOTAS CLÍNICAS (de la más antigua a la más reciente):"]
    for n in sorted(notes, key=lambda n: n.created_at)[-_MAX_NOTES:]:
        kind = "Recomendación" if n.note_type.value == "RECOMMENDATION" else "Nota"
        lines.append(f"- [{_fmt(n.created_at)}] {kind} de {n.author_name}: {n.content}")

    lines.append("")
    lines.append("HISTORIAL DE CITAS:")
    if not appointments:
        lines.append("- (sin citas registradas)")
    for a in sorted(appointments, key=lambda a: a.scheduled_at):
        extra = ""
        if a.reminder_response is not None:
            extra = f", respuesta al recordatorio: {a.reminder_response.value}"
        lines.append(
            f"- {_fmt(a.scheduled_at)} con {a.therapist_name}: {a.status.value}{extra}"
        )

    lines.append("")
    lines.append(f"Fecha de hoy: {_fmt(datetime.now(timezone.utc))}.")
    lines.append("Prepara el resumen ejecutivo.")
    return "\n".join(lines)


def summarize_patient(
    db: Session,
    tenant: Tenant,
    contact: Contact,
    *,
    client: "genai.Client | None" = None,
) -> PatientSummary:
    notes = clinical_notes.list_notes_for_contact(db, tenant, contact)
    now = datetime.now(timezone.utc)
    if not notes:
        # Nothing to summarize: do not call the model at all.
        return PatientSummary(summary=NO_NOTES_MESSAGE, generated_at=now, notes_count=0)

    settings = get_settings()
    if client is None and not settings.gemini_api_key:
        raise SummaryUnavailableError("GEMINI_API_KEY is not configured")

    appointments = (
        db.query(Appointment)
        .filter(Appointment.tenant_id == tenant.id, Appointment.contact_id == contact.id)
        .all()
    )
    prompt = build_summary_prompt(notes, appointments)

    try:
        genai_client = client or genai.Client(api_key=settings.gemini_api_key)
        response = genai_client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
    except Exception as exc:
        raise SummaryGenerationError(f"Gemini request failed: {exc}") from exc

    if not text:
        raise SummaryGenerationError("Gemini returned an empty summary")

    return PatientSummary(summary=text, generated_at=now, notes_count=len(notes))