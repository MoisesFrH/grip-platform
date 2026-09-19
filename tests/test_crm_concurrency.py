"""
Proves the row lock in get_open_conversation_for_update actually blocks a
concurrent transaction, using two real DB connections/threads — not just
that the code compiles. This is the direct answer to architecture doc
§11's "cómo evitar race conditions": without the lock, two webhook
deliveries for the same patient's back-to-back messages could both read
"no open conversation" and each create one.

Run with:
    .venv/bin/python -m tests.test_crm_concurrency
"""

import threading
import time
import uuid

from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from app.services.crm import get_open_conversation_for_update, get_or_create_contact, get_or_create_open_conversation


def _test_phone() -> str:
    return f"+1555{uuid.uuid4().int % 10_000_000:07d}"


def test_concurrent_lookup_serializes_instead_of_double_creating() -> None:
    setup_db = SessionLocal()
    tenant = setup_db.query(Tenant).filter(Tenant.slug == "grip").one()
    tenant_id = tenant.id
    phone = _test_phone()
    contact = get_or_create_contact(setup_db, tenant, phone)
    contact_id = contact.id
    setup_db.commit()
    setup_db.close()

    # Thread A: locks the (nonexistent-yet) conversation row set, creates
    # one, holds the transaction open for a moment before committing —
    # simulating the time a real request spends doing safety/intent/Gemini
    # work before it's done with the conversation.
    thread_a_holding = threading.Event()
    thread_a_release = threading.Event()
    results: dict[str, object] = {}

    def thread_a():
        db = SessionLocal()
        try:
            tenant_row = db.query(Tenant).filter(Tenant.id == tenant_id).one()
            contact_row = db.query(Contact).filter(Contact.id == contact_id).one()
            conv = get_or_create_open_conversation(db, tenant_row, contact_row)
            results["a_conversation_id"] = conv.id
            thread_a_holding.set()
            thread_a_release.wait(timeout=5)
            db.commit()
        finally:
            db.close()

    def thread_b():
        # Waits until A has definitely created (but not committed) its
        # conversation, then tries the same lookup. Without the lock, B
        # would see no rows yet (A hasn't committed) and create a SECOND
        # conversation for the same contact — the exact bug this guards
        # against. With the lock, B blocks until A commits, then correctly
        # sees and reuses A's conversation.
        thread_a_holding.wait(timeout=5)
        db = SessionLocal()
        try:
            tenant_row = db.query(Tenant).filter(Tenant.id == tenant_id).one()
            contact_row = db.query(Contact).filter(Contact.id == contact_id).one()

            start = time.monotonic()
            conv = get_or_create_open_conversation(db, tenant_row, contact_row)
            waited = time.monotonic() - start

            results["b_conversation_id"] = conv.id
            results["b_wait_seconds"] = waited
            db.commit()
        finally:
            db.close()

    ta = threading.Thread(target=thread_a)
    tb = threading.Thread(target=thread_b)

    ta.start()
    tb.start()

    # Let B sit blocked on the lock for a moment, then release A.
    time.sleep(0.5)
    thread_a_release.set()

    ta.join(timeout=5)
    tb.join(timeout=5)

    assert results.get("a_conversation_id") is not None
    assert results.get("b_conversation_id") == results["a_conversation_id"], (
        "the lock failed to serialize the two lookups: they created two different "
        "conversations for the same contact"
    )
    assert results["b_wait_seconds"] >= 0.4, (
        "thread B did not actually block on the row lock (returned almost instantly), "
        "meaning FOR UPDATE is not doing its job"
    )

    cleanup_db = SessionLocal()
    try:
        cleanup_db.query(Conversation).filter(Conversation.contact_id == contact_id).delete(synchronize_session=False)
        cleanup_db.query(Contact).filter(Contact.id == contact_id).delete(synchronize_session=False)
        cleanup_db.commit()
    finally:
        cleanup_db.close()

    print(f"PASS: test_concurrent_lookup_serializes_instead_of_double_creating (B waited {results['b_wait_seconds']:.2f}s)")


if __name__ == "__main__":
    test_concurrent_lookup_serializes_instead_of_double_creating()
