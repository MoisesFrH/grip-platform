"""
Seeds a realistic-looking demo dataset for the ND Neurodiversa tenant (slug "grip") — patients,
appointments, AND conversation/message history — so both the internal CRM
(/crm) and the dashboard (/dashboard) have something to show during a demo
instead of looking empty.

Separate from scripts/seed_grip.py on purpose: that one seeds the
tenant's bot config (business info, therapists, services) and runs
automatically on every deploy (see the Procfile/deploy command) — it must
stay idempotent-and-safe-to-always-run. This script is meant to be run
once, by hand, whenever a fresh demo dataset is wanted, and is idempotent
by phone number: re-running it replaces each demo contact's appointments/
conversations/messages/notes rather than piling up duplicates, so it's
safe to run again before a second demo.

One deliberate omission: no SAFETY_RISK (suicide/self-harm) example is
seeded, even though the dashboard has a tile for it. That's a real
patient-safety feature, not just a chart to fill in, and writing
simulated self-harm dialogue into a database — even clearly fake data —
isn't something to manufacture for a demo. The CLINICAL_QUESTION examples
below (HIGH priority, non-crisis) already exercise the same "safety.py
metadata -> dashboard" pipeline end to end. If you want to demo the
CRITICAL path specifically, it's safer to trigger it live in the
simulator with wording you choose yourself.

Usage (locally):
    .venv/bin/python -m scripts.seed_demo_data

On Railway (from your machine, with the Railway CLI linked to the project):
    railway run python -m scripts.seed_demo_data
"""

from datetime import datetime, timedelta, timezone

from app.core.db import SessionLocal
from app.models.appointment import Appointment, AppointmentStatus, ReminderResponse
from app.models.clinical_note import ClinicalNote, ClinicalNoteType
from app.models.contact import Contact, ContactStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.message import Message, MessageDirection, MessageSender, MessageStatus
from app.models.tenant import Tenant
from app.services import appointments as appointments_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_contact(
    db, tenant: Tenant, *, phone: str, name: str, status: ContactStatus, backdate_created_days: int | None = None
) -> Contact:
    contact = (
        db.query(Contact)
        .filter(Contact.tenant_id == tenant.id, Contact.phone == phone)
        .one_or_none()
    )
    is_new = contact is None
    if contact is None:
        contact = Contact(tenant_id=tenant.id, phone=phone)
        db.add(contact)
    contact.name = name
    contact.status = status
    contact.last_interaction_at = _now()
    if is_new and backdate_created_days:
        # Makes this contact read as an existing/"returning" patient rather
        # than someone who just messaged for the first time this week —
        # see new_vs_returning_patients in app/services/analytics.py, which
        # compares Contact.created_at to the reporting window's start.
        contact.created_at = _now() - timedelta(days=backdate_created_days)
    db.flush()
    return contact


def _reset_demo_history(db, contact: Contact) -> None:
    """Wipes this demo contact's OWN appointments/notes/conversations
    before reseeding, so re-running this script updates the demo instead
    of duplicating it. Scoped strictly to contact_id, so it never touches
    real patients or the separate test contacts used in the WhatsApp
    simulator. Deleting a Conversation cascades to its Messages at the
    database level (see Message.conversation_id's ondelete="CASCADE")."""
    db.query(ClinicalNote).filter(ClinicalNote.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Appointment).filter(Appointment.contact_id == contact.id).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.contact_id == contact.id).delete(synchronize_session=False)
    db.flush()


def _add_note(
    db,
    tenant: Tenant,
    contact: Contact,
    *,
    days_ago: float,
    author: str,
    content: str,
    note_type: ClinicalNoteType = ClinicalNoteType.NOTE,
    appointment: Appointment | None = None,
) -> ClinicalNote:
    """Adds one fictional clinical note, dated `days_ago` days back so the
    patient's history reads like real follow-up over several weeks."""
    note = ClinicalNote(
        tenant_id=tenant.id,
        contact_id=contact.id,
        appointment_id=appointment.id if appointment else None,
        author_name=author,
        note_type=note_type,
        content=content,
    )
    note.created_at = _now() - timedelta(days=days_ago)
    db.add(note)
    return note


