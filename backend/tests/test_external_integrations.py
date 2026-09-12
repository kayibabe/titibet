"""Safe contract tests for integrations with external side effects."""

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, *, response: _FakeResponse, capture: list | None = None, **_kwargs):
        self.response = response
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, **kwargs):
        if self.capture is not None:
            self.capture.append((url, kwargs))
        return self.response

    async def post(self, url, **kwargs):
        if self.capture is not None:
            self.capture.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_paystack_verification_escapes_reference(monkeypatch):
    from app.services import paystack

    captured = []
    payload = {"status": True, "data": {"status": "success", "reference": "ref/with?reserved"}}
    monkeypatch.setattr(paystack, "get_settings", lambda: SimpleNamespace(paystack_secret_key="test-secret"))
    monkeypatch.setattr(
        paystack.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response=_FakeResponse(payload), capture=captured, **kwargs),
    )

    result = await paystack.verify_transaction("ref/with?reserved")

    assert result["status"] == "success"
    assert captured[0][0].endswith("/transaction/verify/ref%2Fwith%3Freserved")


def test_paystack_webhook_signature_is_verified(monkeypatch):
    from app.services import paystack

    secret = "test-secret"
    body = b'{"event":"charge.success"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    monkeypatch.setattr(paystack, "get_settings", lambda: SimpleNamespace(paystack_secret_key=secret))

    assert paystack.validate_webhook(body, signature)
    assert not paystack.validate_webhook(body, "0" * 128)


@pytest.mark.asyncio
async def test_telegram_requires_successful_api_response(monkeypatch):
    from app.services import telegram

    monkeypatch.setattr(telegram, "settings", SimpleNamespace(telegram_bot_token="test-token"))

    monkeypatch.setattr(
        telegram.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response=_FakeResponse({"ok": True}), **kwargs),
    )
    assert await telegram._send_to("-100123", "test")

    monkeypatch.setattr(
        telegram.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(response=_FakeResponse({"ok": False, "description": "chat not found"}), **kwargs),
    )
    assert not await telegram._send_to("-100123", "test")
