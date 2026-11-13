"""Create the frozen ten-table relational schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This DDL intentionally remains frozen and does not import current models.
    op.execute("PRAGMA application_id = 1279345747")
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("root_ref", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("name", name="uq_projects_name"),
    )
    op.create_table(
        "dev_tasks",
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("requirement_summary", sa.Text(), nullable=False),
        sa.Column("assigned_agent", sa.String(100), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("pr_ref", sa.String(200), nullable=True),
        sa.Column("test_result", sa.String(200), nullable=True),
        sa.Column("review_result", sa.String(200), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("completed_time", sa.String(32), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_dev_tasks_project", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("project_id", "id", name="pk_dev_tasks"),
        sa.CheckConstraint(
            "state IN ('planned','active','testing','review','blocked','failed','completed','cancelled')",
            name="ck_dev_tasks_state",
        ),
    )
    op.create_index("ix_dev_tasks_project_state", "dev_tasks", ["project_id", "state"])
    op.create_table(
        "task_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_task_events_project_task",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("json_valid(payload_json)", name="ck_task_events_json"),
    )
    op.create_index(
        "ix_task_events_project_task_time",
        "task_events",
        ["project_id", "task_id", "occurred_at"],
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("build_number", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("completed_at", sa.String(32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_agent_runs_project_task",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "task_id",
            "role",
            "build_number",
            "attempt",
            name="uq_agent_runs_task_role_build_attempt",
        ),
        sa.UniqueConstraint(
            "project_id", "task_id", "id", name="uq_agent_runs_project_task_id"
        ),
        sa.CheckConstraint("build_number >= 1", name="ck_agent_runs_build"),
        sa.CheckConstraint("attempt >= 1", name="ck_agent_runs_attempt"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_agent_runs_input_tokens"
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_agent_runs_output_tokens"
        ),
    )
    op.create_index(
        "ix_agent_runs_project_task_started",
        "agent_runs",
        ["project_id", "task_id", "started_at"],
    )
    op.create_table(
        "code_changes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("diff_sha", sa.String(64), nullable=True),
        sa.Column("source_ref", sa.String(1000), nullable=False),
        sa.Column("source_raw_sha", sa.String(64), nullable=True),
        sa.Column("sanitized_text_sha", sa.String(64), nullable=True),
        sa.Column("observed_head", sa.String(64), nullable=True),
        sa.Column("actual_content_commit_sha", sa.String(64), nullable=True),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("indexed_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_code_changes_project_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id", "run_id"],
            ["agent_runs.project_id", "agent_runs.task_id", "agent_runs.id"],
            name="fk_code_changes_project_task_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("dirty IN (0,1)", name="ck_code_changes_dirty"),
    )
    op.create_index(
        "ix_code_changes_project_task", "code_changes", ["project_id", "task_id"]
    )
    op.create_index(
        "ix_code_changes_project_source", "code_changes", ["project_id", "source_ref"]
    )
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("test_name", sa.String(300), nullable=False),
        sa.Column("command", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("output_sha", sa.String(64), nullable=True),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("completed_at", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_test_runs_project_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id", "run_id"],
            ["agent_runs.project_id", "agent_runs.task_id", "agent_runs.id"],
            name="fk_test_runs_project_task_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_test_runs_project_task_started",
        "test_runs",
        ["project_id", "task_id", "started_at"],
    )
    op.create_table(
        "architecture_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("decision_text", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(1000), nullable=False),
        sa.Column("source_raw_sha", sa.String(64), nullable=True),
        sa.Column("sanitized_text_sha", sa.String(64), nullable=True),
        sa.Column("observed_head", sa.String(64), nullable=True),
        sa.Column("actual_content_commit_sha", sa.String(64), nullable=True),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("indexed_at", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_architecture_decisions_project_task",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "source_ref", name="uq_architecture_decisions_project_source"
        ),
        sa.CheckConstraint(
            "dirty IN (0,1)", name="ck_architecture_decisions_dirty"
        ),
    )
    op.create_index(
        "ix_architecture_decisions_project_status",
        "architecture_decisions",
        ["project_id", "status"],
    )
    op.create_table(
        "known_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(1000), nullable=False),
        sa.Column("source_raw_sha", sa.String(64), nullable=True),
        sa.Column("sanitized_text_sha", sa.String(64), nullable=True),
        sa.Column("observed_head", sa.String(64), nullable=True),
        sa.Column("actual_content_commit_sha", sa.String(64), nullable=True),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("indexed_at", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_known_issues_project_task",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("project_id", "source_ref", name="uq_known_issues_project_source"),
        sa.CheckConstraint(
            "severity IN ('low','medium','high','critical')",
            name="ck_known_issues_severity",
        ),
        sa.CheckConstraint("dirty IN (0,1)", name="ck_known_issues_dirty"),
    )
    op.create_index(
        "ix_known_issues_project_severity_status",
        "known_issues",
        ["project_id", "severity", "status"],
    )
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(1000), nullable=False),
        sa.Column("source_raw_sha", sa.String(64), nullable=True),
        sa.Column("sanitized_text_sha", sa.String(64), nullable=True),
        sa.Column("observed_head", sa.String(64), nullable=True),
        sa.Column("actual_content_commit_sha", sa.String(64), nullable=True),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("indexed_at", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_knowledge_items_project_task",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "kind", "source_ref", name="uq_knowledge_project_kind_source"
        ),
        sa.CheckConstraint(
            "status IN ('active','tombstoned')", name="ck_knowledge_items_status"
        ),
        sa.CheckConstraint("dirty IN (0,1)", name="ck_knowledge_items_dirty"),
    )
    op.create_index(
        "ix_knowledge_items_project_kind_status",
        "knowledge_items",
        ["project_id", "kind", "status"],
    )
    op.create_table(
        "model_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "task_id", "run_id"],
            ["agent_runs.project_id", "agent_runs.task_id", "agent_runs.id"],
            name="fk_model_usage_project_task_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("project_id", "run_id", name="uq_model_usage_project_run"),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_model_usage_input_tokens"
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_model_usage_output_tokens"
        ),
    )
    op.create_index("ix_model_usage_project_run", "model_usage", ["project_id", "run_id"])


def downgrade() -> None:
    op.drop_index("ix_model_usage_project_run", table_name="model_usage")
    op.drop_table("model_usage")
    op.drop_index(
        "ix_knowledge_items_project_kind_status", table_name="knowledge_items"
    )
    op.drop_table("knowledge_items")
    op.drop_index(
        "ix_known_issues_project_severity_status", table_name="known_issues"
    )
    op.drop_table("known_issues")
    op.drop_index(
        "ix_architecture_decisions_project_status", table_name="architecture_decisions"
    )
    op.drop_table("architecture_decisions")
    op.drop_index("ix_test_runs_project_task_started", table_name="test_runs")
    op.drop_table("test_runs")
    op.drop_index("ix_code_changes_project_source", table_name="code_changes")
    op.drop_index("ix_code_changes_project_task", table_name="code_changes")
    op.drop_table("code_changes")
    op.drop_index("ix_agent_runs_project_task_started", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_task_events_project_task_time", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_dev_tasks_project_state", table_name="dev_tasks")
    op.drop_table("dev_tasks")
    op.drop_table("projects")

