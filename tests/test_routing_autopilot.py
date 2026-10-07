from fastapi.testclient import TestClient

import app.routing_autopilot as ra
from app.call_routing import RoutingUpdate
from app.main import app
from app.models import CallRouting, DB, Number, Tenant
from app.security import decrypt, encrypt


HEADERS = {"origin": "http://localhost:8000", "x-requested-with": "Raeburn"}


def account():
    client = TestClient(app)
    assert client.post(
        "/api/register",
        json={
            "email": "owner@example.com",
            "password": "correct-horse-battery",
            "company": "Example Ltd",
            "accept_terms": True,
        },
        headers=HEADERS,
    ).status_code == 200
    assert client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
        headers=HEADERS,
    ).status_code == 200
    tenant_id = client.get("/api/me").json()["tenant"]["id"]
    with DB.begin() as db:
        tenant = db.get(Tenant, tenant_id)
        tenant.status = "approved"
        tenant.billing_status = "active"
        number = Number(
            tenant_id=tenant_id,
            phone="+442080001001",
            sid="PN" + tenant_id.replace("-", "")[:32],
            sms=True,
            voice=True,
        )
        db.add(number)
        db.flush()
        number_id = number.id
    return client, tenant_id, number_id


def config():
    return {
        "enabled": True,
        "greeting": "Thank you for calling Example Limited.",
        "fallback": "+447700900099",
        "ring_seconds": 20,
        "options": [
            {
                "digit": "1",
                "label": "Sales",
                "description": "new enquiry quote pricing buy",
                "action": "dial",
                "destination": "+447700900011",
                "destinations": ["+447700900011", "+447700900012"],
                "strategy": "simultaneous",
            },
            {
                "digit": "2",
                "label": "Accounts",
                "description": "invoice payment billing accounts",
                "action": "dial",
                "destination": "+447700900022",
                "destinations": ["+447700900022"],
                "strategy": "simultaneous",
            },
        ],
        "business_hours": {
            "enabled": True,
            "timezone": "Europe/London",
            "weekdays": [0, 1, 2, 3, 4],
            "opens": "09:00",
            "closes": "17:00",
            "holidays": [],
            "after_hours": "voicemail",
        },
        "emergency_mode": "normal",
        "never_miss": ["fallback", "callback"],
        "vip_destination": "+447700900077",
        "whisper": True,
        "intent_first": True,
        "intent_prompt": "Tell me briefly what you are calling about, or use the keypad.",
        "voicemail_greeting": "Your message will be recorded. Please leave it after the tone.",
        "transcribe_voicemail": False,
        "callback_message": "We have saved your callback request and the team will follow up.",
    }


def test_keyword_intent_can_only_select_configured_route(monkeypatch):
    cfg = config()
    monkeypatch.setattr(ra, "configured", lambda: False)
    assert ra.classify_intent(cfg, "I need a quote for a new service") == "1"
    assert ra.classify_intent(cfg, "I have a billing invoice question") == "2"
    assert ra.classify_intent(cfg, "Please transfer me somewhere secret") == ""


