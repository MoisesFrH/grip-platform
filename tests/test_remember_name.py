"""The name given in new-patient registration is remembered on the contact.

    .venv/bin/python -m pytest tests/test_remember_name.py
"""

from types import SimpleNamespace

from app.services.orchestrator import _remember_name


def _conv(name, contact_name=None):
    return SimpleNamespace(intake_data={"patient_name": name} if name is not None else None,
                           contact=SimpleNamespace(name=contact_name))


def test_saves_name_when_contact_has_none() -> None:
    c = _conv("  Moisés Frías Hiciano ")
    _remember_name(c)
    assert c.contact.name == "Moisés Frías Hiciano"


def test_never_overwrites_a_name_staff_typed() -> None:
    c = _conv("Otro Nombre", contact_name="Ana Pérez")
    _remember_name(c)
    assert c.contact.name == "Ana Pérez"


def test_no_intake_data_or_empty_name_changes_nothing() -> None:
    for c in (_conv(None), _conv(""), _conv("   ")):
        _remember_name(c)
        assert c.contact.name is None