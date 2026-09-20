"""
Creates or updates a GRIP staff account (secretary or doctor) so they can
log into the internal CRM screen at /crm.

Deliberately a script, not a signup screen: the client (Dome) asked for
staff accounts to be created this way rather than self-service, since the
list of who works at GRIP and what role they hold is short and changes
rarely, and a signup screen would be one more thing an attacker could try
against.

Usage (interactive — asks for the password without echoing it):

    .venv/bin/python -m scripts.create_staff --username maria --name "María Fernanda Ruiz" --role doctor

    .venv/bin/python -m scripts.create_staff --username ana --name "Ana Pérez" --role secretary

Flags:
    --username   login username, unique per tenant (e.g. "maria")
    --name       display name shown in the CRM UI (e.g. "María Fernanda Ruiz")
    --role       "secretary" or "doctor"
    --tenant     tenant slug (default: "grip")
    --deactivate instead of creating/updating, deactivate this username
                 (keeps the row, and its clinical-note authorship history,
                 but blocks login immediately)

Safe to re-run: if the username already exists for the tenant, this
updates their display name, role and password (and reactivates them)
rather than failing.
"""

import argparse
import getpass
import sys

from app.core.db import SessionLocal
from app.models.staff import Staff, StaffRole
from app.models.tenant import Tenant
from app.services import auth


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update a GRIP staff account.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--name", dest="display_name")
    parser.add_argument("--role", choices=["secretary", "doctor"])
    parser.add_argument("--tenant", default="grip")
    parser.add_argument("--deactivate", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == args.tenant).one_or_none()
        if tenant is None:
            print(f"No existe un tenant con slug '{args.tenant}'. Corre primero scripts/seed_grip.py.")
            sys.exit(1)

        staff = (
            db.query(Staff)
            .filter(Staff.tenant_id == tenant.id, Staff.username == args.username)
            .one_or_none()
        )

        if args.deactivate:
            if staff is None:
                print(f"No existe la cuenta '{args.username}' para el tenant '{args.tenant}'.")
                sys.exit(1)
            staff.is_active = False
            db.commit()
            print(f"Cuenta '{args.username}' desactivada. No podrá iniciar sesión hasta reactivarla.")
            return

        if not args.display_name or not args.role:
            print("--name y --role son obligatorios para crear o actualizar una cuenta (omítelos solo con --deactivate).")
            sys.exit(1)

        password = getpass.getpass(f"Contraseña para '{args.username}': ")
        confirm = getpass.getpass("Confírmala: ")
        if password != confirm:
            print("Las contraseñas no coinciden.")
            sys.exit(1)
        if len(password) < 8:
            print("Usa una contraseña de al menos 8 caracteres.")
            sys.exit(1)

        role = StaffRole.DOCTOR if args.role == "doctor" else StaffRole.SECRETARY
        password_hash = auth.hash_password(password)

        if staff is None:
            staff = Staff(
                tenant_id=tenant.id,
                username=args.username,
                display_name=args.display_name,
                role=role,
                password_hash=password_hash,
                is_active=True,
            )
            db.add(staff)
            db.commit()
            print(f"Cuenta creada: '{args.username}' ({args.display_name}), rol {role.value}.")
        else:
            staff.display_name = args.display_name
            staff.role = role
            staff.password_hash = password_hash
            staff.is_active = True
            db.commit()
            print(f"Cuenta actualizada: '{args.username}' ({args.display_name}), rol {role.value}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()