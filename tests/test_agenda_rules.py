"""
Opening hours, overlaps, payment amounts, CSV safety and report totals. No
database needed. Run with:

    .venv/bin/python -m pytest tests/test_agenda_rules.py
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import agenda, payments


def _tenant(config=None):
    return SimpleNamespace(config=config or {})


def _utc(year, month, day, hour, minute=0):
    """A clinic-local wall time (UTC-4) as a UTC datetime."""
    return datetime(year, month, day, hour + 4, minute, tzinfo=timezone.utc)


# 2026-10-26 is a Monday, 2026-10-31 a Saturday, 2026-11-01 a Sunday.


def test_weekday_hours() -> None:
    t = _tenant()
    assert agenda.slot_error(t, _utc(2026, 10, 26, 9)) is None
    assert agenda.slot_error(t, _utc(2026, 10, 26, 17)) is None  # 17-18 fits
    assert agenda.slot_error(t, _utc(2026, 10, 26, 18)) is not None  # closes at 18
    assert agenda.slot_error(t, _utc(2026, 10, 26, 8)) is not None  # opens at 9


def test_saturday_and_sunday() -> None:
    t = _tenant()
    assert agenda.slot_error(t, _utc(2026, 10, 31, 8)) is None
    assert agenda.slot_error(t, _utc(2026, 10, 31, 12)) is None  # 12-13 fits
    assert agenda.slot_error(t, _utc(2026, 10, 31, 13)) is not None
    assert "no atiende" in agenda.slot_error(t, _utc(2026, 11, 1, 10))


def test_half_hour_start_must_still_fit() -> None:
    t = _tenant()
    assert agenda.slot_error(t, _utc(2026, 10, 26, 17, 30)) is not None  # 17:30-18:30 spills over
    assert agenda.slot_error(t, _utc(2026, 10, 26, 9, 30)) is None


def test_tenant_can_override_hours() -> None:
    t = _tenant({"agenda": {"hours": {"0": [10, 12]}}})
    assert agenda.slot_error(t, _utc(2026, 10, 26, 10)) is None
    assert agenda.slot_error(t, _utc(2026, 10, 26, 9)) is not None
    assert agenda.slot_error(t, _utc(2026, 10, 27, 10)) is not None  # Tuesday not configured -> closed


def test_overlaps() -> None:
    a = _utc(2026, 10, 26, 10)
    assert agenda.overlaps(a, 60, _utc(2026, 10, 26, 10, 30), 60)
    assert agenda.overlaps(a, 60, _utc(2026, 10, 26, 9, 30), 60)
    assert not agenda.overlaps(a, 60, _utc(2026, 10, 26, 11), 60)  # back to back is fine
    assert not agenda.overlaps(a, 60, _utc(2026, 10, 26, 9), 60)


def test_conflict_message_prefers_therapist() -> None:
    a = SimpleNamespace()
    assert "terapeuta" in agenda.conflict_message([(a, "patient"), (a, "therapist")])
    assert "paciente" in agenda.conflict_message([(a, "patient")])


def test_parse_amount() -> None:
    assert payments.parse_amount("1500") == Decimal("1500.00")
    assert payments.parse_amount(1500.5) == Decimal("1500.50")
    assert payments.parse_amount("") is None and payments.parse_amount(None) is None
    for bad in ("abc", -5, "-1"):
        with pytest.raises(payments.PaymentError):
            payments.parse_amount(bad)


def test_month_bounds() -> None:
    start, end = payments.month_bounds_utc("2026-10")
    assert start == datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
    start, end = payments.month_bounds_utc("2026-12")
    assert end == datetime(2027, 1, 1, 4, tzinfo=timezone.utc)
    for bad in ("2026-13", "26-10", "", "octubre"):
        with pytest.raises(payments.PaymentError):
            payments.month_bounds_utc(bad)


def _row(status, amount, method="TRANSFER", therapist="Ana", patient="Paciente"):
    now = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)
    pay = SimpleNamespace(status=status, amount=Decimal(amount) if amount is not None else None, method=method,
                          reference=None, verified_by="Sec", verified_at=now)
    return {"payment": pay, "appointment": SimpleNamespace(therapist_name=therapist, scheduled_at=now), "patient": patient}


def test_report_counts_only_verified_as_collected_and_flags_missing_amounts() -> None:
    rows = [
        _row("VERIFIED", "1000", "TRANSFER", "Ana"),
        _row("VERIFIED", "500", "CASH", "Beto"),
        _row("REPORTED", "700"),
        _row("PENDING", None),
        _row("REJECTED", "300"),
    ]
    report = payments.build_report(rows)
    assert report["by_status"]["VERIFIED"] == {"count": 2, "amount": Decimal("1500.00")}
    assert report["by_status"]["REPORTED"]["amount"] == Decimal("700.00")
    assert report["verified_by_method"]["CASH"]["amount"] == Decimal("500.00")
    assert report["verified_by_therapist"]["Ana"]["count"] == 1
    assert report["without_amount"] == 1
    assert "REJECTED" not in report["verified_by_method"]


def test_csv_has_header_rows_and_blocks_formula_injection() -> None:
    out = payments.to_csv([_row("VERIFIED", "1000", patient="=HYPERLINK(\"http://x\")")])
    lines = out.strip().splitlines()
    assert lines[0].startswith("Fecha,Hora,Paciente")
    assert "'=HYPERLINK" in lines[1]
    assert "1000.00" in lines[1] and "Verificado" in lines[1]