def _seed_patient(
    db,
    tenant: Tenant,
    *,
    name: str,
    phone: str,
    therapist: str,
    appointments: list[tuple[float, AppointmentStatus]],
    notes: list[tuple[float, str, ClinicalNoteType, str]],
    backdate_days: int = 60,
    status: ContactStatus = ContactStatus.PATIENT,
    reset: bool = True,
) -> Contact:
    """Data-driven demo patient: appointments are (days_from_now, status)
    (negative = past) and notes are (days_ago, author, type, content).
    Past appointments get a completed/no-show status as given; upcoming
    ones are left SCHEDULED unless told otherwise."""
    contact = _get_or_create_contact(
        db, tenant, phone=phone, name=name, status=status, backdate_created_days=backdate_days
    )
    if reset:
        # reset=False keeps a conversation seeded earlier for this contact.
        _reset_demo_history(db, contact)
    for days, appt_status in appointments:
        appt = appointments_service.create_appointment(
            db, tenant, contact, therapist_name=therapist, scheduled_at=_now() + timedelta(days=days)
        )
        appt.status = appt_status
    for days_ago, author, note_type, content in notes:
        _add_note(db, tenant, contact, days_ago=days_ago, author=author, note_type=note_type, content=content)
    return contact


def _seed_conversation(
    db,
    tenant: Tenant,
    contact: Contact,
    *,
    days_ago: float,
    hour: int,
    minute: int = 0,
    status: ConversationStatus,
    turns: list[tuple[MessageDirection, MessageSender, str, str | None]],
    handoff_reason: str | None = None,
    handoff_priority: str | None = None,
    triggered_after_minutes: float = 3,
    claimed_after_minutes: float | None = None,
    safety_categories: dict[int, str] | None = None,
) -> Conversation:
    """turns: (direction, sender, body, intent) tuples, in order — intent
    is only meaningful on inbound turns (matches how the real bot only
    classifies inbound messages). safety_categories maps a turn index to
    a SafetyCategory value, stored in that message's extra_metadata the
    same way app.services.safety/orchestrator does it for real."""
    started_at = (_now() - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    conversation = Conversation(
        tenant_id=tenant.id, contact_id=contact.id, status=status, started_at=started_at
    )
    db.add(conversation)
    db.flush()

    t = started_at
    for i, (direction, sender, body, intent) in enumerate(turns):
        message = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            direction=direction,
            sender=sender,
            body=body,
            intent=intent,
            status=MessageStatus.RECEIVED if direction == MessageDirection.INBOUND else MessageStatus.DELIVERED,
            sent_at=t if direction == MessageDirection.OUTBOUND else None,
            created_at=t,
            extra_metadata={"safety_category": safety_categories[i]} if safety_categories and i in safety_categories else {},
        )
        db.add(message)
        t += timedelta(minutes=2)

    conversation.last_message_at = t - timedelta(minutes=2)

    if handoff_reason:
        conversation.handoff_reason = handoff_reason
        conversation.handoff_priority = handoff_priority
        conversation.handoff_triggered_at = started_at + timedelta(minutes=triggered_after_minutes)
        if claimed_after_minutes is not None:
            conversation.claimed_at = conversation.handoff_triggered_at + timedelta(minutes=claimed_after_minutes)

    db.flush()
    return conversation


IN, OUT = MessageDirection.INBOUND, MessageDirection.OUTBOUND
PATIENT, BOT, HUMAN = MessageSender.PATIENT, MessageSender.BOT, MessageSender.HUMAN_AGENT


