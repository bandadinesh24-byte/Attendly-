"""Persist model predictions and metadata, course groups, and status audit fields."""
from alembic import op
import sqlalchemy as sa

revision = "0002_ml_audit_similarity"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("attendance_sessions", sa.Column("course_code", sa.String(length=80), nullable=True))
    op.create_index("ix_attendance_sessions_course_code", "attendance_sessions", ["course_code"])
    op.add_column("attendance_records", sa.Column("session_group", sa.String(length=80), nullable=True))
    op.create_index("ix_attendance_records_session_group", "attendance_records", ["session_group"])
    op.add_column("attendance_records", sa.Column("model_anomaly_score", sa.Float(), nullable=True))
    op.add_column("attendance_records", sa.Column("model_prediction", sa.Integer(), nullable=True))
    op.add_column("attendance_records", sa.Column("model_version", sa.String(length=40), nullable=True))
    op.add_column("admin_actions", sa.Column("previous_status", sa.String(length=20), nullable=True))
    op.add_column("admin_actions", sa.Column("new_status", sa.String(length=20), nullable=True))
    op.add_column("model_metadata", sa.Column("training_records", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("model_metadata", sa.Column("feature_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    op.add_column("model_metadata", sa.Column("model_file_path", sa.String(length=500), nullable=True))
    op.add_column("model_metadata", sa.Column("training_status", sa.String(length=30), nullable=False, server_default="legacy"))
    op.add_column("model_metadata", sa.Column("error_message", sa.Text(), nullable=True))
    # Backfill available legacy training counts without changing existing model rows.
    op.execute("UPDATE model_metadata SET training_records = COALESCE((metrics->>'rows')::integer, 0), feature_names = COALESCE(metrics->'features', '[]'::json), model_file_path = 'backend/models/isolation_forest.joblib' WHERE name = 'IsolationForest'")

def downgrade():
    op.drop_column("model_metadata", "error_message")
    op.drop_column("model_metadata", "training_status")
    op.drop_column("model_metadata", "model_file_path")
    op.drop_column("model_metadata", "feature_names")
    op.drop_column("model_metadata", "training_records")
    op.drop_column("admin_actions", "new_status")
    op.drop_column("admin_actions", "previous_status")
    op.drop_column("attendance_records", "model_version")
    op.drop_column("attendance_records", "model_prediction")
    op.drop_column("attendance_records", "model_anomaly_score")
    op.drop_index("ix_attendance_records_session_group", table_name="attendance_records")
    op.drop_column("attendance_records", "session_group")
    op.drop_index("ix_attendance_sessions_course_code", table_name="attendance_sessions")
    op.drop_column("attendance_sessions", "course_code")
