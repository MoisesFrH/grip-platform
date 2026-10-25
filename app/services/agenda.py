"""
Agenda rules: opening hours, appointment slots, overlap detection and the
list of therapists. Pure helpers plus a few queries; no HTTP here.

Everything is expressed in the clinic's own clock (America/Santo_Domingo, no
daylight saving). Appointments are stored in UTC.

Opening hours default to the ones the clinic gave for the demo (Mon-Fri
9:00-18:00, Saturday 8:00-13:00, Sunday closed). They are NOT put in
Tenant.config["hours"] on purpose: the bot would start quoting them to
patients before the center confirms them (see scripts/seed_grip.py). A tenant
may override them with Tenant.config["agenda"]["hours"] = {"0": [9, 18], ...}
(weekday number, Monday = 0; null = closed).
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.appointment import Appointment, AppointmentStatus
from app.models.tenant import Tenant
from app.models.therapist import Therapist

CLINIC_TZ = ZoneInfo("America/Santo_Domingo")
APPOINTMENT_MINUTES = 60

DEFAULT_HOURS: dict[int, tuple[int, int] | None] = {
    0: (9, 18), 1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18),  # Mon-Fri
    5: (8, 13),  # Saturday
    6: None,  # Sunday: closed
}

# Statuses that still occupy the therapist's time. CANCELLED / COMPLETED /
# NO_SHOW free the slot.
OCCUPYING = (AppointmentStatus.SCHEDULED, AppointmentStatus.CONFIRMED, AppointmentStatus.RESCHEDULE_REQUESTED)

# Test therapists for the demo (seeded by scripts/seed_grip.py). Names that
# already appear on appointments are always listed too.
DEFAULT_THERAPISTS = ["María Fernanda Ruiz", "Esther Jimenez"]


def hours_map(tenant: Tenant) -> dict[int, tuple[int, int] | None]:
    override = ((tenant.config or {}).get("agenda") or {}).get("hours")
    if not override:
        return DEFAULT_HOURS
    result: dict[int, tuple[int, int] | None] = {}
    for weekday in range(7):
        value = override.get(str(weekday), override.get(weekday))
        result[weekday] = (int(value[0]), int(value[1])) if value else None
    return result


def to_local(moment: datetime) -> datetime:
    return moment.astimezone(CLINIC_TZ)


def local_day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=CLINIC_TZ).astimezone(timezone.utc)


def slot_error(tenant: Tenant, start: datetime, minutes: int = APPOINTMENT_MINUTES) -> str | None:
    """None if [start, start+minutes) fits inside the opening hours of that
    day, otherwise a Spanish message for staff."""
    local = to_local(start)
    hours = hours_map(tenant).get(local.weekday())
    if hours is None:
        return "La clínica no atiende ese día."
    opens, closes = hours
    start_min = local.hour * 60 + local.minute
    if start_min < opens * 60 or start_min + minutes > closes * 60:
        return f"Fuera del horario de atención de ese día ({opens}:00 a {closes}:00)."
    return None


def overlaps(a_start: datetime, a_minutes: int, b_start: datetime, b_minutes: int) -> bool:
    return a_start < b_start + timedelta(minutes=b_minutes) and b_start < a_start + timedelta(minutes=a_minutes)


def find_conflicts(
    db: Session,
    tenant: Tenant,
    *,
    therapist_name: str,
    contact_id,
    start: datetime,
    minutes: int = APPOINTMENT_MINUTES,
    exclude_id=None,
) -> list[tuple[Appointment, str]]:
    """Occupying appointments that overlap the slot and belong to the same
    therapist ("therapist") or the same patient ("patient")."""
    window = timedelta(hours=6)
    rows = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant.id,
            Appointment.status.in_(OCCUPYING),
            Appointment.scheduled_at > start - window,
            Appointment.scheduled_at < start + window,
        )
        .all()
    )
    found: list[tuple[Appointment, str]] = []
    for other in rows:
        if exclude_id is not None and other.id == exclude_id:
            continue
        if not overlaps(start, minutes, other.scheduled_at, other.duration_minutes or APPOINTMENT_MINUTES):
            continue
        if other.therapist_name.strip().lower() == therapist_name.strip().lower():
            found.append((other, "therapist"))
        elif other.contact_id == contact_id:
            found.append((other, "patient"))
    return found


def conflict_message(conflicts: list[tuple[Appointment, str]]) -> str:
    kinds = {kind for _, kind in conflicts}
    if "therapist" in kinds:
        return "Ese terapeuta ya tiene una cita en ese horario."
    return "Este paciente ya tiene otra cita en ese horario."


def therapist_names(db: Session, tenant: Tenant, *, extra: list[str] | None = None) -> list[str]:
    """Active therapists from the table, plus any names used by appointments
    (so an old or typo'd name never makes an appointment vanish from the
    agenda), sorted. `extra` are names found in the range being shown."""
    names = {t.name for t in db.query(Therapist).filter(Therapist.tenant_id == tenant.id, Therapist.is_active.is_(True))}
    names.update(extra or [])
    return sorted(names, key=str.lower)


def seed_therapists(db: Session, tenant: Tenant) -> None:
    existing = {t.name for t in db.query(Therapist).filter(Therapist.tenant_id == tenant.id)}
    for name in DEFAULT_THERAPISTS:
        if name not in existing:
            db.add(Therapist(tenant_id=tenant.id, name=name))