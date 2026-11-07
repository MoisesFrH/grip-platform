"""
Informed consent signed once per patient, from a QR code / link.

NOTE: CONSENT_PARAGRAPHS is a DRAFT written for the demo. It must be reviewed
by the clinic's lawyer before real patients sign it. Changing the wording:
edit the text and bump CONSENT_VERSION; every signed record keeps a snapshot of
the exact text its signer saw.
"""

import base64
import binascii
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.consent import ConsentRecord
from app.models.contact import Contact
from app.services import referrals

CONSENT_VERSION = "v1-demo"
CONSENT_TITLE = "Consentimiento informado para evaluación y tratamiento"
CONSENT_PARAGRAPHS = [
    ("1. Autorización",
     "Yo, madre, padre o tutor legal del paciente indicado, autorizo a ND Neurodiversa (en adelante, «el Centro») "
     "a realizar evaluaciones, intervenciones terapéuticas y el seguimiento correspondiente al menor a mi cargo."),
    ("2. Información recibida",
     "El equipo del Centro me explicó los objetivos de la atención, cómo se desarrollan las sesiones y su duración "
     "aproximada. Entiendo que los resultados dependen de varios factores y que no se pueden garantizar."),
    ("3. Participación voluntaria",
     "Mi decisión es voluntaria. Puedo hacer preguntas en cualquier momento y retirar este consentimiento cuando "
     "lo decida, sin que eso afecte la atención futura."),
    ("4. Confidencialidad",
     "La información clínica es confidencial y solo la conoce el equipo que atiende al paciente. Solo podrá "
     "compartirse sin mi autorización si existe un riesgo grave para la vida o la integridad del menor o de otras "
     "personas, o si lo exige una autoridad competente."),
    ("5. Datos personales",
     "Autorizo el tratamiento de los datos personales y de salud del paciente para su atención, la gestión de citas "
     "y la facturación, conforme a la Ley n.º 172-13 sobre Protección de Datos Personales. Puedo solicitar acceso, "
     "rectificación o supresión de esos datos al Centro."),
    ("6. Comunicación por WhatsApp",
     "Autorizo que el Centro me contacte por WhatsApp al número que registré para recordatorios de citas y "
     "coordinación. Entiendo que el asistente automático solo ofrece información general y no sustituye la orientación "
     "de un profesional, y que los recordatorios pueden incluir una descripción general del objetivo de la sesión."),
    ("7. Seguros y programas",
     "Si la atención se realiza a través de SeNaSa o del Patronato, autorizo compartir con ellos únicamente la "
     "información necesaria para la autorización y el seguimiento del servicio."),
    ("8. Compromisos",
     "Me comprometo a avisar con anticipación cualquier cambio de cita y a cumplir las políticas y tarifas del Centro "
     "que me fueron informadas."),
    ("9. Declaración",
     "He leído este documento, lo comprendo y lo firmo de forma voluntaria."),
]

RELATIONS = ["Madre", "Padre", "Tutor/a legal", "Otro familiar con autorización"]
MAX_SIGNATURE_BYTES = 400_000


class ConsentError(ValueError):
    pass


def consent_text() -> str:
    return "\n\n".join(f"{title}\n{body}" for title, body in CONSENT_PARAGRAPHS)


def get_record(db: Session, contact: Contact) -> ConsentRecord | None:
    return db.query(ConsentRecord).filter(ConsentRecord.contact_id == contact.id).one_or_none()


def create_or_renew_link(db: Session, contact: Contact, staff_name: str | None) -> ConsentRecord:
    """Creates the signing link, or issues a fresh one if the old QR was lost.
    A signed consent is never reopened: consent is given once."""
    row = get_record(db, contact)
    if row is not None and row.signed_at is not None:
        raise ConsentError("Este paciente ya firmó el consentimiento.")
    if row is None:
        row = ConsentRecord(tenant_id=contact.tenant_id, contact_id=contact.id, token="", created_by=staff_name)
        db.add(row)
    row.token = secrets.token_urlsafe(24)
    row.created_by = staff_name
    db.flush()
    return row


def get_by_token(db: Session, token: str) -> ConsentRecord | None:
    if not token or len(token) > 64:
        return None
    return db.query(ConsentRecord).filter(ConsentRecord.token == token).one_or_none()


def decode_signature(data_url: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ConsentError("La firma no es válida. Dibújala de nuevo.")
    try:
        raw = base64.b64decode(data_url[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        raise ConsentError("La firma no es válida. Dibújala de nuevo.")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > MAX_SIGNATURE_BYTES:
        raise ConsentError("La firma no es válida. Dibújala de nuevo.")
    return raw


def sign(
    db: Session,
    row: ConsentRecord,
    *,
    signer_name: str,
    relation: str,
    child_name: str,
    accepted: bool,
    signature_data_url: str,
) -> ConsentRecord:
    if row.signed_at is not None:
        raise ConsentError("Este consentimiento ya fue firmado.")
    signer_name = (signer_name or "").strip()
    child_name = (child_name or "").strip()
    if len(signer_name.split()) < 2:
        raise ConsentError("Escribe tu nombre completo.")
    if not child_name:
        raise ConsentError("Escribe el nombre del niño o niña.")
    if relation not in RELATIONS:
        raise ConsentError("Indica tu parentesco con el niño o niña.")
    if not accepted:
        raise ConsentError("Debes marcar que leíste y aceptas el consentimiento.")
    png = decode_signature(signature_data_url)

    row.signer_name = signer_name[:200]
    row.signer_relation = relation
    row.child_name = child_name[:200]
    row.signature_png = png
    row.text_version = CONSENT_VERSION
    row.text_snapshot = consent_text()
    row.signed_at = datetime.now(timezone.utc)

    contact = db.get(Contact, row.contact_id)
    if contact is not None:
        referrals.mark_consent_signed(db, contact)
    return row