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


def dashboard_summary(db: Session, tenant: Tenant, days: int = 30) -> dict:
    return {
        "tenant": tenant.slug,
        "period_days": days,
        "conversations": conversation_volume(db, tenant, days),
        "handoff": handoff_rate(db, tenant, days),
        "top_intents": top_intents(db, tenant, days),
        "safety": safety_alerts(db, tenant, days),
    }