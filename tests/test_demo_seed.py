"""
Tests for the demo dataset (scripts/demo_patients.py + scripts/seed_demo_data.py).

Run with:
    .venv/bin/python -m pytest tests/test_demo_seed.py
"""

import uuid

from app.core.db import SessionLocal
from app.models.appointment import Appointment
from app.models.clinical_note import ClinicalNote
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.tenant import Tenant
from scripts import seed_demo_data
from scripts.demo_patients import PATIENTS


def _count(model, db, contact_ids):
    return db.query(model).filter(model.contact_id.in_(contact_ids)).count()


def test_demo_data_is_complete_for_every_patient():
    assert len(PATIENTS) == 7
    assert len({p["phone"] for p in PATIENTS}) == 7
    for p in PATIENTS:
        assert p["name"].strip()
        assert p["sessions"]["count"] >= 9  # "muchas sesiones"
        assert len(p["notes"]) >= 8
        assert sum(len(n[3].split()) for n in p["notes"]) >= 400  # notas largas
        assert p["upcoming"], p["name"]
        assert p["conversation"]["turns"], p["name"]


def test_seed_is_idempotent_and_wipe_others_removes_nameless_contacts():
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    stray = Contact(tenant_id=tenant.id, phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}")
    db.add(stray)
    db.commit()
    stray_id = stray.id
    try:
        seed_demo_data.seed()
        seed_demo_data.seed()  # twice: no duplicates

        demo = db.query(Contact).filter(Contact.phone.in_([p["phone"] for p in PATIENTS])).all()
        ids = [c.id for c in demo]
        assert len(demo) == 7
        assert _count(Appointment, db, ids) == sum(p["sessions"]["count"] + len(p["upcoming"]) for p in PATIENTS)
        assert _count(ClinicalNote, db, ids) == sum(len(p["notes"]) for p in PATIENTS)
        assert _count(Conversation, db, ids) == 7
        # Not touched without --wipe-others.
        assert db.query(Contact).filter(Contact.id == stray_id).count() == 1

        seed_demo_data.seed(wipe_others=True, assume_yes=True)
        db.expire_all()
        assert db.query(Contact).filter(Contact.id == stray_id).count() == 0
        assert db.query(Contact).filter(Contact.tenant_id == tenant.id).count() == 7
    finally:
        db.query(Contact).filter(Contact.id == stray_id).delete(synchronize_session=False)
        db.commit()
        db.close()