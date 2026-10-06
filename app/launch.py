"""Read-only activation report; never emits secrets or implies provider acceptance."""

import json
from cryptography.fernet import Fernet
from sqlalchemy import text
from .config import settings
from .models import DB
from .ai import configured


def report():
    try:
        Fernet(settings.encryption_key.encode())
        encrypted = True
    except (ValueError, TypeError):
        encrypted = False
    try:
        with DB() as db:
            revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        schema = revision == "0015"
    except Exception:
        schema = False
    checks = {
        "production_environment": settings.environment == "production",
        "https_hostname": settings.public_url.startswith("https://") and "localhost" not in settings.public_url,
        "postgresql": settings.database_url.startswith("postgresql"),
        "schema_current": schema,
        "encryption_key_valid": encrypted,
        "twilio_main_key_configured": bool(settings.twilio_sid and settings.twilio_api_key and settings.twilio_api_secret),
        "platform_stripe_live_configured": settings.stripe_key.startswith(("rk_live_", "sk_live_"))
        and bool(settings.stripe_webhook_secret),
        "published_plan_prices_configured": bool(settings.business_price and settings.connect_price),
        "company_verification_enabled": settings.company_verification_enabled,
        "registry_configured": bool(settings.companies_house_key),
        "director_identity_configured": bool(settings.identity_key),
        "automated_telephone_review_enabled": settings.verification_auto_twilio,
        "smtp_configured": bool(settings.smtp_host and settings.email_from),
        "business_identity_configured": bool(settings.legal_business_name and settings.legal_business_address and settings.legal_business_number and settings.support_email),
        "published_terms_configured": bool(
            settings.terms_version and settings.terms_url.startswith("https://") and settings.privacy_url.startswith("https://")
        ),
        "ai_endpoint_configured": configured(),
        "streaming_voice_enabled": settings.voice_streaming_enabled,
        "public_sales_enabled": settings.public_sales_enabled,
    }
    return {
        "configuration_complete": all(checks.values()),
        "checks": checks,
        "note": "Configuration checks do not prove provider approval, live acceptance, backup restoration or service readiness.",
    }


if __name__ == "__main__":
    result = report()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["configuration_complete"] else 1)