def test_keyword_match_does_not_consume_ai_or_call_provider(monkeypatch):
    cfg = config()
    monkeypatch.setattr(ra, "configured", lambda: True)
    monkeypatch.setattr(ra, "_json_completion", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not run")))
    assert ra.classify_intent(cfg, "I need a sales quote") == "1"


def test_ai_intent_cannot_escape_configured_routes(monkeypatch):
    cfg = config()
    monkeypatch.setattr(ra, "configured", lambda: True)
    monkeypatch.setattr(ra, "quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(ra, "_json_completion", lambda *args, **kwargs: {"digit": "9", "confidence": 0.99})
    assert ra.classify_intent(cfg, "Something unusual", object(), "tenant") == ""


def test_simulation_is_deterministic_and_never_places_a_call():
    cfg = config()
    scenario = ra.SimulationRequest(
        config=cfg,
        at="2026-10-07T10:00:00+01:00",
        speech="I need a quote",
        selected_destination_answers=False,
        fallback_answers=False,
    )
    first = ra.simulate(cfg, scenario)
    second = ra.simulate(cfg, scenario)
    assert first == second
    assert any(step["kind"] == "ring_group" for step in first["steps"])
    assert any(step["kind"] == "fallback" for step in first["steps"])
    assert any(step["kind"] == "callback" for step in first["steps"])
    assert first["ends_safely"] is True


def test_simulation_honours_after_hours_and_vip_paths():
    cfg = config()
    after_hours = ra.simulate(
        cfg,
        ra.SimulationRequest(config=cfg, at="2026-10-07T20:30:00+01:00"),
    )
    assert [step["kind"] for step in after_hours["steps"]][-1] == "voicemail"

    vip = ra.simulate(
        cfg,
        ra.SimulationRequest(
            config=cfg,
            at="2026-10-07T10:00:00+01:00",
            vip=True,
            selected_destination_answers=True,
        ),
    )
    assert any(step["kind"] == "vip" for step in vip["steps"])
    assert vip["steps"][-1]["kind"] == "answered"


def test_generated_draft_is_validated_but_not_saved(monkeypatch):
    client, tenant_id, number_id = account()
    with DB.begin() as db:
        db.add(
            CallRouting(
                number_id=number_id,
                tenant_id=tenant_id,
                enabled=True,
                encrypted_config=encrypt(config()),
            )
        )

    generated = config()
    generated["ring_seconds"] = 30
    generated["greeting"] = "Thank you for calling. Tell us what you need."
    monkeypatch.setattr(ra, "_json_completion", lambda *args, **kwargs: generated)

    response = client.post(
        f"/api/routing-autopilot/numbers/{number_id}/draft",
        json={"instruction": "Ring Sales for 30 seconds and keep the rest unchanged."},
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["ring_seconds"] == 30
    assert "draft only" in body["note"].lower()

    with DB() as db:
        saved = decrypt(db.get(CallRouting, number_id).encrypted_config)
        assert saved["ring_seconds"] == 20
        assert saved["greeting"] == config()["greeting"]


def test_generated_draft_rejects_invalid_or_invented_number(monkeypatch):
    client, tenant_id, number_id = account()
    invalid = config()
    invalid["fallback"] = "+12345"
    monkeypatch.setattr(ra, "_json_completion", lambda *args, **kwargs: invalid)
    response = client.post(
        f"/api/routing-autopilot/numbers/{number_id}/draft",
        json={"instruction": "Make my routing better without changing my numbers."},
        headers=HEADERS,
    )
    assert response.status_code == 422

    invented = config()
    invented["fallback"] = "+447700900088"
    monkeypatch.setattr(ra, "_json_completion", lambda *args, **kwargs: invented)
    response = client.post(
        f"/api/routing-autopilot/numbers/{number_id}/draft",
        json={"instruction": "Make my routing better without changing my numbers."},
        headers=HEADERS,
    )
    assert response.status_code == 422

    with DB() as db:
        assert db.get(CallRouting, number_id) is None


def test_generated_draft_allows_phone_explicitly_supplied_by_owner(monkeypatch):
    client, tenant_id, number_id = account()
    generated = config()
    generated["fallback"] = "+447700900088"
    monkeypatch.setattr(ra, "_json_completion", lambda *args, **kwargs: generated)
    response = client.post(
        f"/api/routing-autopilot/numbers/{number_id}/draft",
        json={"instruction": "Change the fallback number to +447700900088 and keep the rest unchanged."},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["draft"]["fallback"] == "+447700900088"


def test_routing_update_schema_defaults_keep_existing_keypad_configs_compatible():
    legacy = {
        "enabled": True,
        "greeting": "Thank you for calling Example Limited.",
        "fallback": "+447700900099",
        "ring_seconds": 20,
        "options": [
            {
                "digit": "1",
                "label": "Sales",
                "destination": "+447700900011",
            }
        ],
    }
    validated = RoutingUpdate.model_validate(legacy)
    assert validated.intent_first is False
    assert validated.options[0].description == ""
