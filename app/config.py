import os
from dataclasses import dataclass


@dataclass
class Settings:
    database_url: str = os.getenv('DATABASE_URL', 'sqlite:///./communications.db')
    public_url: str = os.getenv('PUBLIC_URL', 'http://localhost:8000').rstrip('/')
    environment: str = os.getenv('ENVIRONMENT', 'development')
    encryption_key: str = os.getenv('ENCRYPTION_KEY', '')
    twilio_sid: str = os.getenv('TWILIO_ACCOUNT_SID', '')
    twilio_token: str = os.getenv('TWILIO_AUTH_TOKEN', '')
    stripe_key: str = os.getenv('STRIPE_SECRET_KEY', '')
    stripe_webhook_secret: str = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    business_price: str = os.getenv('STRIPE_PRICE_BUSINESS', '')
    connect_price: str = os.getenv('STRIPE_PRICE_CONNECT', '')
    ai_price: str = os.getenv('STRIPE_PRICE_AI', '')
    registration_enabled: bool = os.getenv('REGISTRATION_ENABLED', 'false') == 'true'

    def validate(self):
        if self.environment == 'production':
            if not self.public_url.startswith('https://') or not self.encryption_key:
                raise RuntimeError('Production requires HTTPS PUBLIC_URL and ENCRYPTION_KEY')
            if not self.database_url.startswith('postgresql'):
                raise RuntimeError('Production requires PostgreSQL')


settings = Settings()
