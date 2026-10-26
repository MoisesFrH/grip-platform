"""
Demo data: three FICTITIOUS patients, each with one filled-in registration
form (Patronato, SeNaSa, Privado) so the CRM can be shown without real data.
Names and phones are invented (555 numbers). Safe to re-run: skips patients
that already exist.

It runs on every deploy (Procfile) but does nothing unless the Railway
variable SEED_DEMO_FORMS=1 is set — so it never touches a real database by
accident. Remove the variable (and the patients) when the demo is over.

Manual usage:
    SEED_DEMO_FORMS=1 python -m scripts.seed_demo_forms
"""

import os

from app.core.db import SessionLocal
from app.models.contact import Contact
from app.models.registration import RegistrationForm
from app.models.tenant import Tenant
from app.services import registration_forms as forms

COMMON = {
    "consent_given": "Sí", "consent_date": "2026-10-20", "nationality": "Dominicana", "address": "Calle Duarte 12, Moca",
    "marriage": "No", "free_union": "Sí", "separated": "No",
    "planned": "Sí", "pregnancy_type": "Normal", "previous_losses": "No", "complications": "No",
    "induced_or_emergency": "No", "weeks": "39", "birth_type": "Parto", "cried_immediately": "Sí",
    "late_cry": "No", "incubator": "No", "breathing": "No", "breastfed": "Sí", "formula_or_mixed": "No",
    "sleep": "Dormía bien", "neurologist": "No", "psych_referral": "Sí", "psych_center_visited": "No",
    "vaccines": "Sí", "prior_evals": "No", "language": ["Dificultad para pronunciar"],
}

DEMO = [
    {
        "phone": "+15550100001", "type": forms.SENASA, "name": "Sofía Martínez Peña",
        "answers": {
            "companion_name": "Carolina Peña", "companion_relation": "Madre",
            "child_name": "Sofía Martínez Peña", "birth_date": "2019-03-14", "age": "7", "sex": "Femenino",
            "school": "Colegio Evangélico Moca", "grade": "2.º de primaria",
            "consult_reason": "Le cuesta mantener la atención en clase y pronuncia mal varias palabras.",
            "mother_name": "Carolina Peña", "mother_age": "33", "mother_phone": "809-555-0101", "mother_job": "Maestra",
            "father_name": "Luis Martínez", "father_age": "36", "father_phone": "809-555-0102", "father_job": "Comerciante",
            "siblings": "Daniel, 11 años", "kindergarten": "Sí", "school_enrolled": "Sí",
            "first_words_months": "14", "head_months": "4", "crawl_months": "9", "sphincter_years": "3",
            "behavior": ["Hiperactividad", "Dificultad para socializar"],
            "academic": ["Atención", "Concentración"], "academic_notes": "Se distrae con facilidad.",
            "dx_adhd": ["Presentación predominante inatenta"],
            "dx_communication": ["Trastorno fonológico"],
        },
    },
    {
        "phone": "+15550100002", "type": forms.PATRONATO, "name": "Mateo Rodríguez Gil",
        "answers": {
            "consent_name": "Ana Gil",
            "child_name": "Mateo Rodríguez Gil", "birth_date": "2020-08-02", "age": "6", "sex": "Masculino",
            "school": "Escuela Primaria Los Pinos", "grade": "1.º de primaria",
            "consult_reason": "Casi no habla y le cuesta relacionarse con otros niños.",
            "mother_name": "Ana Gil", "mother_age": "30", "mother_phone": "809-555-0201", "mother_job": "Enfermera",
            "father_name": "Pedro Rodríguez", "father_age": "34", "father_phone": "809-555-0202", "father_job": "Mecánico",
            "siblings": "Ninguno", "daycare": "Sí", "daycare_name": "Guardería Mis Primeros Pasos",
            "first_words_months": "24", "first_words": "mamá, agua", "head_months": "5", "sphincter_years": "4",
            "language": ["No habla / habla poco"], "language_comment": "Se comunica señalando.",
            "behavior": ["Timidez extrema", "Dificultad para socializar"],
            "academic": "Necesita apoyo para seguir instrucciones.",
            "dx_asd": "Pendiente de evaluación", "dx_communication": "Trastorno del lenguaje (por confirmar)",
        },
    },
    {
        "phone": "+15550100003", "type": forms.DIRECT, "name": "Valentina Núñez Cruz",
        "answers": {
            "companion_name": "Rosa Cruz", "companion_relation": "Abuela",
            "child_name": "Valentina Núñez Cruz", "birth_date": "2017-11-25", "age": "8", "sex": "Femenino",
            "school": "Colegio San José", "grade": "3.º de primaria",
            "consult_reason": "Dificultad para leer y escribir; se frustra con las tareas.",
            "mother_name": "Marisol Cruz", "mother_age": "35", "mother_phone": "809-555-0301", "mother_job": "Contadora",
            "father_name": "Jorge Núñez", "father_age": "38", "father_job": "Ingeniero",
            "separated": "Sí", "marriage": "No", "free_union": "No",
            "separation_detail": "Separados hace 2 años; la niña vive con la madre.",
            "siblings": "Camila, 5 años", "kindergarten": "Sí", "school_enrolled": "Sí",
            "behavior": ["Irritabilidad"], "academic": ["Dificultad en lectura", "Dificultad en escritura"],
            "dx_learning": ["Con dificultad en la lectura (dislexia)", "Con dificultad en la expresión escrita (disgrafía)"],
        },
    },
]


def main() -> None:
    if os.environ.get("SEED_DEMO_FORMS") != "1":
        print("seed_demo_forms: SEED_DEMO_FORMS no está en 1, no se crea nada.")
        return
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.slug == "grip").one()
        for item in DEMO:
            contact = db.query(Contact).filter(Contact.tenant_id == tenant.id, Contact.phone == item["phone"]).one_or_none()
            if contact is not None:
                print(f"ya existe: {item['name']}")
                continue
            contact = Contact(tenant_id=tenant.id, phone=item["phone"], name=item["name"])
            db.add(contact)
            db.flush()
            answers = forms.clean_answers(item["type"], {**COMMON, **item["answers"]})
            db.add(RegistrationForm(
                tenant_id=tenant.id, contact_id=contact.id, form_type=item["type"],
                child_name=answers["child_name"], answers=answers, filled_by="Demo",
            ))
            print(f"creado: {item['name']} ({item['type']})")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()