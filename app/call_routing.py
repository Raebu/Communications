"""Customer-configurable Never-Miss inbound call routing."""
import re
from datetime import date, datetime, timezone
from typing import Literal
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from .models import Audit, Call, CallRouting, DB, Number, now
from .security import csrf, current_user, decrypt, encrypt

router = APIRouter()


PHONE = r"^\+44(?:[12]\d{9}|7[1-57-9]\d{8})$"
FALLBACK_ACTIONS = {"fallback", "ai", "callback", "voicemail"}


class RouteOption(BaseModel):
    digit: str = Field(pattern=r"^[0-9]$")
    label: str = Field(min_length=1, max_length=60)
    action: Literal["dial", "callback", "ai", "voicemail"] = "dial"
    destination: str = ""
    destinations: list[str] = Field(default_factory=list, max_length=8)
    strategy: Literal["simultaneous", "sequential", "priority", "longest_idle"] = "simultaneous"

    @model_validator(mode="after")
    def validate_option(self):
        members = self.destinations or ([self.destination] if self.destination else [])
        if self.action == "dial":
            if not members:
                raise ValueError("A dial option needs at least one destination")
            if len(set(members)) != len(members):
                raise ValueError("A ring group cannot contain the same destination twice")
            if any(not re.fullmatch(PHONE, member) for member in members):
                raise ValueError("Use valid UK phone numbers for ring group members")
        return self


class BusinessHours(BaseModel):
    enabled: bool = False
    timezone: str = Field(default="Europe/London", max_length=100)
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], max_length=7)
    opens: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    closes: str = Field(default="17:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    holidays: list[str] = Field(default_factory=list, max_length=40)
    after_hours: Literal["fallback", "ai", "callback", "voicemail"] = "fallback"

    @model_validator(mode="after")
    def validate_hours(self):
        if len(set(self.weekdays)) != len(self.weekdays) or any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("Working days must be unique values from 0 to 6")
        if self.opens >= self.closes:
            raise ValueError("Closing time must be later than opening time")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("Use a valid timezone") from None
        for holiday in self.holidays:
            try:
                date.fromisoformat(holiday)
            except ValueError:
                raise ValueError("Holidays must use YYYY-MM-DD") from None
        return self


class RoutingUpdate(BaseModel):
    enabled: bool = False
    greeting: str = Field(
        default="Thank you for calling. Please choose from the following options.",
        min_length=10,
        max_length=500,
    )
    fallback: str = Field(default="", pattern=r"^(?:\+44(?:[12]\d{9}|7[1-57-9]\d{8}))?$")
    ring_seconds: int = Field(default=20, ge=10, le=45)
    options: list[RouteOption] = Field(default_factory=list, max_length=10)
    business_hours: BusinessHours = Field(default_factory=BusinessHours)
    emergency_mode: Literal["normal", "closed", "fallback", "ai", "callback", "voicemail"] = "normal"
    never_miss: list[Literal["fallback", "ai", "callback", "voicemail"]] = Field(
        default_factory=lambda: ["fallback", "ai", "callback"],
        max_length=4,
    )
    vip_destination: str = Field(default="", pattern=r"^(?:\+44(?:[12]\d{9}|7[1-57-9]\d{8}))?$")
    whisper: bool = True
    voicemail_greeting: str = Field(
        default="Nobody is available right now. Your message will be recorded. Please leave it after the tone.",
        min_length=10,
        max_length=300,
    )
    transcribe_voicemail: bool = False
    callback_message: str = Field(
        default="We have saved your callback request and the team will follow up.",
        min_length=10,
        max_length=300,
    )

    @model_validator(mode="after")
    def validate_routes(self):
        digits = [item.digit for item in self.options]
        if len(digits) != len(set(digits)):
            raise ValueError("Each menu number can only be used once")
        if self.enabled and not self.options and self.emergency_mode == "normal":
            raise ValueError("Add at least one call menu option")
        if len(set(self.never_miss)) != len(self.never_miss):
            raise ValueError("Never-Miss steps cannot be duplicated")
        if any(step not in FALLBACK_ACTIONS for step in self.never_miss):
            raise ValueError("Unsupported Never-Miss step")
        if "fallback" in self.never_miss and self.enabled and not self.fallback:
            raise ValueError("Add a fallback number for the Never-Miss chain")
        return self


def _members(option):
    return option.get("destinations") or ([option.get("destination")] if option.get("destination") else [])


def _normalise(value):
    value = dict(value or {})
    value.setdefault("business_hours", BusinessHours().model_dump())
    value.setdefault("emergency_mode", "normal")
    value.setdefault("never_miss", ["fallback", "ai", "callback"])
    value.setdefault("vip_destination", "")
    value.setdefault("whisper", True)
    value.setdefault("voicemail_greeting", "Nobody is available right now. Your message will be recorded. Please leave it after the tone.")
    value.setdefault("transcribe_voicemail", False)
    value.setdefault("callback_message", "We have saved your callback request and the team will follow up.")
    value.setdefault("ring_seconds", 20)
    value.setdefault("fallback", "")
    value.setdefault("options", [])
    for option in value["options"]:
        option.setdefault("action", "dial")
        option.setdefault("strategy", "simultaneous")
        option.setdefault("destinations", _members(option))
    return value


def _config(row):
    if not row or not row.encrypted_config:
        return None
    value = _normalise(decrypt(row.encrypted_config))
    value["enabled"] = bool(row.enabled)
    return value


def routing_for(db, number):
    row = db.get(CallRouting, number.id)
    value = _config(row)
    return value if value and value.get("enabled") else None


def is_open(config, at=None):
    hours = config.get("business_hours") or {}
    if not hours.get("enabled"):
        return True
    try:
        local = (at or datetime.now(timezone.utc)).astimezone(ZoneInfo(hours.get("timezone", "Europe/London")))
    except ZoneInfoNotFoundError:
        return False
    if local.date().isoformat() in set(hours.get("holidays") or []):
        return False
    if local.weekday() not in set(hours.get("weekdays") or []):
        return False
    current = local.strftime("%H:%M")
    return hours.get("opens", "09:00") <= current < hours.get("closes", "17:00")


def initial_action(config):
    emergency = config.get("emergency_mode", "normal")
    if emergency != "normal":
        return "closed" if emergency == "closed" else emergency
    if not is_open(config):
        return (config.get("business_hours") or {}).get("after_hours", "fallback")
    return "menu"


def selected_option(config, digit):
    for item in config.get("options", []):
        if item.get("digit") == digit:
            return item
    return None


def ordered_members(db, tenant_id, option):
    members = list(_members(option))
    strategy = option.get("strategy", "simultaneous")
    if strategy != "longest_idle" or len(members) < 2:
        return members
    last_used = {}
    for member in members:
        latest = db.scalar(
            select(Call.created_at).where(
                Call.tenant_id == tenant_id,
                Call.destination == member,
            ).order_by(Call.created_at.desc()).limit(1)
        )
        last_used[member] = latest or datetime.min.replace(tzinfo=timezone.utc)
    return sorted(members, key=lambda member: last_used[member])


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
            "business_hours": BusinessHours().model_dump(),
            "emergency_mode": "normal",
            "never_miss": ["fallback", "ai", "callback"],
            "vip_destination": "",
            "whisper": True,
            "voicemail_greeting": "Nobody is available right now. Your message will be recorded. Please leave it after the tone.",
            "transcribe_voicemail": False,
            "callback_message": "We have saved your callback request and the team will follow up.",
        }


