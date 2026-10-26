import pytest

from app.services.orchestrator import _promises_handoff


@pytest.mark.parametrize("text", [
    "No tengo ese dato ahora mismo. Te conecto con el equipo para que puedan dar la información.",
    "Te paso con una persona del equipo.",
    "Lo ideal es que te derivemos... te derivo con administración.",
])
def test_promise_detected(text):
    assert _promises_handoff(text)


@pytest.mark.parametrize("text", [
    "Estamos en C/ Antonio Pasica 16, Moca.",
    "",
    None,
])
def test_no_promise(text):
    assert not _promises_handoff(text)