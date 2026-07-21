"""
Alembic migration environment. Configures SQLAlchemy metadata and runs
migrations in either offline (URL-only) or online (live connection) mode.
"""

import logging
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from app.db.base import Base
from app.models import *  # noqa

# Alembic config object, providing access to values in alembic.ini.
config = context.config

# Wire Python logging to the handlers declared in alembic.ini.
fileConfig(config.config_file_name)

# Schema metadata used by Alembic to detect model changes for autogenerate.
target_metadata = Base.metadata


# Run migrations without a live database connection, using a URL string only.
def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


# Run migrations against a live database connection.
def run_migrations_online():
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
