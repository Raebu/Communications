"""Customer-configurable inbound call menus and routing."""
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from .models import Audit, CallRouting, DB, Number, now
from .security import csrf, current_user, decrypt, encrypt

router = APIRouter()


PHONE = r"^\+44(?:[12]\d{9}|7[1-57-9]\d{8})$"


class RouteOption(BaseModel):
    digit: str = Field(pattern=r"^[0-9]$")
    label: str = Field(min_length=1, max_length=60)
    destination: str = Field(pattern=PHONE)


class RoutingUpdate(BaseModel):
    enabled: bool = False
    greeting: str = Field(default="Thank you for calling. Please choose from the following options.", min_length=10, max_length=500)
    fallback: str = Field(default="", pattern=r"^(?:\+44(?:[12]\d{9}|7[1-57-9]\d{8}))?$")
    ring_seconds: int = Field(default=20, ge=10, le=45)
    options: list[RouteOption] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_routes(self):
        digits = [item.digit for item in self.options]
        if len(digits) != len(set(digits)):
            raise ValueError("Each menu number can only be used once")
        if self.enabled and not self.options:
            raise ValueError("Add at least one call menu option")
        if self.enabled and not self.fallback:
            raise ValueError("Add a fallback number for unanswered calls")
        return self


def _config(row):
    if not row or not row.encrypted_config:
        return None
    value = decrypt(row.encrypted_config)
    value["enabled"] = bool(row.enabled)
    return value


def routing_for(db, number):
    row = db.get(CallRouting, number.id)
    value = _config(row)
    return value if value and value.get("enabled") else None


@router.get("/api/numbers/{number_id}/call-routing")
def get_call_routing(number_id: str, user=Depends(current_user)):
    with DB() as db:
        number = db.scalar(select(Number).where(Number.id == number_id, Number.tenant_id == user.tenant_id))
        if not number or not number.voice:
            raise HTTPException(404, "Voice number not found")
        row = db.get(CallRouting, number.id)
        value = _config(row)
        return value or {
            "enabled": False,
            "greeting": "Thank you for calling. Please choose from the following options.",
            "fallback": number.forwarding or "",
            "ring_seconds": 20,
            "options": [],
        }


@router.put("/api/numbers/{number_id}/call-routing", dependencies=[Depends(csrf)])
def update_call_routing(number_id: str, data: RoutingUpdate, user=Depends(current_user)):
    if user.role != "owner":
        raise HTTPException(403, "Account owner required")
    with DB.begin() as db:
        number = db.scalar(select(Number).where(Number.id == number_id, Number.tenant_id == user.tenant_id).with_for_update())
        if not number or not number.voice:
            raise HTTPException(404, "Voice number not found")
        destinations = [item.destination for item in data.options]
        if number.phone in destinations or data.fallback == number.phone:
            raise HTTPException(422, "A business number cannot route calls back to itself")
        payload = {
            "greeting": data.greeting.strip(),
            "fallback": data.fallback,
            "ring_seconds": data.ring_seconds,
            "options": [item.model_dump() for item in data.options],
        }
        row = db.get(CallRouting, number.id)
        if not row:
            row = CallRouting(number_id=number.id, tenant_id=user.tenant_id)
            db.add(row)
        row.enabled = data.enabled
        row.encrypted_config = encrypt(payload)
        row.updated_at = now()
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="call_routing.updated", detail=number.id))
    return {"status": "updated"}


def menu_xml(config, action_url, prefix=""):
    menu = " ".join(
        ("Press zero" if item["digit"] == "0" else "Press " + item["digit"]) + " for " + item["label"] + "."
        for item in sorted(config["options"], key=lambda item: item["digit"])
    )
    prompt = (prefix + " " if prefix else "") + config["greeting"].strip() + " " + menu
    return (
        '<Response><Gather numDigits="1" timeout="6" actionOnEmptyResult="true" action="'
        + escape(action_url, {'"': "&quot;"})
        + '" method="POST"><Say>'
        + escape(prompt)
        + '</Say></Gather></Response>'
    )


def dial_xml(destination, minutes, action_url, timeout):
    return (
        '<Response><Dial timeout="'
        + str(timeout)
        + '" timeLimit="'
        + str(max(1, minutes) * 60)
        + '" action="'
        + escape(action_url, {'"': "&quot;"})
        + '" method="POST"><Number>'
        + escape(destination)
        + '</Number></Dial></Response>'
    )


def unavailable_xml(message="We are sorry, nobody is available to take your call right now. Please try again later."):
    return "<Response><Say>" + escape(message) + "</Say></Response>"


def selected_destination(config, digit):
    for item in config["options"]:
        if item["digit"] == digit:
            return item["destination"]
    return ""
