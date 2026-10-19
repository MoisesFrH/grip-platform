 
import pytest
 
from app.services.greeting import is_greeting
 
 
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