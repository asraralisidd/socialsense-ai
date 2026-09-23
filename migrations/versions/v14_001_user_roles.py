"""Version 14 auth hardening: user roles

Revision ID: v14_001
Revises: v13_001
Create Date: 2026-09-23 12:00:00.000000

Additive V14 foundation:

    users.role   String(20), NOT NULL, server_default 'user'.
                 'admin' grants access to /admin/* maintenance and
                 monitoring endpoints; every existing row keeps the
                 safe non-admin default.

PostgreSQL notes
----------------
* Existing rows receive ``'user'`` via ``server_default`` at DDL time,
  so no data migration is required and no existing user gains admin
  access.
* The column is created behind an ``inspector.has_column`` guard because
  ``database.init_db`` calls ``db.create_all()`` on every app start, which
  may have already created it.
* Downgrade removes only this column; all other user data is preserved.
"""
from alembic import op
import sqlalchemy as sa


revision = 'v14_001'
down_revision = 'v13_001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('users'):
        columns = [c['name'] for c in inspector.get_columns('users')]
        if 'role' not in columns:
            op.add_column('users', sa.Column(
                'role', sa.String(length=20), nullable=False,
                server_default='user'))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('users'):
        columns = [c['name'] for c in inspector.get_columns('users')]
        if 'role' in columns:
            op.drop_column('users', 'role')
