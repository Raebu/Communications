"""Transactional delivery; existing Resend SMTP credentials use HTTPS."""
import smtplib
from email.message import EmailMessage
import httpx
from .config import settings


def send_email(recipient, payload, delivery_id):
    sender = payload.get("from") or settings.email_from
    reply_to = payload.get("reply_to") or settings.email_reply_to
    if settings.smtp_host.lower() == "smtp.resend.com":
        if not settings.smtp_password.startswith("re_"):
            raise RuntimeError("Resend sending key missing")
        data = {"from": sender, "to": [recipient], "subject": payload["subject"], "text": payload["body"]}
        if reply_to:
            data["reply_to"] = reply_to
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": "Bearer " + settings.smtp_password,
                         "Idempotency-Key": "raeburn-email/" + delivery_id},
                json=data,
            )
        if not response.is_success:
            raise RuntimeError("Resend rejected email (HTTP " + str(response.status_code) + ")")
        if not isinstance(response.json().get("id"), str) or not response.json()["id"]:
            raise RuntimeError("Resend delivery acknowledgement missing")
        return
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, payload["subject"]
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(payload["body"])
    connection = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
    with connection(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_port != 465:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
