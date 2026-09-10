"""Version 12 threat and narrative intelligence

Revision ID: v12_001
Revises: v11_001
Create Date: 2026-08-28 12:00:00.000000

Creates the V12 intelligence layer:

    narratives              detected recurring themes/claims per user
    narrative_occurrences   narrative <-> analyzed content links
    coordination_signals    potential coordinated-behaviour signals
    propagation_events      observed relationships between analyses
    threat_assessments      consolidated per-analysis assessment (1:1)

PostgreSQL notes
----------------
* JSON payload columns are declared as ``sa.JSON().with_variant(JSONB, 'postgresql')``
  so production gets JSONB while the SQLite-backed test suite still works.
* Every foreign key is created with an explicit ``ondelete`` rule so cascade
  behaviour is enforced by the database and not only by the ORM.
* Each table is created behind an ``inspector.has_table`` guard because
  ``database.init_db`` calls ``db.create_all()`` on every app start, which may
  have already created these tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'v12_001'
down_revision = 'v11_001'
branch_labels = None
depends_on = None


def _json():
    """JSONB on PostgreSQL, JSON elsewhere. Mirrors the model definitions."""
    return sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('narratives'):
        op.create_table(
            'narratives',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True),
            sa.Column('name', sa.String(length=300), nullable=False),
            sa.Column('normalized_name', sa.String(length=300), nullable=False, index=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('category', sa.String(length=40), nullable=False, server_default='unknown'),
            sa.Column('risk_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('growth_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('platform_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('keywords', _json(), nullable=True),
            sa.Column('entity_names', _json(), nullable=True),
            sa.Column('evidence', _json(), nullable=True),
            sa.Column('detection_method', sa.String(length=40), nullable=False,
                      server_default='heuristic'),
            sa.Column('first_seen_at', sa.DateTime(), nullable=False, index=True),
            sa.Column('last_seen_at', sa.DateTime(), nullable=False, index=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('user_id', 'normalized_name',
                                name='uq_narratives_user_normalized'),
        )
        op.create_index('ix_narratives_user_last_seen', 'narratives',
                        ['user_id', 'last_seen_at'])
        op.create_index('ix_narratives_user_risk', 'narratives',
                        ['user_id', 'risk_score'])

    if not inspector.has_table('narrative_occurrences'):
        op.create_table(
            'narrative_occurrences',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('narrative_id', sa.Integer(), nullable=False, index=True),
            sa.Column('analysis_id', sa.Integer(), nullable=False, index=True),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True),
            sa.Column('platform', sa.String(length=20), nullable=False,
                      server_default='unknown', index=True),
            sa.Column('source', sa.String(length=20), nullable=False, server_default='combined'),
            sa.Column('channel_id', sa.String(length=200), nullable=True, index=True),
            sa.Column('content_ref', sa.String(length=200), nullable=True, index=True),
            sa.Column('relevance_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('risk_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('match_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('evidence', _json(), nullable=True),
            sa.Column('timestamp_source', sa.String(length=20), nullable=False,
                      server_default='analysis_fallback'),
            sa.Column('occurred_at', sa.DateTime(), nullable=False, index=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['narrative_id'], ['narratives.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['analysis_id'], ['analyses.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('narrative_id', 'analysis_id', 'source',
                                name='uq_narrative_occurrence_narrative_analysis_source'),
        )
        op.create_index('ix_narrative_occurrences_narrative_ts', 'narrative_occurrences',
                        ['narrative_id', 'occurred_at'])
        op.create_index('ix_narrative_occurrences_user_ts', 'narrative_occurrences',
                        ['user_id', 'occurred_at'])

    if not inspector.has_table('coordination_signals'):
        op.create_table(
            'coordination_signals',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('analysis_id', sa.Integer(), nullable=False, index=True),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True),
            sa.Column('narrative_id', sa.Integer(), nullable=True, index=True),
            sa.Column('signal_type', sa.String(length=40), nullable=False, index=True),
            sa.Column('score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('level', sa.String(length=20), nullable=False, server_default='none'),
            sa.Column('cluster_size', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('comparisons_performed', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('comparisons_truncated', sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column('window_seconds', sa.Integer(), nullable=True),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('reasons', _json(), nullable=True),
            sa.Column('indicators', _json(), nullable=True),
            sa.Column('evidence', _json(), nullable=True),
            sa.Column('related_entities', _json(), nullable=True),
            sa.Column('detection_method', sa.String(length=40), nullable=False,
                      server_default='heuristic'),
            sa.Column('first_event_at', sa.DateTime(), nullable=True),
            sa.Column('last_event_at', sa.DateTime(), nullable=True),
            sa.Column('detected_at', sa.DateTime(), nullable=False, index=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['analysis_id'], ['analyses.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['narrative_id'], ['narratives.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('analysis_id', 'signal_type',
                                name='uq_coordination_signals_analysis_type'),
        )
        op.create_index('ix_coordination_signals_analysis_type', 'coordination_signals',
                        ['analysis_id', 'signal_type'])
        op.create_index('ix_coordination_signals_user_detected', 'coordination_signals',
                        ['user_id', 'detected_at'])

    if not inspector.has_table('propagation_events'):
        op.create_table(
            'propagation_events',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('narrative_id', sa.Integer(), nullable=True, index=True),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True),
            sa.Column('source_analysis_id', sa.Integer(), nullable=False, index=True),
            sa.Column('target_analysis_id', sa.Integer(), nullable=False, index=True),
            sa.Column('source_platform', sa.String(length=20), nullable=False,
                      server_default='unknown'),
            sa.Column('target_platform', sa.String(length=20), nullable=False,
                      server_default='unknown'),
            sa.Column('source_ref', sa.String(length=200), nullable=True),
            sa.Column('target_ref', sa.String(length=200), nullable=True),
            sa.Column('relationship_type', sa.String(length=40), nullable=False,
                      server_default='related', index=True),
            sa.Column('direction', sa.String(length=20), nullable=False,
                      server_default='undirected'),
            sa.Column('propagation_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('similarity_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('lag_seconds', sa.Float(), nullable=True),
            sa.Column('shared_entities', _json(), nullable=True),
            sa.Column('reasons', _json(), nullable=True),
            sa.Column('evidence', _json(), nullable=True),
            sa.Column('detection_method', sa.String(length=40), nullable=False,
                      server_default='heuristic'),
            sa.Column('occurred_at', sa.DateTime(), nullable=False, index=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['narrative_id'], ['narratives.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['source_analysis_id'], ['analyses.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['target_analysis_id'], ['analyses.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('narrative_id', 'source_analysis_id', 'target_analysis_id',
                                'relationship_type', name='uq_propagation_events_edge'),
        )
        op.create_index('ix_propagation_events_narrative_ts', 'propagation_events',
                        ['narrative_id', 'occurred_at'])
        op.create_index('ix_propagation_events_source_target', 'propagation_events',
                        ['source_analysis_id', 'target_analysis_id'])
        op.create_index('ix_propagation_events_user_ts', 'propagation_events',
                        ['user_id', 'occurred_at'])

    if not inspector.has_table('threat_assessments'):
        op.create_table(
            'threat_assessments',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('analysis_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False, index=True),
            sa.Column('overall_threat_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('threat_level', sa.String(length=20), nullable=False,
                      server_default='Minimal'),
            sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
            # Nullable on purpose: NULL means "signal unavailable", not "zero threat".
            sa.Column('authenticity_score', sa.Float(), nullable=True),
            sa.Column('manipulation_score', sa.Float(), nullable=True),
            sa.Column('coordination_score', sa.Float(), nullable=True),
            sa.Column('narrative_risk_score', sa.Float(), nullable=True),
            sa.Column('propagation_score', sa.Float(), nullable=True),
            sa.Column('temporal_score', sa.Float(), nullable=True),
            sa.Column('entity_risk_score', sa.Float(), nullable=True),
            sa.Column('evidence_coverage', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('agreement_score', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('component_scores', _json(), nullable=True),
            sa.Column('component_weights', _json(), nullable=True),
            sa.Column('available_components', _json(), nullable=True),
            sa.Column('missing_components', _json(), nullable=True),
            sa.Column('capability_labels', _json(), nullable=True),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('reasons', _json(), nullable=True),
            sa.Column('indicators', _json(), nullable=True),
            sa.Column('limitations', _json(), nullable=True),
            sa.Column('assessment_method', sa.String(length=40), nullable=False,
                      server_default='heuristic_weighted'),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['analysis_id'], ['analyses.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_threat_assessments_analysis_id', 'threat_assessments',
                        ['analysis_id'], unique=True)
        op.create_index('ix_threat_assessments_user_created', 'threat_assessments',
                        ['user_id', 'created_at'])
        op.create_index('ix_threat_assessments_user_score', 'threat_assessments',
                        ['user_id', 'overall_threat_score'])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Children first: propagation_events and coordination_signals reference
    # narratives, so narratives must be dropped last.
    for table in ('threat_assessments', 'propagation_events', 'coordination_signals',
                  'narrative_occurrences', 'narratives'):
        if inspector.has_table(table):
            op.drop_table(table)
