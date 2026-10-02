import json
from datetime import timedelta, timezone
import httpx
from app import integrations
from app.integrations import GoogleCalendar
from app.autonomy import slots
from app.models import DB, Department, Integration, now
from app.security import encrypt
from test_ai import setup_ai as setup_ai


def test_google_calendar_token_busy_and_idempotent_event(monkeypatch):
    events = {}
    created = []
    original = httpx.Client

    def handle(request):
        path = request.url.path
        if path == "/token":
            return httpx.Response(200, json={"access_token": "private-access"})
        assert request.headers["Authorization"] == "Bearer private-access"
        if path.endswith("/freeBusy"):
            return httpx.Response(200, json={"calendars": {"business@example.com": {"busy": []}}})
        if request.method == "GET":
            identity = path.split("/")[-1]
            return httpx.Response(200, json=events[identity]) if identity in events else httpx.Response(404)
        if request.method == "POST":
            body = json.loads(request.content)
            events[body["id"]] = body
            created.append(body)
            return httpx.Response(200, json=body)
        raise AssertionError("Unexpected request")

    monkeypatch.setattr(integrations.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    provider = GoogleCalendar(
        {"calendar_id": "business@example.com", "client_id": "client", "client_secret": "secret", "refresh_token": "refresh"}
    )
    start = now() + timedelta(days=1)
    end = start + timedelta(minutes=30)
    assert provider.busy(start, end) == []
    identity = provider.create("request-1", start, end, "Appointment")
    assert provider.create("request-1", start, end, "Appointment") == identity
    assert len(created) == 1
    provider.close()


def test_external_busy_intervals_remove_native_slots_and_hide_secrets(setup_ai, monkeypatch):
    c, t, n = setup_ai
    with DB.begin() as db:
        department = Department(tenant_id=t, name="Consulting", bookings_enabled=True)
        db.add(department)
        db.flush()
        did = department.id
        offered = slots(db, department)[0]
        integration = Integration(
            tenant_id=t, name="Calendar", kind="google_calendar", encrypted_config=encrypt({"secret": "never-export"}), enabled=True
        )
        db.add(integration)
        db.flush()
        department.integration_id = integration.id
    from datetime import datetime

    busy_start = datetime.fromisoformat(offered).astimezone(timezone.utc)

    class Provider:
        def busy(self, *args):
            return [(busy_start, busy_start + timedelta(minutes=30))]

        def close(self):
            pass

    monkeypatch.setattr(integrations, "calendar_for", lambda *args: Provider())
    result = c.get("/api/departments/" + did + "/availability")
    assert result.status_code == 200
    assert offered not in result.json()["slots"]
    response = c.get("/api/integrations")
    assert "never-export" not in response.text and "encrypted_config" not in response.text
