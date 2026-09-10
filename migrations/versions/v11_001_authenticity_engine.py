"""Version 11 authenticity intelligence

Revision ID: v11_001
Revises: v9_001
Create Date: 2026-08-14 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'v11_001'
down_revision = 'v9_001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('media_analyses'):
        op.create_table('media_analyses',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('analysis_id', sa.Integer(), sa.ForeignKey('analyses.id'), nullable=False, index=True),
            sa.Column('overall_ai_probability', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('overall_authenticity_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('deepfake_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('synthetic_voice_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('thumbnail_ai_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('frame_manipulation_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('metadata_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('reasons', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('analysis_id'),
        )


def downgrade():
    op.drop_table('media_analyses')