def seed() -> None:
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == "grip").one_or_none()
        if tenant is None:
            raise RuntimeError("Tenant 'grip' not found — run `python -m scripts.seed_grip` first.")

        # ----- Patients (CRM screen) --------------------------------------

        # 1. Wanda Pérez — cita de mañana, recordatorio todavía sin enviar.
        wanda = _get_or_create_contact(db, tenant, phone="+18095550101", name="Wanda Pérez", status=ContactStatus.PATIENT)
        _reset_demo_history(db, wanda)
        appointments_service.create_appointment(
            db, tenant, wanda, therapist_name="María Fernanda Ruiz", scheduled_at=_now() + timedelta(hours=20)
        )
        # Fictional notes (demo data only) so the "Resumen con IA" button in
        # the CRM has real material to summarize.
        _add_note(
            db, tenant, wanda, days_ago=42, author="María Fernanda Ruiz",
            content=(
                "Consulta por dificultad sostenida para concentrarse en el trabajo, "
                "olvidos frecuentes y desorden con las tareas. Antecedente de bajo "
                "rendimiento escolar en la infancia. Se inicia evaluación de "
                "atención y funciones ejecutivas."
            ),
        )
        _add_note(
            db, tenant, wanda, days_ago=28, author="María Fernanda Ruiz",
            content=(
                "Completadas las escalas de atención. Resultados compatibles con "
                "un perfil de inatención marcado; se necesita descartar ansiedad "
                "como factor de fondo. Paciente colaboradora, llega puntual."
            ),
        )
        _add_note(
            db, tenant, wanda, days_ago=14, author="María Fernanda Ruiz",
            note_type=ClinicalNoteType.RECOMMENDATION,
            content=(
                "Recomendaciones: agenda semanal con alarmas, dividir tareas en "
                "pasos cortos y bloques de trabajo de 25 minutos con pausa. "
                "Revisar cómo le fue con estas estrategias en la próxima sesión."
            ),
        )
        _add_note(
            db, tenant, wanda, days_ago=2, author="María Fernanda Ruiz",
            content=(
                "Refiere que las alarmas ayudan, pero le cuesta mantener la "
                "rutina los fines de semana. Pendiente: devolución de resultados "
                "y valorar derivación a psiquiatría para evaluación médica."
            ),
        )
        _seed_conversation(
            db, tenant, wanda, days_ago=3, hour=10, status=ConversationStatus.CLOSED,
            turns=[
                (IN, PATIENT, "Buenas, quisiera saber cómo puedo agendar una cita", "BOOKING_GUIDANCE"),
                (OUT, BOT, "Claro, escríbenos por aquí el servicio y el horario que prefieres y alguien del equipo te confirma la cita.", None),
                (IN, PATIENT, "Perfecto, gracias", None),
            ],
        )

        # 2. Carlos Méndez — cita de mañana, recordatorio ya enviado y confirmado.
        carlos = _get_or_create_contact(db, tenant, phone="+18295550142", name="Carlos Méndez", status=ContactStatus.PATIENT)
        _reset_demo_history(db, carlos)
        apt_carlos = appointments_service.create_appointment(
            db, tenant, carlos, therapist_name="Andrés Salas", scheduled_at=_now() + timedelta(hours=26)
        )
        apt_carlos.reminder_sent_at = _now() - timedelta(hours=2)
        apt_carlos.reminder_response = ReminderResponse.CONFIRMED
        apt_carlos.status = AppointmentStatus.CONFIRMED
        _add_note(
            db, tenant, carlos, days_ago=35, author="Andrés Salas",
            content=(
                "Primera consulta. Motivo: dificultades en la interacción social "
                "y rigidez ante cambios de rutina. Sensibilidad marcada a ruidos "
                "fuertes. Se acuerdan sesiones semanales de habilidades sociales."
            ),
        )
        _add_note(
            db, tenant, carlos, days_ago=21, author="Andrés Salas",
            content=(
                "Buena respuesta a los apoyos visuales. Practicó iniciar "
                "conversaciones breves en la sesión. En casa persisten las "
                "dificultades con las transiciones (salir de casa por la mañana)."
            ),
        )
        _add_note(
            db, tenant, carlos, days_ago=7, author="Andrés Salas",
            note_type=ClinicalNoteType.RECOMMENDATION,
            content=(
                "Recomendación a la familia: anticipar los cambios con un "
                "calendario visual, avisar con 10 minutos antes de cada "
                "transición y mantener un espacio tranquilo en casa."
            ),
        )
        _seed_conversation(
            db, tenant, carlos, days_ago=5, hour=14, status=ConversationStatus.CLOSED,
            turns=[
                (IN, PATIENT, "¿Qué días atiende el Dr. Andrés Salas?", "THERAPIST_AVAILABILITY"),
                (OUT, BOT, "El Dr. Andrés Salas atiende de forma presencial; su disponibilidad actual es limitada, pero puedo ayudarte a encontrar un espacio.", None),
            ],
        )

        # 3. Rosa Difo — recordatorio enviado, pidió reprogramar; handoff
        # todavía SIN reclamar, para mostrar un pendiente real en el dashboard.
        rosa = _get_or_create_contact(db, tenant, phone="+18495550177", name="Rosa Difo", status=ContactStatus.PATIENT)
        _reset_demo_history(db, rosa)
        apt_rosa = appointments_service.create_appointment(
            db, tenant, rosa, therapist_name="María Fernanda Ruiz", scheduled_at=_now() + timedelta(hours=18)
        )
        apt_rosa.reminder_sent_at = _now() - timedelta(hours=1)
        apt_rosa.reminder_response = ReminderResponse.CANCEL_OR_RESCHEDULE
        apt_rosa.status = AppointmentStatus.RESCHEDULE_REQUESTED
        _add_note(
            db, tenant, rosa, days_ago=30, author="María Fernanda Ruiz",
            content=(
                "Consulta por retraso en el lenguaje expresivo de su hijo (4 "
                "años): usa pocas palabras y señala para pedir. Comprensión "
                "adecuada para su edad. Se solicita informe de audiometría."
            ),
        )
        _add_note(
            db, tenant, rosa, days_ago=9, author="María Fernanda Ruiz",
            content=(
                "Audiometría sin hallazgos. Avance leve: ya combina dos "
                "palabras en juego simbólico. La madre pide pocos horarios "
                "disponibles por trabajo; conviene ofrecer citas de tarde."
            ),
        )
        _seed_conversation(
            db, tenant, rosa, days_ago=0.05, hour=_now().hour, status=ConversationStatus.WAITING_FOR_HUMAN,
            turns=[
                (IN, PATIENT, "Necesito cambiar mi cita de mañana, no voy a poder llegar a esa hora", "APPOINTMENT_CHANGE"),
            ],
            handoff_reason="Solicitud de reprogramación de cita",
            handoff_priority="MEDIUM",
            triggered_after_minutes=1,
        )

        # 4. Yolanda Ramírez — paciente ya conocida (se registró hace 60
        # días, fuera de la ventana del reporte, para que cuente como
        # "returning" y no "new"); historial con una cita completada + nota
        # clínica, una próxima cita, y una consulta clínica ya resuelta.
        yolanda = _get_or_create_contact(
            db, tenant, phone="+18495550110", name="Yolanda Ramírez", status=ContactStatus.PATIENT,
            backdate_created_days=60,
        )
        _reset_demo_history(db, yolanda)
        apt_past = appointments_service.create_appointment(
            db, tenant, yolanda, therapist_name="Lucía Torres", scheduled_at=_now() - timedelta(days=7)
        )
        apt_past.status = AppointmentStatus.COMPLETED
        db.flush()
        _add_note(
            db, tenant, yolanda, days_ago=7, author="Lucía Torres", appointment=apt_past,
            content=(
                "Primera sesión de evaluación. Reporta ansiedad relacionada con el "
                "trabajo y dificultad para dormir. Se recomienda continuar con la "
                "batería de evaluación completa en las próximas dos sesiones."
            ),
        )
        _add_note(
            db, tenant, yolanda, days_ago=1, author="Lucía Torres",
            note_type=ClinicalNoteType.RECOMMENDATION,
            content=(
                "Recomendaciones para el sueño: horario fijo para acostarse, "
                "sin pantallas la hora previa y registro diario de cuántas "
                "horas duerme. Traer el registro a la próxima cita."
            ),
        )
        appointments_service.create_appointment(
            db, tenant, yolanda, therapist_name="Lucía Torres", scheduled_at=_now() + timedelta(days=3, hours=4)
        )
        _seed_conversation(
            db, tenant, yolanda, days_ago=10, hour=16, status=ConversationStatus.HUMAN_RESOLVED,
            turns=[
                (IN, PATIENT, "Hola, he tenido problemas para dormir esta semana, ¿es normal después de empezar terapia?", "CLINICAL_QUESTION"),
                (OUT, HUMAN, "Hola Yolanda, gracias por escribir. Sí, es algo que puede pasar al inicio; hablemos de esto en tu próxima sesión con Lucía, y si empeora antes, avísanos.", None),
            ],
            safety_categories={0: "SYMPTOMS"},
            handoff_reason="Pregunta clínica sobre síntomas — requiere respuesta de un profesional, no del bot",
            handoff_priority="HIGH",
            triggered_after_minutes=1,
            claimed_after_minutes=6,
        )

        # 5. Julián Objio — lead que preguntó por servicios y precios,
        # todavía sin cita agendada.
        julian = _get_or_create_contact(db, tenant, phone="+18095550199", name="Julián Objio", status=ContactStatus.LEAD)
        _reset_demo_history(db, julian)
        _seed_conversation(
            db, tenant, julian, days_ago=2, hour=11, status=ConversationStatus.CLOSED,
            turns=[
                (IN, PATIENT, "Buenas tardes, ¿qué servicios ofrecen?", "SERVICES"),
                (OUT, BOT, "Ofrecemos terapia individual, terapia de pareja y evaluaciones psicológicas, presencial y online.", None),
                (IN, PATIENT, "¿Y cómo son los pagos?", "PAYMENT_INFO"),
                (OUT, BOT, "Aceptamos tarjeta, transferencia o efectivo en recepción, al finalizar cada sesión o al reservar en línea.", None),
            ],
        )

        # ----- Extra activity, just for the dashboard (no CRM appointment) --

        # 6. Ana Beltrán — cita pasada ya marcada como completada (no_show_rate).
        ana = _get_or_create_contact(db, tenant, phone="+18095550210", name="Ana Beltrán", status=ContactStatus.PATIENT)
        _reset_demo_history(db, ana)
        apt_ana = appointments_service.create_appointment(
            db, tenant, ana, therapist_name="María Fernanda Ruiz", scheduled_at=_now() - timedelta(days=5)
        )
        apt_ana.status = AppointmentStatus.COMPLETED
        _add_note(
            db, tenant, ana, days_ago=5, author="María Fernanda Ruiz", appointment=apt_ana,
            content=(
                "Sesión de seguimiento. La hija (7 años) tiene menos conflictos "
                "con los deberes escolares desde que usan temporizador. La "
                "escuela pide un informe para adaptar las tareas."
            ),
        )
        _seed_conversation(
            db, tenant, ana, days_ago=6, hour=8, status=ConversationStatus.CLOSED,
            turns=[
                (IN, PATIENT, "Hola, ¿dónde queda la clínica?", "LOCATION"),
                (OUT, BOT, "Estamos en C/ Antonio Pasica 16, Moca. Aquí tienes el mapa: [enlace]", None),
            ],
        )

        # 7. Miguel Reyes — cita pasada marcada como no-show (no_show_rate).
        miguel = _get_or_create_contact(db, tenant, phone="+18295550233", name="Miguel Reyes", status=ContactStatus.PATIENT)
        _reset_demo_history(db, miguel)
        apt_miguel = appointments_service.create_appointment(
            db, tenant, miguel, therapist_name="Andrés Salas", scheduled_at=_now() - timedelta(days=3)
        )
        apt_miguel.status = AppointmentStatus.NO_SHOW
        _add_note(
            db, tenant, miguel, days_ago=24, author="Andrés Salas",
            content=(
                "Evaluación inicial por dificultades de conducta en el aula y "
                "baja tolerancia a la frustración. Buena disposición. Se "
                "plantea plan de trabajo quincenal."
            ),
        )
        _add_note(
            db, tenant, miguel, days_ago=3, author="Andrés Salas",
            content=(
                "No asistió a la sesión programada y no avisó. Segunda "
                "ausencia en el último mes. Pendiente: contactar a la familia "
                "para confirmar si el horario sigue siendo viable."
            ),
        )
        _seed_conversation(
            db, tenant, miguel, days_ago=4, hour=19, status=ConversationStatus.CLOSED,
            turns=[
                (IN, PATIENT, "¿Qué terapeutas tienen disponibles?", "THERAPISTS"),
                (OUT, BOT, "Actualmente: María Fernanda Ruiz, Andrés Salas y Lucía Torres, cada uno con distintas especialidades.", None),
            ],
        )

        # 8. Daniela Cruz — problema de pago, escalado y ya resuelto por una persona.
        daniela = _get_or_create_contact(db, tenant, phone="+18495550256", name="Daniela Cruz", status=ContactStatus.PATIENT)
        _reset_demo_history(db, daniela)
        _seed_conversation(
            db, tenant, daniela, days_ago=2, hour=15, status=ConversationStatus.HUMAN_RESOLVED,
            turns=[
                (IN, PATIENT, "Me cobraron dos veces la sesión de la semana pasada", "PAYMENT_ISSUE"),
                (OUT, HUMAN, "Hola Daniela, disculpa el inconveniente. Ya verifiqué y vamos a hacer el reembolso del cobro duplicado hoy mismo.", None),
            ],
            handoff_reason="Reporte de cobro duplicado",
            handoff_priority="MEDIUM",
            triggered_after_minutes=1,
            claimed_after_minutes=15,
        )

        # 9. Esteban Paulino — contacto nuevo de hoy, conversación EN VIVO con
        # una persona ahora mismo (para que el dashboard muestre algo "activo").
        esteban = _get_or_create_contact(db, tenant, phone="+18095550278", name="Esteban Paulino", status=ContactStatus.NEW_CONTACT)
        _reset_demo_history(db, esteban)
        _seed_conversation(
            db, tenant, esteban, days_ago=0.02, hour=_now().hour, status=ConversationStatus.HUMAN_ACTIVE,
            turns=[
                (IN, PATIENT, "Quisiera hablar con alguien del equipo directamente, por favor", "HUMAN_REQUEST"),
                (OUT, HUMAN, "Hola Esteban, soy parte del equipo de ND Neurodiversa, ¿en qué te puedo ayudar?", None),
            ],
            handoff_reason="Paciente solicitó hablar directamente con una persona",
            handoff_priority="MEDIUM",
            triggered_after_minutes=1,
            claimed_after_minutes=2,
        )

        # ----- Más pacientes para que el CRM luzca lleno en la demo --------
        # Todos ficticios. Cada uno con citas pasadas/próximas y notas.
        S, C, D, X = (AppointmentStatus.SCHEDULED, AppointmentStatus.COMPLETED,
                      AppointmentStatus.CONFIRMED, AppointmentStatus.NO_SHOW)
        NOTE, REC = ClinicalNoteType.NOTE, ClinicalNoteType.RECOMMENDATION
        MF, AS, LT = "María Fernanda Ruiz", "Andrés Salas", "Lucía Torres"

        _seed_patient(
            db, tenant, name="Sofía Martínez", phone="+18095550311", therapist=MF,
            appointments=[(-40, C), (-26, C), (-12, C), (2, S)],
            notes=[
                (40, MF, NOTE, "Consulta de la madre por sospecha de TDAH en su hija (9 años): se distrae en clase, pierde útiles y le cuesta terminar tareas. Sin problemas de sueño. Se inicia evaluación."),
                (26, MF, NOTE, "Aplicadas escalas a madre y maestra; coinciden en inatención en casa y escuela. Buen vínculo con la terapeuta. Se solicita informe escolar."),
                (12, MF, REC, "Recomendaciones a la familia: rutina fija de tareas con temporizador, instrucciones de un paso a la vez y reforzar lo positivo de inmediato."),
                (3, MF, NOTE, "Informe escolar recibido: confirma el patrón. Pendiente: devolución de resultados a la familia y plan con la escuela."),
            ],
        )
        _seed_patient(
            db, tenant, name="Luis Fernando Almonte", phone="+18295550322", therapist=AS,
            appointments=[(-33, C), (-19, C), (-5, C), (4, S)],
            notes=[
                (33, AS, NOTE, "Niño de 6 años derivado por la escuela: poco contacto visual, juego repetitivo con carros alineados, molestia con el ruido del timbre. Evaluación del espectro en curso."),
                (19, AS, NOTE, "Observación en sesión: responde a su nombre de forma intermitente, usa frases de 2-3 palabras. Comprensión buena. Se trabaja turnos de juego."),
                (5, AS, REC, "Recomendación: apoyos visuales (secuencia de la mañana en imágenes), audífonos reductores de ruido en eventos y avisar los cambios con antelación."),
            ],
        )
        _seed_patient(
            db, tenant, name="Carmen Rosario", phone="+18495550333", therapist=LT,
            appointments=[(-28, C), (-14, C), (1, D)],
            notes=[
                (28, LT, NOTE, "Adulta que consulta por ansiedad y dificultad para organizarse en el trabajo; sospecha de TDAH no diagnosticado de adulta. Motivada."),
                (14, LT, NOTE, "Aplicada entrevista de historia del desarrollo. Refiere inquietud desde la infancia. Mejora leve del sueño con el registro semanal."),
                (4, LT, REC, "Pautas: lista de tres prioridades al día, pausas activas cada hora y limitar cafeína después de las 2 p. m."),
            ],
        )
        _seed_patient(
            db, tenant, name="Pedro Jiménez", phone="+18095550344", therapist=MF,
            appointments=[(-21, C), (-7, X), (3, S)],
            notes=[
                (21, MF, NOTE, "Niño de 8 años con dificultades de lectura y escritura (confunde letras, lectura lenta). Inteligencia verbal normal. Se descarta problema visual con oftalmólogo."),
                (7, MF, NOTE, "No asistió a la última sesión; la madre avisó después por trabajo. Se ofrece horario de tarde."),
            ],
        )
        _seed_patient(
            db, tenant, name="Valentina Peña", phone="+18295550355", therapist=LT,
            appointments=[(-45, C), (-31, C), (-17, C), (-3, C), (5, S)],
            notes=[
                (45, LT, NOTE, "Niña de 5 años con retraso del lenguaje expresivo y ansiedad de separación al entrar al salón. Buena comprensión."),
                (31, LT, NOTE, "Incorpora 10 palabras nuevas por juego simbólico. Llora menos al despedirse de la madre."),
                (17, LT, REC, "Leer cuentos con imágenes 15 minutos diarios, nombrar objetos del hogar y evitar corregir de forma directa."),
                (3, LT, NOTE, "Frases de 3 palabras de forma espontánea. La maestra reporta mejor adaptación. Continuar con el plan actual."),
            ],
        )
        _seed_patient(
            db, tenant, name="Ramón Gómez", phone="+18495550366", therapist=AS,
            appointments=[(-10, C), (6, S)],
            notes=[
                (10, AS, NOTE, "Primera consulta. Adolescente de 14 años con baja tolerancia a la frustración y conflictos en el aula. Se muestra reservado. Acuerdo de sesiones quincenales."),
            ],
        )
        _seed_patient(
            db, tenant, name="Isabela Núñez", phone="+18095550377", therapist=MF,
            appointments=[(-35, C), (-21, C), (-9, X), (-2, X), (7, S)],
            notes=[
                (35, MF, NOTE, "Adolescente de 12 años con rasgos de ansiedad social y perfeccionismo. Evita exposiciones orales."),
                (21, MF, REC, "Práctica gradual de exposición: empezar por hablar en grupos pequeños; respiración lenta antes de participar."),
                (2, MF, NOTE, "Segunda ausencia consecutiva sin aviso. Pendiente: llamar a la familia para revisar si el horario o la motivación son el problema."),
            ],
        )
        _seed_patient(
            db, tenant, name="Andrés Castillo", phone="+18295550388", therapist=LT,
            appointments=[(-8, C), (8, S)],
            notes=[
                (8, LT, NOTE, "Adulto joven (22 años) con diagnóstico previo de TEA nivel 1; busca apoyo para la transición al trabajo. Buen nivel verbal, rigidez ante cambios de horario."),
            ],
        )
        _seed_patient(
            db, tenant, name="Marisol Ureña", phone="+18495550399", therapist=AS,
            appointments=[(-50, C), (-36, C), (-22, C), (-8, C), (2, D)],
            notes=[
                (50, AS, NOTE, "Madre consulta por berrinches intensos de su hijo (4 años) y dificultad para cambios de actividad."),
                (36, AS, REC, "Pautas de crianza: anticipar transiciones, ofrecer dos opciones, validar la emoción antes de poner el límite."),
                (22, AS, NOTE, "Reducción de berrinches de 5 a 2 por semana. La madre refiere cansancio; se refuerza su autocuidado."),
                (8, AS, NOTE, "Continúa mejoría. Se planea espaciar las sesiones a cada tres semanas si se mantiene."),
            ],
        )
        _seed_patient(
            db, tenant, name="Héctor Batista", phone="+18095550410", therapist=LT,
            appointments=[(-15, C), (6, S)],
            notes=[
                (15, LT, NOTE, "Evaluación inicial de hijo de 10 años por bajo rendimiento y distracción. Se solicita informe del colegio y examen auditivo."),
            ],
            status=ContactStatus.PATIENT,
        )

        # Daniela (ya tenía conversación) ahora con historial clínico también.
        _seed_patient(
            db, tenant, name="Daniela Cruz", phone="+18495550256", therapist=MF,
            appointments=[(-30, C), (-16, C), (-2, C), (5, S)],
            reset=False,
            notes=[
                (30, MF, NOTE, "Consulta por dificultad de atención sostenida y olvidos frecuentes. Evaluación de funciones ejecutivas iniciada."),
                (16, MF, NOTE, "Resultados preliminares: atención dividida por debajo de lo esperado. Se descarta influencia del sueño por ahora."),
                (2, MF, REC, "Usar recordatorios en el teléfono, dividir proyectos grandes en pasos y revisar la lista cada noche."),
            ],
        )

        db.commit()
        print(
            "Datos de demo listos (CRM + dashboard): Wanda Pérez, Carlos Méndez, "
            "Rosa Difo, Yolanda Ramírez, Julián Objio, Ana Beltrán, Miguel Reyes, "
            "Daniela Cruz, Esteban Paulino + 9 pacientes más con citas y notas."
        )
    finally:
        db.close()


if __name__ == "__main__":
    seed()