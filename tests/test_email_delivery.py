from unittest.mock import MagicMock
import httpx
import pytest
from app.config import settings
from app import email_delivery as delivery


@pytest.mark.parametrize("status,body,accepted", [
    (200, {"id": "email-provider-id"}, True),
    (401, {"message": "secret-provider-error"}, False),
    (200, {}, False),
])
def test_resend_uses_https_and_requires_acknowledgement(monkeypatch, status, body, accepted):
    monkeypatch.setattr(settings, "smtp_host", "smtp.resend.com")
    monkeypatch.setattr(settings, "smtp_password", "re_private")
    monkeypatch.setattr(settings, "email_from", "newphoneline@theraeburngroup.com")
    monkeypatch.setattr(settings, "email_reply_to", "support@theraeburngroup.com")
    client = MagicMock()
    client.post.return_value = httpx.Response(status, json=body)
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    monkeypatch.setattr(delivery.httpx, "Client", factory)
    smtp = MagicMock()
    monkeypatch.setattr(delivery.smtplib, "SMTP_SSL", smtp)
    payload = {"subject": "Verification", "body": "Private token", "from": "verification@theraeburngroup.com"}
    if accepted:
        delivery.send_email("contact@example.com", payload, "job-123")
    else:
        with pytest.raises(RuntimeError) as error:
            delivery.send_email("contact@example.com", payload, "job-123")
        assert "secret-provider-error" not in str(error.value)
    smtp.assert_not_called()
    factory.assert_called_once_with(timeout=20, follow_redirects=False, trust_env=False)
    args, kwargs = client.post.call_args
    assert args == ("https://api.resend.com/emails",)
    assert kwargs["headers"]["Idempotency-Key"] == "raeburn-email/job-123"
    assert kwargs["json"]["reply_to"] == "support@theraeburngroup.com"
    assert kwargs["json"]["from"] == "verification@theraeburngroup.com"


def test_other_smtp_providers_keep_tls(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "user")
    smtp = MagicMock()
    monkeypatch.setattr(delivery.smtplib, "SMTP", smtp)
    delivery.send_email("contact@example.com", {"subject": "Hello", "body": "Message"}, "job")
    smtp.return_value.__enter__.return_value.starttls.assert_called_once()
    smtp.return_value.__enter__.return_value.send_message.assert_called_once()
