"""
init_db.py
==========
KHÔNG dùng create_all() nữa — toàn bộ schema do Alembic quản lý.
Hàm này chỉ chạy alembic upgrade head để đảm bảo DB luôn ở schema mới nhất,
áp dụng cho mọi máy (mới hoặc đã có data cũ).
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)


def init_db():
    project_root = Path(__file__).resolve().parents[2]  # back-end/
    alembic_ini = project_root / "alembic.ini"

    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("script_location", str(project_root / "app" / "alembic"))

    logger.info("Running alembic upgrade head...")
    command.upgrade(alembic_cfg, "head")
    logger.info("Alembic upgrade completed.")