"""Bare greetings get a welcome; anything with real content does not."""

import pytest

from types import SimpleNamespace

from app.services.greeting import first_name, is_greeting, welcome_message


@pytest.mark.parametrize("text", [
    "Hola", "hola!", "Holaaa", "buenas", "Buenos días", "buenas tardes", "Buenas noches!",
    "hola buenas tardes", "Hola, qué tal", "hey", "Saludos", "hola como estas",
])
def test_bare_greetings(text):
    assert is_greeting(text)


@pytest.mark.parametrize("text", [
    "", "   ", "hola quiero una cita", "hola, ¿dónde quedan?", "buenas, no quiero vivir",
    "quiero terapia", "1", "Juan Pérez", "hola necesito hablar con una persona",
])
def test_not_greetings(text):
    assert not is_greeting(text)


TENANT = SimpleNamespace(name="ND Neurodiversa")


def test_first_name() -> None:
    assert first_name("ana maría pérez") == "Ana"
    assert first_name("María Fernanda") == "María"
    assert first_name("  ") is None and first_name(None) is None


def test_welcome_without_name_is_the_generic_one() -> None:
    text = welcome_message(TENANT)
    assert "Te damos la bienvenida" in text and "Hola," not in text


def test_welcome_with_name_is_personal() -> None:
    text = welcome_message(TENANT, name="Ana Pérez")
    assert text.startswith("¡Hola, Ana!") and "ND Neurodiversa" in text and "próxima cita" not in text


def test_welcome_mentions_next_appointment() -> None:
    text = welcome_message(TENANT, name="Ana", next_appointment="mañana a las 3:00 p. m. con Esther Jimenez")
    assert "tu próxima cita es mañana a las 3:00 p. m. con Esther Jimenez." in text