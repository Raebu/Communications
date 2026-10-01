from alembic import context
from sqlalchemy import create_engine, pool
from app.config import settings
from app.models import Base

config = context.config
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings.database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=settings.database_url.startswith('sqlite'))
        with context.begin_transaction():
            context.run_migrations()
