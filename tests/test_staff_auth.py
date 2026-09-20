"""
Tests for the /crm authentication and role-gating logic — the actual
security boundary for clinical data, so this gets the same "prove it
against the real thing" treatment as the reminder-reply guard.

No httpx/TestClient here on purpose: this sandbox doesn't have httpx
installed, and app/services/auth.py is deliberately stdlib-only rather
than pulling in a new dependency for something this small, so the tests
follow the same philosophy — they call the route/dependency functions
directly (FastAPI only wires up Depends() at real request time; calling
the plain Python functions with real arguments works exactly the same
way) against the real Postgres database, the same pattern the rest of
this test suite already uses.

Run with:
    .venv/bin/python -m tests.test_staff_auth
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.api.routes.crm import (
    BootstrapStaffRequest,
    LoginRequest,
    bootstrap_staff,
    create_note_route,
    get_current_staff,
    login,
    logout,
    require_doctor,
)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.staff import Staff, StaffRole, StaffSession
from app.models.tenant import Tenant
from app.services import auth


class FakeRequest:
    """Duck-types the one attribute get_current_staff/logout actually use."""

    def __init__(self, cookies: dict | None = None):
        self.cookies = cookies or {}


class FakeResponse:
    """Duck-types Response.set_cookie/delete_cookie so we can inspect what
    a route tried to send back without going through a real ASGI call."""

    def __init__(self):
        self.cookies_set: list[dict] = []
        self.cookies_deleted: list[str] = []

    def set_cookie(self, key, value, **kwargs):
        self.cookies_set.append({"key": key, "value": value, **kwargs})

    def delete_cookie(self, key):
        self.cookies_deleted.append(key)


def _grip() -> Tenant:
    db = SessionLocal()
    tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
    db.close()
    return tenant


def _make_staff(db, tenant, role: StaffRole, *, is_active: bool = True, password: str = "correcthorse123") -> Staff:
    username = f"test_{uuid.uuid4().hex[:10]}"
    staff = Staff(
        tenant_id=tenant.id,
        username=username,
        display_name="Personal de Prueba",
        role=role,
        password_hash=auth.hash_password(password),
        is_active=is_active,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def _cleanup(db, staff: Staff) -> None:
    db.query(StaffSession).filter(StaffSession.staff_id == staff.id).delete()
    db.query(Staff).filter(Staff.id == staff.id).delete()
    db.commit()


def test_password_hash_roundtrip_and_rejects_wrong_password():
    hashed = auth.hash_password("mi-clave-segura")
    assert auth.verify_password("mi-clave-segura", hashed) is True
    assert auth.verify_password("clave-incorrecta", hashed) is False
    # Garbage stored hashes (or None-like corruption) must fail closed, not raise.
    assert auth.verify_password("cualquier-cosa", "no-es-un-hash-valido") is False


def test_login_succeeds_and_sets_httponly_cookie():
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY, password="clave-correcta-123")
    try:
        response = FakeResponse()
        result = login(
            LoginRequest(tenant_slug="grip", username=staff.username, password="clave-correcta-123"),
            response,
            db,
        )
        assert result.role == "SECRETARY"
        assert len(response.cookies_set) == 1
        cookie = response.cookies_set[0]
        assert cookie["key"] == auth.SESSION_COOKIE_NAME
        assert cookie["httponly"] is True
        # Exactly one session row was created, and only its hash is stored —
        # the raw cookie value never appears verbatim in the database.
        sessions = db.query(StaffSession).filter(StaffSession.staff_id == staff.id).all()
        assert len(sessions) == 1
        assert sessions[0].token_hash != cookie["value"]
        assert sessions[0].token_hash == auth.hash_session_token(cookie["value"])
    finally:
        _cleanup(db, staff)
        db.close()


def test_login_rejects_wrong_password_with_generic_error():
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY, password="clave-correcta-123")
    try:
        try:
            login(LoginRequest(tenant_slug="grip", username=staff.username, password="incorrecta"), FakeResponse(), db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 401
        # No session should have been created on a failed login.
        assert db.query(StaffSession).filter(StaffSession.staff_id == staff.id).count() == 0
    finally:
        _cleanup(db, staff)
        db.close()


def test_login_rejects_deactivated_account():
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.DOCTOR, is_active=False, password="clave-correcta-123")
    try:
        try:
            login(LoginRequest(tenant_slug="grip", username=staff.username, password="clave-correcta-123"), FakeResponse(), db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 401
    finally:
        _cleanup(db, staff)
        db.close()


def test_login_rejects_unknown_tenant_slug_without_leaking_which_field_was_wrong():
    db = SessionLocal()
    try:
        try:
            login(LoginRequest(tenant_slug="tenant-que-no-existe", username="cualquiera", password="loquesea"), FakeResponse(), db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 401
            assert exc.detail == "Usuario o contraseña incorrectos."
    finally:
        db.close()


def test_get_current_staff_requires_cookie():
    db = SessionLocal()
    try:
        try:
            get_current_staff(FakeRequest(cookies={}), db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 401
    finally:
        db.close()


def test_get_current_staff_rejects_expired_session():
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY)
    token = auth.new_session_token()
    session = StaffSession(
        staff_id=staff.id,
        token_hash=auth.hash_session_token(token),
        created_at=datetime.now(timezone.utc) - timedelta(hours=13),
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # already expired
    )
    db.add(session)
    db.commit()
    try:
        try:
            get_current_staff(FakeRequest(cookies={auth.SESSION_COOKIE_NAME: token}), db)
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 401
    finally:
        _cleanup(db, staff)
        db.close()


def test_logout_revokes_session_immediately():
    """The whole point of server-side sessions over a self-verifying token:
    logout must take effect immediately, not "eventually when it expires"."""
    tenant = _grip()
    db = SessionLocal()
    staff = _make_staff(db, tenant, StaffRole.SECRETARY)
    token = auth.new_session_token()
    db.add(
        StaffSession(
            staff_id=staff.id,
            token_hash=auth.hash_session_token(token),
            created_at=datetime.now(timezone.utc),
            expires_at=auth.new_session_expiry(),
        )
    )
    db.commit()
    try:
        request = FakeRequest(cookies={auth.SESSION_COOKIE_NAME: token})
        # Works before logout.
        staff_out = get_current_staff(request, db)
        assert staff_out.id == staff.id

        logout(request, FakeResponse(), db)

        try:
            get_current_staff(request, db)
            assert False, "expected HTTPException after logout"
        except HTTPException as exc:
            assert exc.status_code == 401
    finally:
        _cleanup(db, staff)
        db.close()


def test_require_doctor_blocks_secretary_and_allows_doctor():
    tenant = _grip()
    db = SessionLocal()
    secretary = _make_staff(db, tenant, StaffRole.SECRETARY)
    doctor = _make_staff(db, tenant, StaffRole.DOCTOR)
    try:
        try:
            require_doctor(secretary)
            assert False, "expected HTTPException for a secretary"
        except HTTPException as exc:
            assert exc.status_code == 403

        # Should not raise for a doctor, and should return the same staff member.
        assert require_doctor(doctor).id == doctor.id
    finally:
        _cleanup(db, secretary)
        _cleanup(db, doctor)
        db.close()


def test_create_note_route_is_unreachable_without_require_doctor():
    """Belt-and-suspenders: even if a future refactor let a secretary reach
    create_note_route's *body*, the dependency itself is what FastAPI runs
    first — this pins that require_doctor (not the route body) is the gate."""
    import inspect

    sig = inspect.signature(create_note_route)
    staff_param = sig.parameters["staff"]
    assert staff_param.default.dependency is require_doctor


# --- POST /crm/bootstrap-staff ----------------------------------------------
#
# This endpoint exists so a client without a local Python/dependency setup
# (e.g. on Windows, deploying only through Railway) can create their first
# staff accounts through a web form instead of scripts/create_staff.py. It
# must fail closed: disabled unless STAFF_BOOTSTRAP_SECRET is set, and a
# wrong secret gets the same 404 as "disabled" rather than a 401/403 that
# would confirm the endpoint exists.


def _set_bootstrap_secret(value: str) -> str:
    """Returns the previous value so the test can restore it — settings are
    a process-wide lru_cache singleton, so tests must clean up after
    themselves or they'll bleed into whichever test runs next."""
    settings = get_settings()
    previous = settings.staff_bootstrap_secret
    settings.staff_bootstrap_secret = value
    return previous


