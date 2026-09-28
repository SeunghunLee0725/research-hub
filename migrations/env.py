from alembic import context
from sqlalchemy import engine_from_config, pool

from hub.db.models import Base

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    from hub.config import Settings
    config.set_main_option("sqlalchemy.url", Settings().database_url)

connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                 prefix="sqlalchemy.", poolclass=pool.NullPool)
with connectable.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
