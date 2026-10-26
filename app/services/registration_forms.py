"""
Field definitions of the three "Registro de consulta inicial infantil" forms.

One schema drives everything (the /registro page renders it, the CRM renders
saved answers from it, the API validates against it), so a wording change is a
one-line edit here. The three paper forms are the same questionnaire except:

  * PATRONATO  — "Patronato Sordomudo": no "who accompanies" block, and the
                 diagnoses section is four free-text questions.
  * SENASA     — "Programa Senasa Integra": diagnoses are tick-box lists.
  * DIRECT     — "Paciente privado": same as SENASA.

Field kinds: text, number, date, textarea, yesno, choice (single, `options`),
multi (several, `options`). Answers are stored as plain JSON by field key.
"""

SENASA = "SENASA"
PATRONATO = "PATRONATO"
DIRECT = "DIRECT"
FORM_TYPES = (PATRONATO, SENASA, DIRECT)

TITLES = {
    PATRONATO: "Registro de consulta inicial infantil — Patronato Sordomudo",
    SENASA: "Registro de consulta inicial infantil — Programa Senasa Integra",
    DIRECT: "Registro de consulta inicial infantil — Paciente privado",
}
SLUGS = {"patronato": PATRONATO, "senasa": SENASA, "privado": DIRECT, "directo": DIRECT}

# Maps the WhatsApp intake answer (Conversation.intake_data["payer"]) to a form.
PAYER_TO_FORM = {"SENASA": SENASA, "PATRONATO": PATRONATO, "DIRECT": DIRECT}


def _f(key, label, kind="text", **extra):
    return {"key": key, "label": label, "kind": kind, **extra}


