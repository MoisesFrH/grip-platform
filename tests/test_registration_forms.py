import pytest

from app.services import registration_forms as forms


def test_three_schemas_and_differences():
    for t in forms.FORM_TYPES:
        assert forms.get_schema(t)["sections"]
    assert "companion_name" not in forms.field_index(forms.PATRONATO)
    assert "companion_name" in forms.field_index(forms.SENASA)
    assert forms.field_index(forms.PATRONATO)["dx_asd"]["kind"] == "text"
    assert forms.field_index(forms.DIRECT)["dx_asd"]["kind"] == "multi"


def test_clean_answers_drops_unknown_and_blank():
    out = forms.clean_answers(forms.SENASA, {"child_name": " Ana ", "consult_reason": "x", "bogus": "1", "age": ""})
    assert out == {"child_name": "Ana", "consult_reason": "x"}


@pytest.mark.parametrize("bad", [
    {"child_name": "A", "consult_reason": "x", "planned": "Quizás"},
    {"child_name": "A", "consult_reason": "x", "age": "abc"},
    {"child_name": "A", "consult_reason": "x", "dx_asd": ["Grado 9"]},
    {"consult_reason": "x"},
])
def test_clean_answers_rejects_bad(bad):
    with pytest.raises(ValueError):
        forms.clean_answers(forms.SENASA, bad)


def test_demo_data_is_valid():
    from scripts.seed_demo_forms import COMMON, DEMO
    for item in DEMO:
        forms.clean_answers(item["type"], {**COMMON, **item["answers"]})