@router.put("/api/numbers/{number_id}/call-routing", dependencies=[Depends(csrf)])
def update_call_routing(number_id: str, data: RoutingUpdate, user=Depends(current_user)):
    if user.role != "owner":
        raise HTTPException(403, "Account owner required")
    with DB.begin() as db:
        number = db.scalar(
            select(Number).where(Number.id == number_id, Number.tenant_id == user.tenant_id).with_for_update()
        )
        if not number or not number.voice:
            raise HTTPException(404, "Voice number not found")
        destinations = []
        for item in data.options:
            destinations.extend(item.destinations or ([item.destination] if item.destination else []))
        destinations.extend([data.fallback, data.vip_destination])
        if number.phone in {value for value in destinations if value}:
            raise HTTPException(422, "A business number cannot route calls back to itself")
        payload = data.model_dump(exclude={"enabled"})
        payload["greeting"] = data.greeting.strip()
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
        for item in sorted(config.get("options", []), key=lambda item: item["digit"])
    )
    prompt = (prefix + " " if prefix else "") + config["greeting"].strip() + " " + menu
    return (
        '<Response><Gather numDigits="1" timeout="6" actionOnEmptyResult="true" action="'
        + escape(action_url, {'"': "&quot;"})
        + '" method="POST"><Say>'
        + escape(prompt)
        + '</Say></Gather></Response>'
    )


def dial_group_xml(destinations, minutes, action_url, timeout, whisper_url="", sequential=False):
    numbers = "".join(
        '<Number'
        + (' url="' + escape(whisper_url, {'"': "&quot;"}) + '" method="POST"' if whisper_url else "")
        + ">"
        + escape(destination)
        + "</Number>"
        for destination in destinations
    )
    return (
        '<Response><Dial timeout="'
        + str(timeout)
        + ('" sequential="true' if sequential else '')
        + '" timeLimit="'
        + str(max(1, minutes) * 60)
        + '" action="'
        + escape(action_url, {'"': "&quot;"})
        + '" method="POST">'
        + numbers
        + '</Dial></Response>'
    )


def dial_xml(destination, minutes, action_url, timeout, whisper_url=""):
    return dial_group_xml([destination], minutes, action_url, timeout, whisper_url, False)


def voicemail_xml(config, action_url, status_url, transcription_url=""):
    transcribe = bool(config.get("transcribe_voicemail") and transcription_url)
    transcription = (
        ' transcribe="true" transcribeCallback="'
        + escape(transcription_url, {'"': "&quot;"})
        + '"'
        if transcribe else ""
    )
    return (
        "<Response><Say>"
        + escape(config.get("voicemail_greeting", "Your message will be recorded. Please leave it after the tone."))
        + '</Say><Record maxLength="119" playBeep="true" action="'
        + escape(action_url, {'"': "&quot;"})
        + '" recordingStatusCallback="'
        + escape(status_url, {'"': "&quot;"})
        + '" recordingStatusCallbackMethod="POST" method="POST"'
        + transcription
        + "/></Response>"
    )


def callback_xml(config):
    return "<Response><Say>" + escape(config.get("callback_message", "Your callback request has been saved.")) + "</Say></Response>"


def unavailable_xml(message="We are sorry, nobody is available to take your call right now. Please try again later."):
    return "<Response><Say>" + escape(message) + "</Say></Response>"


def whisper_xml(text):
    return "<Response><Say>" + escape(text[:500]) + "</Say></Response>"