def test_bootstrap_staff_disabled_by_default_returns_404():
    previous = _set_bootstrap_secret("")
    db = SessionLocal()
    try:
        try:
            bootstrap_staff(
                BootstrapStaffRequest(secret="anything", username="x", display_name="X", role="doctor", password="clave-correcta-123"),
                db,
            )
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404
    finally:
        _set_bootstrap_secret(previous)
        db.close()


def test_bootstrap_staff_rejects_wrong_secret_with_same_404():
    previous = _set_bootstrap_secret("correct-secret-value")
    db = SessionLocal()
    try:
        try:
            bootstrap_staff(
                BootstrapStaffRequest(secret="wrong-secret", username="x", display_name="X", role="doctor", password="clave-correcta-123"),
                db,
            )
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 404
    finally:
        _set_bootstrap_secret(previous)
        db.close()


def test_bootstrap_staff_creates_and_then_updates_account():
    tenant = _grip()
    previous = _set_bootstrap_secret("correct-secret-value")
    db = SessionLocal()
    username = f"bootstrap_{uuid.uuid4().hex[:10]}"
    try:
        created = bootstrap_staff(
            BootstrapStaffRequest(
                secret="correct-secret-value", username=username, display_name="Nueva Persona", role="secretary", password="clave-correcta-123"
            ),
            db,
        )
        assert created.role == "SECRETARY"

        staff_row = db.query(Staff).filter(Staff.tenant_id == tenant.id, Staff.username == username).one()
        assert auth.verify_password("clave-correcta-123", staff_row.password_hash)

        # Re-running with a new role/password/display name updates the same
        # row in place (safe to re-run), same as scripts/create_staff.py.
        updated = bootstrap_staff(
            BootstrapStaffRequest(
                secret="correct-secret-value", username=username, display_name="Nombre Actualizado", role="doctor", password="otra-clave-valida"
            ),
            db,
        )
        assert updated.role == "DOCTOR"
        db.refresh(staff_row)
        assert staff_row.display_name == "Nombre Actualizado"
        assert staff_row.role == StaffRole.DOCTOR
        assert auth.verify_password("otra-clave-valida", staff_row.password_hash)
        assert not auth.verify_password("clave-correcta-123", staff_row.password_hash)
    finally:
        _set_bootstrap_secret(previous)
        _cleanup(db, db.query(Staff).filter(Staff.tenant_id == tenant.id, Staff.username == username).one())
        db.close()


def test_bootstrap_staff_rejects_short_password():
    previous = _set_bootstrap_secret("correct-secret-value")
    db = SessionLocal()
    try:
        try:
            bootstrap_staff(
                BootstrapStaffRequest(secret="correct-secret-value", username="x", display_name="X", role="doctor", password="corta"),
                db,
            )
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 400
    finally:
        _set_bootstrap_secret(previous)
        db.close()


def test_bootstrap_staff_rejects_invalid_role():
    previous = _set_bootstrap_secret("correct-secret-value")
    db = SessionLocal()
    try:
        try:
            bootstrap_staff(
                BootstrapStaffRequest(secret="correct-secret-value", username="x", display_name="X", role="admin", password="clave-correcta-123"),
                db,
            )
            assert False, "expected HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 400
    finally:
        _set_bootstrap_secret(previous)
        db.close()