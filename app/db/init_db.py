import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import settings

logger = logging.getLogger(__name__)


def init_db():
    project_root = Path(__file__).resolve().parents[2]  # /app trong container
    alembic_ini = project_root / "alembic.ini"

    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("script_location", str(project_root / "app" / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)  # ← đè giá trị sai trong ini

    logger.info("Running alembic upgrade head with url=%s", settings.database_url)
    command.upgrade(alembic_cfg, "head")
    logger.info("Alembic upgrade completed.")