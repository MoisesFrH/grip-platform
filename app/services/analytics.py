"""
Read-only aggregation queries behind the tenant dashboard (GET /dashboard).

Deliberately separate from crm/orchestrator: this module never writes
anything, and every function takes a tenant + a time window and returns a
plain dict/list ready to serialize as JSON — no ORM objects leak out, so
the API route (app.api.routes.dashboard) stays a thin adapter.

All of this reads from the same messages/conversations tables the bot
already writes in the normal course of answering patients — there is no
separate events/analytics table to keep in sync.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from statistics import median

from app.models.appointment import Appointment, AppointmentStatus
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message, MessageDirection
from app.models.tenant import Tenant

# Intents that route to a human being at all (architecture doc's category
# B and C) — used for the handoff-rate metric. Kept as a literal set here
# rather than importing IntentCategory/INTENT_METADATA, since a
# conversation's handoff_priority/handoff_reason columns are the ground
# truth of "a handoff actually happened", independent of how intents are
# categorized elsewhere.
SAFETY_INTENTS = {"SAFETY_RISK", "CLINICAL_QUESTION"}


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def conversation_volume(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """New conversations per day over the window, plus a breakdown of
    every conversation currently in each state (not windowed — a
    conversation someone forgot to close six months ago should still show
    up as "still open" today)."""
    since = _since(days)

    by_day = (
        db.query(
            func.date_trunc("day", Conversation.started_at).label("day"),
            func.count(Conversation.id),
        )
        .filter(Conversation.tenant_id == tenant.id, Conversation.started_at >= since)
        .group_by("day")
        .order_by("day")
        .all()
    )

    by_status = (
        db.query(Conversation.status, func.count(Conversation.id))
        .filter(Conversation.tenant_id == tenant.id)
        .group_by(Conversation.status)
        .all()
    )

    return {
        "new_per_day": [{"date": day.date().isoformat(), "count": count} for day, count in by_day],
        "total_new": sum(count for _, count in by_day),
        "by_status": {status.value: count for status, count in by_status},
    }


def conversation_volume_by_outcome(db: Session, tenant: Tenant, days: int = 30) -> list[dict]:
    """Same "new conversations per day" as conversation_volume, but split
    by whether each conversation was ever escalated to a human
    (handoff_reason gets set exactly once, the first time it escalates —
    see orchestrator._handoff) versus handled by the bot start to finish.
    Meant for a stacked bar chart: bot-only vs. escalated, per day."""
    since = _since(days)
    day_expr = func.date_trunc("day", Conversation.started_at)
    escalated_expr = Conversation.handoff_reason.isnot(None)

    rows = (
        db.query(day_expr.label("day"), escalated_expr.label("escalated"), func.count(Conversation.id))
        .filter(Conversation.tenant_id == tenant.id, Conversation.started_at >= since)
        .group_by(day_expr, escalated_expr)
        .order_by(day_expr)
        .all()
    )

    by_day: dict[str, dict] = {}
    for day, escalated, count in rows:
        key = day.date().isoformat()
        entry = by_day.setdefault(key, {"date": key, "bot_only": 0, "escalated": 0})
        entry["escalated" if escalated else "bot_only"] += count

    return [by_day[key] for key in sorted(by_day)]


def appointments_overview(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """Counts appointments SCHEDULED within the window (not by created_at —
    what matters here is when the appointment itself falls, not when the
    row was inserted), broken down by status and by how the patient
    responded to their reminder, if any."""
    since = _since(days)
    until = datetime.now(timezone.utc) + timedelta(days=days)

    by_status = (
        db.query(Appointment.status, func.count(Appointment.id))
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.scheduled_at >= since,
            Appointment.scheduled_at <= until,
        )
        .group_by(Appointment.status)
        .all()
    )

    reminder_rows = (
        db.query(Appointment.reminder_response, func.count(Appointment.id))
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.reminder_sent_at.isnot(None),
            Appointment.reminder_sent_at >= since,
        )
        .group_by(Appointment.reminder_response)
        .all()
    )
    by_reminder_response = {"NO_RESPONSE": 0, "CONFIRMED": 0, "CANCEL_OR_RESCHEDULE": 0}
    reminders_sent = 0
    for response, count in reminder_rows:
        reminders_sent += count
        by_reminder_response[response.value if response else "NO_RESPONSE"] = count

    return {
        "by_status": {status.value: count for status, count in by_status},
        "reminders_sent": reminders_sent,
        "by_reminder_response": by_reminder_response,
    }


def reminder_log(db: Session, tenant: Tenant, days: int = 30, limit: int = 50) -> list[dict]:
    """Recent reminders sent, most recent first — "who got a reminder and
    what happened next." Contact phone is included since that's how staff
    identify a WhatsApp patient day-to-day; no message content, no
    clinical data."""
    since = _since(days)
    rows = (
        db.query(Appointment, Contact)
        .join(Contact, Appointment.contact_id == Contact.id)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.reminder_sent_at.isnot(None),
            Appointment.reminder_sent_at >= since,
        )
        .order_by(Appointment.reminder_sent_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "contact_name": contact.name or contact.whatsapp_profile_name,
            "contact_phone": contact.phone,
            "therapist_name": appointment.therapist_name,
            "scheduled_at": appointment.scheduled_at.isoformat(),
            "reminder_sent_at": appointment.reminder_sent_at.isoformat(),
            "status": appointment.status.value,
            "reminder_response": appointment.reminder_response.value if appointment.reminder_response else None,
        }
        for appointment, contact in rows
    ]


def handoff_rate(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """Of the conversations started in this window, what fraction ever
    needed a human at all (handoff_reason gets set exactly once, by
    orchestrator._handoff, the first time a conversation escalates)."""
    since = _since(days)

    total = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.tenant_id == tenant.id, Conversation.started_at >= since)
        .scalar()
    ) or 0

    handed_off = (
        db.query(func.count(Conversation.id))
        .filter(
            Conversation.tenant_id == tenant.id,
            Conversation.started_at >= since,
            Conversation.handoff_reason.isnot(None),
        )
        .scalar()
    ) or 0

    by_priority = (
        db.query(Conversation.handoff_priority, func.count(Conversation.id))
        .filter(
            Conversation.tenant_id == tenant.id,
            Conversation.started_at >= since,
            Conversation.handoff_priority.isnot(None),
        )
        .group_by(Conversation.handoff_priority)
        .all()
    )

    return {
        "total_conversations": total,
        "handed_off": handed_off,
        "rate": round(handed_off / total, 3) if total else 0.0,
        "by_priority": {priority: count for priority, count in by_priority},
    }


def top_intents(db: Session, tenant: Tenant, days: int = 30, limit: int = 10) -> list[dict]:
    """What patients actually ask about, ranked — the single most useful
    "what should we improve" signal, since it comes straight from every
    classified inbound message rather than a sample."""
    since = _since(days)

    rows = (
        db.query(Message.intent, func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.tenant_id == tenant.id,
            Message.direction == MessageDirection.INBOUND,
            Message.intent.isnot(None),
            Message.created_at >= since,
        )
        .group_by(Message.intent)
        .order_by(func.count(Message.id).desc())
        .limit(limit)
        .all()
    )
    return [{"intent": intent, "count": count} for intent, count in rows]


def safety_alerts(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """Counts of safety/clinical escalations, broken down by the specific
    category (suicide/self-harm, abuse, medication question, etc.) that
    orchestrator.py stamps onto the message's extra_metadata. Never
    exposes message bodies here — only counts and categories, since this
    is a metrics view, not a case log."""
    since = _since(days)

    rows = (
        db.query(Message.intent, Message.extra_metadata)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.tenant_id == tenant.id,
            Message.direction == MessageDirection.INBOUND,
            Message.intent.in_(SAFETY_INTENTS),
            Message.created_at >= since,
        )
        .all()
    )

    by_priority = {"SAFETY_RISK": 0, "CLINICAL_QUESTION": 0}
    by_category: dict[str, int] = {}
    for intent, metadata in rows:
        by_priority[intent] = by_priority.get(intent, 0) + 1
        category = (metadata or {}).get("safety_category")
        if category:
            by_category[category] = by_category.get(category, 0) + 1

    return {
        "total_alerts": len(rows),
        "by_priority": by_priority,
        "by_category": by_category,
    }


def no_show_rate(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """Of appointments that have already happened (scheduled_at in the
    past, within the window) and were marked one way or the other, what
    fraction were a no-show. Appointments still SCHEDULED/CONFIRMED with a
    past date aren't counted either way — nobody has recorded an outcome
    for them yet, and counting an un-reviewed appointment as a "show"
    would understate the real rate. NOTE: nothing in this codebase marks
    COMPLETED/NO_SHOW automatically yet (there's no staff screen to do it
    from) — this will read as 0/0 until that exists."""
    since = _since(days)
    now = datetime.now(timezone.utc)

    rows = (
        db.query(Appointment.status, func.count(Appointment.id))
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.scheduled_at >= since,
            Appointment.scheduled_at <= now,
            Appointment.status.in_([AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW]),
        )
        .group_by(Appointment.status)
        .all()
    )
    counts = {status.value: count for status, count in rows}
    completed = counts.get("COMPLETED", 0)
    no_show = counts.get("NO_SHOW", 0)
    total = completed + no_show

    return {
        "completed": completed,
        "no_show": no_show,
        "rate": round(no_show / total, 3) if total else None,
    }


def time_to_claim_critical(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """How long a CRITICAL-priority handoff sat in WAITING_FOR_HUMAN before
    an agent claimed it — averaged over conversations escalated in this
    window that have since been claimed. Unclaimed ones aren't counted
    (their wait isn't over yet, so including a partial wait would make the
    average look better than it is); see safety_alerts for how many
    CRITICAL cases are still open right now."""
    since = _since(days)

    rows = (
        db.query(Conversation.handoff_triggered_at, Conversation.claimed_at)
        .filter(
            Conversation.tenant_id == tenant.id,
            Conversation.handoff_priority == "CRITICAL",
            Conversation.handoff_triggered_at.isnot(None),
            Conversation.handoff_triggered_at >= since,
            Conversation.claimed_at.isnot(None),
        )
        .all()
    )
    waits_seconds = [
        (claimed_at - triggered_at).total_seconds()
        for triggered_at, claimed_at in rows
        if claimed_at >= triggered_at
    ]

    return {
        "claimed_count": len(waits_seconds),
        "average_seconds": round(sum(waits_seconds) / len(waits_seconds)) if waits_seconds else None,
        "median_seconds": round(median(waits_seconds)) if waits_seconds else None,
    }


def new_vs_returning_patients(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """Of the contacts who messaged in this window, how many were writing
    to GRIP for the very first time (Contact.created_at falls inside the
    window — see crm.get_or_create_contact, which creates the row on a
    person's first-ever message) versus already-known contacts writing
    again."""
    since = _since(days)

    contact_created_at = (
        db.query(Conversation.contact_id, Contact.created_at)
        .join(Contact, Conversation.contact_id == Contact.id)
        .filter(Conversation.tenant_id == tenant.id, Conversation.started_at >= since)
        .distinct()
        .all()
    )
    new_count = sum(1 for _, created_at in contact_created_at if created_at >= since)
    returning_count = len(contact_created_at) - new_count

    return {"new": new_count, "returning": returning_count}


def volume_by_hour_and_weekday(db: Session, tenant: Tenant, days: int = 30) -> dict:
    """When patients actually write in — the busiest hours of the day and
    days of the week, from inbound message timestamps (not conversation
    start, so a long-running conversation's later messages count too).
    Hour is in UTC; the dashboard is responsible for any local-time
    framing it wants to add. weekday follows Postgres's extract(dow):
    0 = Sunday .. 6 = Saturday."""
    since = _since(days)

    hour_expr = func.extract("hour", Message.created_at)
    weekday_expr = func.extract("dow", Message.created_at)

    base_filter = (
        Conversation.tenant_id == tenant.id,
        Message.direction == MessageDirection.INBOUND,
        Message.created_at >= since,
    )

    by_hour_rows = (
        db.query(hour_expr.label("hour"), func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(*base_filter)
        .group_by(hour_expr)
        .all()
    )
    by_weekday_rows = (
        db.query(weekday_expr.label("weekday"), func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .filter(*base_filter)
        .group_by(weekday_expr)
        .all()
    )

    by_hour = {int(hour): 0 for hour in range(24)}
    for hour, count in by_hour_rows:
        by_hour[int(hour)] = count

    by_weekday = {day: 0 for day in range(7)}
    for weekday, count in by_weekday_rows:
        by_weekday[int(weekday)] = count

    return {
        "by_hour": [by_hour[h] for h in range(24)],
        "by_weekday": [by_weekday[d] for d in range(7)],  # index 0 = Sunday
    }


def patient_list(db: Session, tenant: Tenant, limit: int = 200) -> list[dict]:
    """The closest thing to a "patient list" today: every contact who has
    ever messaged this tenant, most recently active first. Administrative
    fields only (name/phone/status/contact dates + counts) — no message
    content, no clinical data. This is a read-only view, not the internal
    CRM screen (with actual create/edit of appointments and notes) that
    would need its own, staff-authenticated surface."""
    rows = (
        db.query(
            Contact,
            func.count(func.distinct(Conversation.id)),
            func.count(func.distinct(Appointment.id)),
        )
        .outerjoin(Conversation, Conversation.contact_id == Contact.id)
        .outerjoin(Appointment, Appointment.contact_id == Contact.id)
        .filter(Contact.tenant_id == tenant.id)
        .group_by(Contact.id)
        .order_by(Contact.last_interaction_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    return [
        {
            "name": contact.name or contact.whatsapp_profile_name,
            "phone": contact.phone,
            "status": contact.status.value,
            "first_contact_at": contact.created_at.isoformat(),
            "last_interaction_at": contact.last_interaction_at.isoformat() if contact.last_interaction_at else None,
            "conversation_count": conversation_count,
            "appointment_count": appointment_count,
        }
        for contact, conversation_count, appointment_count in rows
    ]


def dashboard_summary(db: Session, tenant: Tenant, days: int = 30) -> dict:
    return {
        "tenant": tenant.slug,
        "period_days": days,
        "conversations": conversation_volume(db, tenant, days),
        "conversations_by_outcome": conversation_volume_by_outcome(db, tenant, days),
        "handoff": handoff_rate(db, tenant, days),
        "top_intents": top_intents(db, tenant, days),
        "safety": safety_alerts(db, tenant, days),
        "appointments": appointments_overview(db, tenant, days),
        "no_show": no_show_rate(db, tenant, days),
        "time_to_claim_critical": time_to_claim_critical(db, tenant, days),
        "patients_new_vs_returning": new_vs_returning_patients(db, tenant, days),
        "volume_by_time": volume_by_hour_and_weekday(db, tenant, days),
    }