from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from app.db.base import Base
from app.models import *  # noqa

config = context.config
fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    import logging
    logger = logging.getLogger("alembic.runtime")
    logger.info("Creating engine...")
    
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    logger.info("Engine created, connecting...")
    
    with connectable.connect() as connection:
        logger.info("Connected! Configuring context...")
        context.configure(connection=connection, target_metadata=target_metadata)
        logger.info("Running migrations...")
        with context.begin_transaction():
            context.run_migrations()
        logger.info("Migrations done.")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
