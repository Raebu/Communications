import os
from dataclasses import dataclass


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./communications.db")
    public_url: str = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
    environment: str = os.getenv("ENVIRONMENT", "development")
    encryption_key: str = os.getenv("ENCRYPTION_KEY", "")
    twilio_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    stripe_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    business_price: str = os.getenv("STRIPE_PRICE_BUSINESS", "")
    connect_price: str = os.getenv("STRIPE_PRICE_CONNECT", "")
    ai_price: str = os.getenv("STRIPE_PRICE_AI", "")
    twilio_api_key: str = os.getenv("TWILIO_API_KEY", "")
    twilio_api_secret: str = os.getenv("TWILIO_API_SECRET", "")
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    email_from: str = os.getenv("EMAIL_FROM", "")
    email_reply_to: str = os.getenv("EMAIL_REPLY_TO", "")
    verification_email_from: str = os.getenv("VERIFICATION_EMAIL_FROM", "")
    company_review_email: str = os.getenv("COMPANY_REVIEW_EMAIL", "")
    billing_email: str = os.getenv("BILLING_EMAIL", "")
    public_sales_enabled: bool = os.getenv("PUBLIC_SALES_ENABLED", "false") == "true"
    sms_monthly_segments: int = int(os.getenv("SMS_MONTHLY_SEGMENTS", "100"))
    voice_monthly_minutes: int = int(os.getenv("VOICE_MONTHLY_MINUTES", "30"))
    legal_business_name: str = os.getenv("LEGAL_BUSINESS_NAME", "")
    legal_business_address: str = os.getenv("LEGAL_BUSINESS_ADDRESS", "")
    support_email: str = os.getenv("SUPPORT_EMAIL", "")
    terms_url: str = os.getenv("TERMS_URL", "")
    privacy_url: str = os.getenv("PRIVACY_URL", "")
    terms_version: str = os.getenv("TERMS_VERSION", "")
    ai_url: str = os.getenv("AI_URL", "").rstrip("/")
    ai_key: str = os.getenv("AI_API_KEY", "")
    ai_model: str = os.getenv("AI_MODEL", "")
    voice_streaming_enabled: bool = os.getenv("VOICE_STREAMING_ENABLED", "false") == "true"
    ai_daily_requests: int = int(os.getenv("AI_DAILY_REQUESTS", "100"))
    message_retention_days: int = int(os.getenv("MESSAGE_RETENTION_DAYS", "90"))
    voice_retention_days: int = int(os.getenv("VOICE_RETENTION_DAYS", "30"))
    company_verification_enabled: bool = os.getenv("COMPANY_VERIFICATION_ENABLED", "false") == "true"
    companies_house_key: str = os.getenv("COMPANIES_HOUSE_API_KEY", "")
    identity_key: str = os.getenv("STRIPE_IDENTITY_KEY", "")
    trusted_company_domains: str = os.getenv("TRUSTED_COMPANY_DOMAINS", "{}")
    protected_domains: str = os.getenv("PROTECTED_DOMAINS", "theraeburngroup.com,techleadersindex.com,globaltechtop10.com")
    verification_daily_limit: int = int(os.getenv("VERIFICATION_DAILY_LIMIT", "20"))
    verification_auto_twilio: bool = os.getenv("VERIFICATION_AUTO_TWILIO", "false") == "true"
    registration_enabled: bool = os.getenv("REGISTRATION_ENABLED", "false") == "true"

    def validate(self):
        if not 31 <= self.message_retention_days <= 365 or not 1 <= self.voice_retention_days <= 90:
            raise RuntimeError("Retention must be 31–365 days for messages and 1–90 days for voice")
        import json
        try:
            trusted = json.loads(self.trusted_company_domains)
            if not isinstance(trusted, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in trusted.items()):
                raise ValueError()
        except (ValueError, TypeError):
            raise RuntimeError("TRUSTED_COMPANY_DOMAINS must be a company-number to domain JSON object") from None
        if self.company_verification_enabled:
            if not all([self.companies_house_key, self.identity_key, self.smtp_host, self.email_from, self.encryption_key]):
                raise RuntimeError("Company verification requires registry, identity and email configuration")
            if self.environment == "production" and not self.identity_key.startswith(("rk_live_", "sk_live_")):
                raise RuntimeError("Production company verification requires a live Stripe Identity key")
        if not 1 <= self.verification_daily_limit <= 100:
            raise RuntimeError("Verification daily limit must be 1–100")
        if self.environment == "production":
            if not self.public_url.startswith("https://") or not self.encryption_key:
                raise RuntimeError("Production requires HTTPS PUBLIC_URL and ENCRYPTION_KEY")
            if self.registration_enabled and not self.public_sales_enabled:
                raise RuntimeError("Public registration requires PUBLIC_SALES_ENABLED launch configuration")
            if self.public_sales_enabled:
                required = [
                    self.company_verification_enabled,
                    self.smtp_host,
                    self.email_from,
                    self.twilio_sid,
                    self.twilio_api_key,
                    self.twilio_api_secret,
                    self.stripe_key,
                    self.stripe_webhook_secret,
                    self.business_price,
                    self.connect_price,
                    self.legal_business_name,
                    self.legal_business_address,
                    self.support_email,
                    self.terms_version,
                    self.terms_url,
                    self.privacy_url,
                ]
                if not self.terms_url.startswith("https://") or not self.privacy_url.startswith("https://"):
                    raise RuntimeError("Public sales requires published HTTPS terms and privacy policy")
                if not all(required) or not self.stripe_key.startswith(("rk_live_", "sk_live_")):
                    raise RuntimeError("Public sales requires live providers, email and legal service identity")
            if not self.database_url.startswith("postgresql"):
                raise RuntimeError("Production requires PostgreSQL")


settings = Settings()
