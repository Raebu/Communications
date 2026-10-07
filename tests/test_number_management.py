from unittest.mock import MagicMock

from sqlalchemy import select

import app.number_management as nm
from app.models import DB, PortRequest, Suppression
from test_flows import HEADERS, customer, enable, number


def test_porting_request_is_authority_gated_and_sensitive_fields_stay_encrypted():
    c, t = customer()
    enable(t)
    denied = c.post(
        "/api/number-porting",
        headers=HEADERS,
        json={
            "phone": "+442080001234",
            "current_carrier": "Example Carrier",
            "account_number": "ACC-123456",
            "authorized_name": "Alex Director",
            "service_address": "1 Example Street, London, SW1A 1AA",
            "recent_bill_ready": True,
            "authority_confirmed": False,
        },
    )
    assert denied.status_code == 422

    created = c.post(
        "/api/number-porting",
        headers=HEADERS,
        json={
            "phone": "+442080001234",
            "current_carrier": "Example Carrier",
            "account_number": "ACC-123456",
            "authorized_name": "Alex Director",
            "service_address": "1 Example Street, London, SW1A 1AA",
            "recent_bill_ready": True,
            "authority_confirmed": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["status"] == "review"
    with DB() as db:
        row = db.scalar(select(PortRequest).where(PortRequest.tenant_id == t))
        assert row is not None
        assert "ACC-123456" not in row.encrypted_payload
        assert "Example Street" not in row.encrypted_payload


def test_click_to_call_calls_agent_first_and_is_idempotent(monkeypatch):
    c, t = customer()
    enable(t)
    n = number(t)
    assert c.put(
        f"/api/numbers/{n}/forwarding",
        headers=HEADERS,
        json={"destination": "+447700900111"},
    ).status_code == 200

    provider = MagicMock()
    provider.usage.records.this_month.list.return_value = [MagicMock(price="1.00", price_unit="USD")]
    provider.calls.create.return_value = MagicMock(sid="CA" + "1" * 32)
    monkeypatch.setattr(nm, "tenant_client", lambda tenant: provider)

    payload = {
        "number_id": n,
        "destination": "+447700900222",
        "request_key": "click-call-1234567890",
        "consent_confirmed": True,
    }
    first = c.post("/api/outbound-calls", headers=HEADERS, json=payload)
    second = c.post("/api/outbound-calls", headers=HEADERS, json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    provider.calls.create.assert_called_once()
    kwargs = provider.calls.create.call_args.kwargs
    assert kwargs["to"] == "+447700900111"
    assert kwargs["from_"] == "+442080001001"
    assert "+447700900222" not in kwargs["to"]


def test_click_to_call_requires_permission_and_respects_suppression(monkeypatch):
    c, t = customer()
    enable(t)
    n = number(t)
    assert c.put(
        f"/api/numbers/{n}/forwarding",
        headers=HEADERS,
        json={"destination": "+447700900111"},
    ).status_code == 200

    denied = c.post(
        "/api/outbound-calls",
        headers=HEADERS,
        json={
            "number_id": n,
            "destination": "+447700900222",
            "request_key": "click-call-no-consent",
            "consent_confirmed": False,
        },
    )
    assert denied.status_code == 422

    with DB.begin() as db:
        db.add(Suppression(tenant_id=t, peer="+447700900222"))

    provider = MagicMock()
    monkeypatch.setattr(nm, "tenant_client", lambda tenant: provider)
    suppressed = c.post(
        "/api/outbound-calls",
        headers=HEADERS,
        json={
            "number_id": n,
            "destination": "+447700900222",
            "request_key": "click-call-suppressed",
            "consent_confirmed": True,
        },
    )
    assert suppressed.status_code == 409
    provider.calls.create.assert_not_called()


def test_provider_timeout_never_blindly_retries(monkeypatch):
    c, t = customer()
    enable(t)
    n = number(t)
    assert c.put(
        f"/api/numbers/{n}/forwarding",
        headers=HEADERS,
        json={"destination": "+447700900111"},
    ).status_code == 200

    provider = MagicMock()
    provider.usage.records.this_month.list.return_value = [MagicMock(price="1.00", price_unit="USD")]
    provider.calls.create.side_effect = TimeoutError()
    monkeypatch.setattr(nm, "tenant_client", lambda tenant: provider)
    payload = {
        "number_id": n,
        "destination": "+447700900222",
        "request_key": "click-call-timeout-123",
        "consent_confirmed": True,
    }
    first = c.post("/api/outbound-calls", headers=HEADERS, json=payload)
    second = c.post("/api/outbound-calls", headers=HEADERS, json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "review"
    assert second.json()["status"] == "review"
    provider.calls.create.assert_called_once()
