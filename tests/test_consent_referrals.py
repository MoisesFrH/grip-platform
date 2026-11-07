import base64
from types import SimpleNamespace

import pytest

from app.services import consent, referrals

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def _data_url(raw=PNG):
    return "data:image/png;base64," + base64.b64encode(raw).decode()


class _FakeDb:
    def get(self, model, key):
        return None


def _row():
    return SimpleNamespace(signed_at=None, contact_id="c1", token="t", signature_png=None)


def _sign(row, **over):
    args = dict(signer_name="Ana Gil", relation="Madre", child_name="Mateo", accepted=True, signature_data_url=_data_url())
    args.update(over)
    return consent.sign(_FakeDb(), row, **args)


def test_sign_stores_snapshot_and_time():
    row = _sign(_row())
    assert row.signed_at is not None
    assert row.text_version == consent.CONSENT_VERSION
    assert row.text_snapshot == consent.consent_text()
    assert row.signature_png == PNG


@pytest.mark.parametrize("over", [
    {"signer_name": "Ana"},
    {"child_name": " "},
    {"relation": "Vecino"},
    {"accepted": False},
    {"signature_data_url": "data:image/jpeg;base64,AAAA"},
    {"signature_data_url": _data_url(b"not a png")},
])
def test_sign_rejects_bad_input(over):
    with pytest.raises(consent.ConsentError):
        _sign(_row(), **over)


def test_cannot_sign_twice():
    row = _sign(_row())
    with pytest.raises(consent.ConsentError):
        _sign(row)


def test_apply_reward_needs_earned_and_a_note():
    r = SimpleNamespace(reward_status=referrals.PENDING_CONSENT, discount_note=None, applied_at=None, applied_by=None)
    with pytest.raises(referrals.ReferralError):
        referrals.apply_reward(None, r, note="10 %", staff_name="Dome")
    r.reward_status = referrals.EARNED
    with pytest.raises(referrals.ReferralError):
        referrals.apply_reward(None, r, note="  ", staff_name="Dome")
    referrals.apply_reward(None, r, note="10 % en la próxima sesión", staff_name="Dome")
    assert r.reward_status == referrals.APPLIED and r.applied_by == "Dome"