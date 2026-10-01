import os
import tempfile
os.environ['DATABASE_URL'] = os.getenv('TEST_DATABASE_URL', 'sqlite:///' + tempfile.mktemp(suffix='.db'))
os.environ['REGISTRATION_ENABLED'] = 'true'
import pytest
from cryptography.fernet import Fernet
from app.config import settings
from app.models import Base, engine


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings.encryption_key = Fernet.generate_key().decode()
    yield