def _sections(form_type: str) -> list[dict]:
    s = []

    # Consent is on the first page of every form; the companion block only on
    # SeNaSa / Privado (Patronato asks "madre/padre" in the consent line).
    s.append({"title": "Consentimiento informado", "fields": [
        _f("consent_name", "Quien acompaña, madre/padre (nombre)"),
        _f("consent_date", "Fecha", "date"),
        _f("consent_given", "Autorizo la consulta del menor", "yesno"),
    ]})
    if form_type != PATRONATO:
        s.append({"title": "Quien acompaña", "fields": [
            _f("companion_name", "Nombre"),
            _f("companion_relation", "Grado de familiaridad"),
        ]})

    s.append({"title": "Datos generales del niño/a", "fields": [
        _f("child_name", "Nombre completo", required=True),
        _f("birth_date", "Fecha de nacimiento", "date"),
        _f("age", "Edad", "number"),
        _f("sex", "Sexo", "choice", options=["Masculino", "Femenino"]),
        _f("nationality", "Nacionalidad"),
        _f("school", "Centro educativo"),
        _f("grade", "Grado/Curso"),
        _f("consult_reason", "Motivo de consulta (describa brevemente la razón)", "textarea", required=True),
    ]})

    s.append({"title": "Datos familiares", "fields": [
        _f("father_name", "Nombre del padre"), _f("father_age", "Edad del padre", "number"),
        _f("father_phone", "Teléfono del padre"), _f("father_job", "Ocupación del padre"),
        _f("mother_name", "Nombre de la madre"), _f("mother_age", "Edad de la madre", "number"),
        _f("mother_phone", "Teléfono de la madre"), _f("mother_job", "Ocupación de la madre"),
        _f("address", "Dirección"),
    ]})

    s.append({"title": "Padres", "fields": [
        _f("marriage", "Unión matrimonial", "yesno"),
        _f("free_union", "Unión libre", "yesno"),
        _f("separated", "Separados", "yesno"),
        _f("separation_detail", "Si es afirmativo: tiempo separados y con quién vive el menor", "textarea"),
    ]})

    s.append({"title": "Hermanos y procesos escolares", "fields": [
        _f("siblings", "Cantidad de hijos (hermanos): nombres y edades", "textarea"),
        _f("daycare", "Estuvo inscrito en guardería", "yesno"),
        *([_f("daycare_name", "Nombre de la guardería")] if form_type == PATRONATO else []),
        _f("kindergarten", "Estuvo inscrito en colegio", "yesno"),
        *([_f("kindergarten_name", "Nombre del colegio")] if form_type == PATRONATO else []),
        _f("school_enrolled", "Estuvo inscrito en escuela", "yesno"),
        *([_f("school_enrolled_name", "Nombre de la escuela")] if form_type == PATRONATO else []),
    ]})

    s.append({"title": "Antecedentes del embarazo", "fields": [
        _f("mother_age_pregnant", "Edad de la madre cuando salió embarazada", "number"),
        _f("planned", "El embarazo fue planificado", "yesno"),
        _f("pregnancy_type", "Este embarazo fue", "choice", options=["Normal", "Riesgoso", "Complicado", "De muy alto riesgo"]),
        _f("previous_losses", "Pérdidas de embarazo anteriores", "yesno"),
        _f("losses_count", "Cuántas pérdidas", "number"),
        _f("complications", "Complicaciones durante el embarazo", "yesno"),
        _f("pregnancy_meds", "Medicación indicada durante la gestación"),
    ]})

    s.append({"title": "Proceso de nacimiento", "fields": [
        _f("induced_or_emergency", "El embarazo fue inducido o de emergencia", "yesno"),
        _f("weeks", "Cantidad de semanas", "number"),
        _f("birth_type", "Tipo de nacimiento", "choice", options=["Cesárea", "Parto", "Inducido"]),
        _f("emergency_detail", "Si fue de emergencia, especifique"),
        _f("cried_immediately", "Al nacer hubo llanto inmediato", "yesno"),
        _f("late_cry", "Llanto tardío o necesitó ayuda", "yesno"),
        _f("birth_weight", "Peso al nacer"),
        _f("skin_color", "Coloración del niño/a al nacer, especifique"),
        _f("incubator", "Necesidad de incubadora", "yesno"),
        _f("breathing", "Dificultades respiratorias", "yesno"),
    ]})

    s.append({"title": "Alimentación inicial", "fields": [
        _f("breastfed", "Lactancia (seno)", "yesno"),
        _f("formula_or_mixed", "Fórmula (leche) o mixta", "yesno"),
        _f("formula_name", "¿Cuál fórmula?"),
    ]})

    s.append({"title": "Hitos del desarrollo", "fields": [
        _f("sleep", "Sueño", "choice", options=["Dormía bien", "Mal", "Se levantaba mucho"]),
        _f("head_months", "Sostén cefálico (meses)", "number"),
        _f("crawl_months", "Gateó (meses)", "number"),
        _f("babble_months", "Balbuceo (meses)", "number"),
        _f("first_words_months", "Primeras palabras (meses)", "number"),
        _f("first_words", "Cuáles fueron las primeras palabras"),
        _f("sphincter_years", "Control de esfínteres (años)", "number"),
        _f("last_pediatric", "Última visita pediátrica"),
        _f("neurologist", "Ha visitado al neurólogo pediátrico", "yesno"),
        _f("neurologist_name", "Nombre del especialista"),
        _f("health_center", "Centro de salud"),
        _f("psych_referral", "Le indicaron estudios o derivaciones psicológicas", "yesno"),
        _f("psych_center_visited", "Ha asistido a centros de psicología", "yesno"),
        _f("psych_center_name", "Nombre del centro"),
        _f("therapist_specialty", "Especialidad del terapeuta"),
        _f("which_therapies", "A cuáles"),
        _f("therapies_done", "Terapias realizadas"),
        _f("vaccines", "Récord de vacunas completo", "yesno"),
        _f("studies_done", "Si fueron realizados estudios, cuáles", "textarea"),
    ]})

    s.append({"title": "Antecedentes médicos", "fields": [
        _f("diseases", "Enfermedades relevantes"),
        _f("medication", "Medicación actual"),
        _f("prior_evals", "Evaluaciones previas realizadas", "yesno"),
        _f("prior_evals_detail", "Especifique"),
    ]})

    s.append({"title": "Dificultades cognitivas — Lenguaje y comunicación", "fields": [
        _f("language", "Lenguaje", "multi", options=["Habla claramente", "Dificultad para pronunciar", "No habla / habla poco"]),
        _f("language_since", "Tiempo estimado en que se produjo"),
        _f("language_comment", "Comentario"),
    ]})

    s.append({"title": "Área conductual y emocional", "fields": [
        _f("behavior", "Conducta", "multi", options=[
            "Irritabilidad", "Conductas destructivas (dañar objetos)", "Hiperactividad",
            "Timidez extrema", "Dificultad para socializar"]),
        _f("behavior_1_2y", "Entre 1-2 años, cómo era el comportamiento en las áreas de desarrollo conductual", "textarea"),
    ]})

    if form_type == PATRONATO:
        s.append({"title": "Diagnósticos", "fields": [
            _f("academic", "Área académica (en la escuela, cómo le va)", "textarea"),
            _f("dx_intellectual", "Discapacidad intelectual. ¿Cuál?"),
            _f("dx_communication", "Trastornos de la comunicación. ¿Cuál?"),
            _f("dx_asd", "Trastorno del espectro autista (TEA). ¿Qué grado?"),
            _f("dx_adhd", "Trastorno por déficit de atención e hiperactividad (TDAH). ¿Cuál?"),
        ]})
    else:
        s.append({"title": "Área académica", "fields": [
            _f("academic", "En la escuela, cómo le va", "multi", options=[
                "Dificultad en lectura", "Dificultad en escritura", "Dificultad en matemáticas",
                "Concentración", "Atención", "Dificultad en otras áreas"]),
            _f("academic_notes", "Observaciones", "textarea"),
        ]})
        s.append({"title": "Marque en la que su hijo/a se encuentra", "fields": [
            _f("dx_intellectual", "Discapacidad intelectual", "multi", options=["Leve", "Moderada", "Severa", "Profunda"]),
            _f("dx_communication", "Trastornos de la comunicación", "multi", options=[
                "Trastorno del lenguaje", "Trastorno fonológico", "Trastorno de la fluidez (tartamudez)",
                "Trastorno de la comunicación social (pragmático)"]),
            _f("dx_asd", "Trastorno del espectro autista (TEA)", "multi", options=["Grado 1", "Grado 2", "Grado 3"]),
            _f("dx_adhd", "TDAH", "multi", options=[
                "Presentación predominante inatenta",
                "Presentación predominante hiperactiva/impulsiva o combinada"]),
            _f("dx_learning", "Trastornos específicos del aprendizaje", "multi", options=[
                "Con dificultad en la lectura (dislexia)", "Con dificultad en la expresión escrita (disgrafía)",
                "Con dificultad en las matemáticas (discalculia)", "Con dificultad en otras áreas"]),
            _f("dx_motor", "Trastornos motores", "multi", options=[
                "Trastorno del desarrollo de la coordinación", "Trastorno de movimientos estereotipados"]),
            _f("dx_tics", "Trastornos por tics", "multi", options=[
                "Trastorno de tics motores", "Trastorno de tics vocales",
                "Trastorno de tics motores y vocales (síndrome de Tourette)"]),
        ]})
    return s


