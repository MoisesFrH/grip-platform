
"""
Intake of a NEW patient who wants therapy, deterministic on purpose.
 
The bot never registers or schedules anyone: a person (the secretary) does
that. All the bot does is collect three things and hand the case to the
secretary's inbox with them already written down:
 
    1. how the patient will register: SeNaSa, Patronato de Sordomudos, or
       directly (on their own),
    2. the patient's full name,
    3. the therapy or concern the family describes.
 
No LLM is involved in this module. Gemini never sees the name or the
concern; answers are parsed with fixed rules. While an intake is open the
orchestrator only lets two things interrupt it: the safety layer and an
explicit request for a human (see `should_interrupt`).
 
State lives in `Conversation.intake_data` (JSON) so it survives between
WhatsApp messages and is shown to the secretary in the CRM case card.
"""
 
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
 
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.services.intent_rules import deterministic_candidates
from app.services.intents import Intent
from app.services.safety import check_message
from app.services.text_utils import compile_keyword_pattern, normalize
 
STEP_PAYER = "ASK_PAYER"
STEP_NAME = "ASK_NAME"
STEP_CONCERN = "ASK_CONCERN"
STEP_DONE = "DONE"
STEP_ABANDONED = "ABANDONED"
ACTIVE_STEPS = (STEP_PAYER, STEP_NAME, STEP_CONCERN)
 
# An intake nobody answered for this long is dropped: a reply tomorrow
# ("Juan Pérez") must not be read as the answer to yesterday's question.
EXPIRY = timedelta(hours=12)
 
MAX_NAME_LEN = 80
MAX_CONCERN_LEN = 300
 
DEFAULT_PAYERS = [
    {
        "code": "SENASA",
        "label": "SeNaSa (Servicio Nacional de Salud)",
        "keywords": ["senasa", "seguro nacional de salud", "servicio nacional de salud"],
        "note": (
            "Si eres afiliado a SeNaSa, tanto en el Régimen Contributivo como Subsidiado "
            "(No Contributivo), puedes acercarte a la sucursal de SeNaSa más cercana para "
            "conocer si aplicas al programa y recibir toda la información correspondiente."
        ),
    },
    {
        "code": "PATRONATO",
        "label": "Patronato de Sordomudos",
        "keywords": ["patronato", "sordomudo", "sordomudos"],
    },
    {
        "code": "DIRECT",
        "label": "Directamente (por tu propia cuenta)",
        "keywords": [
            "por mi cuenta", "por cuenta propia", "mi propia cuenta", "directamente",
            "directo", "particular", "privado", "sin seguro", "pagando yo", "yo pago",
        ],
    },
]
 
NAME_PREFIXES = ("me llamo", "mi nombre es", "el nombre es", "se llama", "es", "soy", "mi hijo se llama", "mi hija se llama")
 
 
@dataclass
class IntakeTurn:
    reply: str
    complete: bool = False
    reason: str | None = None  # handoff reason, set only when complete
 
 
def get_config(tenant: Tenant) -> dict | None:
    cfg = (tenant.config or {}).get("intake")
    if not cfg or not cfg.get("enabled"):
        return None
    return cfg
 
 
def _payers(tenant: Tenant) -> list[dict]:
    cfg = get_config(tenant) or {}
    return cfg.get("payers") or DEFAULT_PAYERS
 
 
def _now() -> datetime:
    return datetime.now(timezone.utc)
 
 
def _save(conversation: Conversation, data: dict) -> None:
    data["updated_at"] = _now().isoformat()
    # Reassign (not mutate): SQLAlchemy does not track in-place JSON changes.
    conversation.intake_data = dict(data)
 
 
def is_active(conversation: Conversation) -> bool:
    data = conversation.intake_data
    if not data or data.get("step") not in ACTIVE_STEPS:
        return False
    try:
        updated = datetime.fromisoformat(data["updated_at"])
    except (KeyError, ValueError):
        return False
    return _now() - updated <= EXPIRY
 
 
def abandon(conversation: Conversation) -> None:
    if conversation.intake_data and conversation.intake_data.get("step") in ACTIVE_STEPS:
        _save(conversation, {**conversation.intake_data, "step": STEP_ABANDONED})
 
 
def should_interrupt(tenant: Tenant, body: str, step: str) -> bool:
    """True when the message must go through the normal pipeline instead of
    being read as an intake answer: a safety signal, a request for a human,
    or (for the short-answer steps) a question."""
    if check_message(tenant, body).risk_detected:
        return True
    if any(m.intent == Intent.HUMAN_REQUEST for m in deterministic_candidates(body)):
        return True
    if step in (STEP_PAYER, STEP_NAME) and "?" in body:
        return True
    return False
 
 
