"""
Welcome message for a bare greeting ("hola", "buenas tardes", ...).
 
Only a message that is *entirely* a greeting counts: "hola quiero una cita"
is not a bare greeting and goes through normal classification. Deterministic,
no LLM, and it never replaces the safety check (a message with any other
word is not a bare greeting, so safety still sees everything that matters).
"""
 
import re
 
from app.models.tenant import Tenant
from app.services.text_utils import normalize
 
_STARTERS = {"hola", "ola", "buenas", "buenos", "buen", "hey", "saludos", "hello", "hi", "alo"}
_FILLERS = {"dias", "dia", "tardes", "noches", "que", "tal", "como", "estas", "estan",
            "esta", "todos", "a", "de", "tarde", "noche", "equipo", "nd", "neurodiversa"}
 
 
def is_greeting(text: str) -> bool:
    if not text or not text.strip():
        return False
    cleaned = re.sub(r"[^a-z\s]", " ", normalize(text))
    cleaned = re.sub(r"(.)\1{2,}", r"\1", cleaned)  # "holaaa" -> "hola"
    tokens = cleaned.split()
    if not tokens or len(tokens) > 6:
        return False
    return tokens[0] in _STARTERS and all(t in _STARTERS or t in _FILLERS for t in tokens)
 
 
def welcome_message(tenant: Tenant) -> str:
    return (
        f"¡Hola! Te damos la bienvenida a {tenant.name} 💙 Soy el asistente virtual.\n\n"
        "¿En qué te puedo ayudar? Puedo contarte sobre nuestros servicios, dónde "
        "estamos o ayudarte a solicitar una cita."
    )