def get_schema(form_type: str) -> dict:
    if form_type not in FORM_TYPES:
        raise KeyError(form_type)
    return {"form_type": form_type, "title": TITLES[form_type], "sections": _sections(form_type)}


def field_index(form_type: str) -> dict[str, dict]:
    return {f["key"]: f for sec in _sections(form_type) for f in sec["fields"]}


def clean_answers(form_type: str, raw: dict) -> dict:
    """Keeps only known keys, coerces to the field kind, drops blanks. Raises
    ValueError on a bad value so the API answers 422 instead of storing junk."""
    fields = field_index(form_type)
    out: dict = {}
    for key, value in (raw or {}).items():
        spec = fields.get(key)
        if spec is None or value in (None, "", []):
            continue
        kind = spec["kind"]
        if kind == "yesno":
            if value not in ("Sí", "No"):
                raise ValueError(f"{spec['label']}: responde Sí o No.")
        elif kind == "choice":
            if value not in spec["options"]:
                raise ValueError(f"{spec['label']}: opción no válida.")
        elif kind == "multi":
            if not isinstance(value, list) or any(v not in spec["options"] for v in value):
                raise ValueError(f"{spec['label']}: opción no válida.")
        elif kind == "number":
            try:
                float(str(value).replace(",", "."))
            except ValueError:
                raise ValueError(f"{spec['label']}: debe ser un número.")
            value = str(value).strip()
        else:
            value = str(value).strip()[:2000]
            if not value:
                continue
        out[key] = value
    for key, spec in fields.items():
        if spec.get("required") and key not in out:
            raise ValueError(f"Falta: {spec['label']}.")
    return out