def _payer_question(tenant: Tenant, prefix: str = "") -> str:
    lines = [f"{i}. {p['label']}" for i, p in enumerate(_payers(tenant), start=1)]
    return (
        prefix
        + "¿Cómo te gustaría registrarte? Responde con el número o con tu opción:\n"
        + "\n".join(lines)
    )
 
 
def parse_payer(tenant: Tenant, text: str) -> dict | None:
    payers = _payers(tenant)
    normalized = normalize(text).strip()
    digits = normalized.replace("opcion", "").replace("numero", "").strip(" .)")
    if digits.isdigit() and 1 <= int(digits) <= len(payers):
        return payers[int(digits) - 1]
    found = [p for p in payers if compile_keyword_pattern(p["keywords"]).search(normalized)]
    return found[0] if len(found) == 1 else None
 
 
def clean_name(text: str) -> str | None:
    name = " ".join(text.split()).strip(" .,;:")
    low = normalize(name)
    for prefix in NAME_PREFIXES:
        if low.startswith(prefix + " "):
            name = name[len(prefix):].strip(" .,;:")
            break
    if len(name) < 2 or len(name) > MAX_NAME_LEN or not any(c.isalpha() for c in name):
        return None
    return name
 
 
def start(tenant: Tenant, conversation: Conversation, first_message: str) -> IntakeTurn:
    data = {"step": STEP_PAYER, "payer": None, "payer_label": None, "patient_name": None,
            "concern": None, "payer_attempts": 0}
    intro = (
        "¡Con gusto te ayudo! El registro lo hace una persona de nuestro equipo; "
        "yo solo necesito unos datos para dejarle tu solicitud lista.\n\n"
    )
    # The first message may already say how they will register.
    payer = parse_payer(tenant, first_message) if not first_message.strip().isdigit() else None
    if payer is not None:
        return _after_payer(tenant, conversation, data, payer, intro)
    _save(conversation, data)
    return IntakeTurn(reply=_payer_question(tenant, intro))
 
 
def _after_payer(tenant, conversation, data, payer, prefix="") -> IntakeTurn:
    data.update(step=STEP_NAME, payer=payer["code"], payer_label=payer["label"])
    _save(conversation, data)
    note = f"{payer['note']}\n\n" if payer.get("note") else ""
    return IntakeTurn(reply=f"{prefix}{note}¿Cuál es el nombre completo del paciente?")
 
 
def advance(tenant: Tenant, conversation: Conversation, body: str) -> IntakeTurn:
    data = dict(conversation.intake_data)
    step = data["step"]
 
    if step == STEP_PAYER:
        payer = parse_payer(tenant, body)
        if payer is not None:
            return _after_payer(tenant, conversation, data, payer)
        data["payer_attempts"] = data.get("payer_attempts", 0) + 1
        if data["payer_attempts"] < 2:
            _save(conversation, data)
            return IntakeTurn(reply=_payer_question(tenant, "No logré identificar tu opción. "))
        # Second miss: do not block the family; the secretary will ask.
        data.update(step=STEP_NAME, payer="UNSPECIFIED", payer_label="No especificada")
        _save(conversation, data)
        return IntakeTurn(reply="No hay problema, lo confirmará nuestro equipo. ¿Cuál es el nombre completo del paciente?")
 
    if step == STEP_NAME:
        name = clean_name(body)
        if name is None:
            _save(conversation, data)
            return IntakeTurn(reply="¿Me indicas el nombre completo del paciente, por favor?")
        data.update(step=STEP_CONCERN, patient_name=name)
        _save(conversation, data)
        return IntakeTurn(
            reply=(
                f"Gracias. Y cuéntame brevemente, ¿qué terapia o evaluación buscas para {name}, "
                "o qué situación te preocupa? (por ejemplo: lenguaje, conducta, aprendizaje, "
                "evaluación o apoyo psicológico)"
            )
        )
 
    # STEP_CONCERN
    concern = " ".join(body.split())[:MAX_CONCERN_LEN]
    data.update(step=STEP_DONE, concern=concern)
    _save(conversation, data)
    reason = (
        f"Solicitud de nuevo paciente — Paciente: {data['patient_name']}. "
        f"Vía: {data['payer_label']}. Motivo/terapia: {concern}"
    )[:500]
    return IntakeTurn(
        reply=(
            "Listo, gracias. Ya dejé tu solicitud con nuestro equipo y una persona te "
            "escribirá por aquí para continuar con el registro."
        ),
        complete=True,
        reason=reason,
    )