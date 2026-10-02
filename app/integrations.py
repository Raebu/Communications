"""Operator-installed tenant integrations; secrets never enter model context or API responses."""

import hashlib
from datetime import datetime
from urllib.parse import quote
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from .models import DB, Department, Integration
from .security import current_user, decrypt

router = APIRouter()


class GoogleCalendar:
    def __init__(self, config):
        self.calendar = quote(config["calendar_id"], safe="")
        self.client = httpx.Client(timeout=8, follow_redirects=False, trust_env=False)
        try:
            r = self.client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "refresh_token": config["refresh_token"],
                    "grant_type": "refresh_token",
                },
            )
            r.raise_for_status()
            self.headers = {"Authorization": "Bearer " + r.json()["access_token"]}
        except Exception:
            self.client.close()
            raise RuntimeError("Calendar authorisation unavailable") from None

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        return self.client.request(method, "https://www.googleapis.com/calendar/v3" + path, headers=self.headers, **kwargs)

    def busy(self, start, end):
        r = self.request(
            "POST", "/freeBusy", json={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": self.calendar_id()}]}
        )
        r.raise_for_status()
        cal = r.json()["calendars"][self.calendar_id()]
        if cal.get("errors"):
            raise RuntimeError("Calendar availability unavailable")
        return [
            (datetime.fromisoformat(x["start"].replace("Z", "+00:00")), datetime.fromisoformat(x["end"].replace("Z", "+00:00")))
            for x in cal["busy"]
        ]

    def calendar_id(self):
        from urllib.parse import unquote

        return unquote(self.calendar)

    @staticmethod
    def event_id(key):
        return "r" + hashlib.sha256(key.encode()).hexdigest()[:48]

    def get(self, event_id):
        r = self.request("GET", "/calendars/" + self.calendar + "/events/" + event_id)
        if r.status_code in (404, 410):
            return None
        r.raise_for_status()
        return r.json()

    def create(self, key, start, end, name):
        identity = self.event_id(key)
        previous = self.get(identity)
        if previous:
            if (
                previous.get("status") == "cancelled"
                or datetime.fromisoformat(previous["start"]["dateTime"].replace("Z", "+00:00")) != start
            ):
                raise RuntimeError("Calendar reconciliation requires review")
            return identity
        r = self.request(
            "POST",
            "/calendars/" + self.calendar + "/events",
            json={
                "id": identity,
                "summary": name,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
                "extendedProperties": {"private": {"communications_request": key}},
            },
        )
        if r.status_code == 409:
            if self.get(identity):
                return identity
        r.raise_for_status()
        if r.json().get("id") != identity:
            raise RuntimeError("Calendar result unknown")
        return identity

    def cancel(self, event_id):
        r = self.request("DELETE", "/calendars/" + self.calendar + "/events/" + event_id)
        if r.status_code not in (204, 404, 410):
            r.raise_for_status()

    def move(self, event_id, start, end):
        previous = self.get(event_id)
        if not previous or previous.get("status") == "cancelled":
            raise RuntimeError("Calendar appointment unavailable")
        headers = {**self.headers, "If-Match": previous["etag"]}
        r = self.client.patch(
            "https://www.googleapis.com/calendar/v3/calendars/" + self.calendar + "/events/" + event_id,
            headers=headers,
            json={"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}},
        )
        r.raise_for_status()


def calendar_for(db, department):
    if not department.integration_id:
        return None
    integration = db.scalar(
        select(Integration).where(
            Integration.id == department.integration_id,
            Integration.tenant_id == department.tenant_id,
            Integration.kind == "google_calendar",
            Integration.enabled.is_(True),
        )
    )
    if not integration:
        raise RuntimeError("Calendar integration disabled")
    return GoogleCalendar(decrypt(integration.encrypted_config))


@router.get("/api/integrations")
def integrations(user=Depends(current_user)):
    with DB() as db:
        return [
            {"id": i.id, "name": i.name, "kind": i.kind, "enabled": i.enabled, "status": i.last_status}
            for i in db.scalars(select(Integration).where(Integration.tenant_id == user.tenant_id))
        ]


@router.get("/api/departments/{department_id}/availability")
def availability(department_id: str, user=Depends(current_user)):
    from .autonomy import slots

    with DB() as db:
        d = db.scalar(select(Department).where(Department.id == department_id, Department.tenant_id == user.tenant_id))
        if not d or not d.bookings_enabled:
            raise HTTPException(404, "Booking department unavailable")
        try:
            return {"slots": slots(db, d), "source": "external_calendar" if d.integration_id else "native_calendar"}
        except Exception:
            raise HTTPException(503, "Calendar availability cannot be verified") from None
