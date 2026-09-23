"""Version 13 historical context intelligence

Revision ID: v13_001
Revises: v12_001
Create Date: 2026-09-23 12:00:00.000000

Additive V13 foundation:

    threat_assessments.evidence_refs   nullable JSON list of evidence links
                                       ``{claim, component, source_table,
                                       source_id, ref, snippet, score,
                                       evidence_type}`` connecting each
                                       persisted claim to the stored row
                                       that produced it.

PostgreSQL notes
----------------
* The column uses ``sa.JSON().with_variant(JSONB, 'postgresql')`` so
  production gets JSONB while the SQLite-backed test suite still works.
* The column is created behind an ``inspector.has_column`` guard because
  ``database.init_db`` calls ``db.create_all()`` on every app start, which
  may have already created it.
* Downgrade removes only this column; all V12 data is preserved.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'v13_001'
down_revision = 'v12_001'
branch_labels = None
depends_on = None


def _json():
    """JSONB on PostgreSQL, JSON elsewhere. Mirrors the model definitions."""
    return sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('threat_assessments'):
        columns = [c['name'] for c in inspector.get_columns('threat_assessments')]
        if 'evidence_refs' not in columns:
            op.add_column('threat_assessments', sa.Column('evidence_refs', _json(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('threat_assessments'):
        columns = [c['name'] for c in inspector.get_columns('threat_assessments')]
        if 'evidence_refs' in columns:
            op.drop_column('threat_assessments', 'evidence_refs')
