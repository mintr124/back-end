"""remove role clearance department from users

Revision ID: 001_remove_user_fields
Revises: 694b9a88557d
Create Date: 2026-05-30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "001_remove_user_fields"
down_revision = "694b9a88557d"
branch_labels = None
depends_on = None


def _fk_exists(conn, table, fk_name):
    result = conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND CONSTRAINT_NAME = :fk
          AND CONSTRAINT_TYPE = 'FOREIGN KEY'
    """), {"table": table, "fk": fk_name})
    return result.scalar() > 0


def _column_exists(conn, table, column):
    result = conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table
          AND COLUMN_NAME = :col
    """), {"table": table, "col": column})
    return result.scalar() > 0


def upgrade():
    conn = op.get_bind()

    if _fk_exists(conn, "users", "users_ibfk_1"):
        op.execute("ALTER TABLE users DROP FOREIGN KEY `users_ibfk_1`")

    for col in ["role", "clearance_level", "department_id"]:
        if _column_exists(conn, "users", col):
            op.execute(f"ALTER TABLE users DROP COLUMN `{col}`")


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("role", sa.String(64), nullable=False, server_default="employee"))
        batch_op.add_column(sa.Column("clearance_level", sa.String(32), nullable=False, server_default="internal"))
        batch_op.add_column(sa.Column("department_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key("users_ibfk_1", "departments", ["department_id"], ["id"])