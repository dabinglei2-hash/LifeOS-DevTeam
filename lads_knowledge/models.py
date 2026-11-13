"""Frozen SQLAlchemy metadata for the ten LADS knowledge tables."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class SourceMixin:
    source_ref: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_raw_sha: Mapped[str | None] = mapped_column(String(64))
    sanitized_text_sha: Mapped[str | None] = mapped_column(String(64))
    observed_head: Mapped[str | None] = mapped_column(String(64))
    actual_content_commit_sha: Mapped[str | None] = mapped_column(String(64))
    dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    indexed_at: Mapped[str] = mapped_column(String(32), nullable=False)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("name", name="uq_projects_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    root_ref: Mapped[str | None] = mapped_column(String(1000))


class DevTask(TimestampMixin, Base):
    __tablename__ = "dev_tasks"
    __table_args__ = (
        CheckConstraint(
            "state IN ('planned','active','testing','review','blocked','failed','completed','cancelled')",
            name="ck_dev_tasks_state",
        ),
        Index("ix_dev_tasks_project_state", "project_id", "state"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement_summary: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_agent: Mapped[str | None] = mapped_column(String(100))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    pr_ref: Mapped[str | None] = mapped_column(String(200))
    test_result: Mapped[str | None] = mapped_column(String(200))
    review_result: Mapped[str | None] = mapped_column(String(200))
    error_reason: Mapped[str | None] = mapped_column(Text)
    completed_time: Mapped[str | None] = mapped_column(String(32))


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_task_events_project_task",
            ondelete="CASCADE",
        ),
        CheckConstraint("json_valid(payload_json)", name="ck_task_events_json"),
        Index("ix_task_events_project_task_time", "project_id", "task_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(32), nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_agent_runs_project_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id",
            "task_id",
            "role",
            "build_number",
            "attempt",
            name="uq_agent_runs_task_role_build_attempt",
        ),
        UniqueConstraint(
            "project_id", "task_id", "id", name="uq_agent_runs_project_task_id"
        ),
        CheckConstraint("build_number >= 1", name="ck_agent_runs_build"),
        CheckConstraint("attempt >= 1", name="ck_agent_runs_attempt"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_agent_runs_input_tokens"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_agent_runs_output_tokens"
        ),
        Index("ix_agent_runs_project_task_started", "project_id", "task_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    build_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_reason: Mapped[str | None] = mapped_column(Text)


class CodeChange(SourceMixin, Base):
    __tablename__ = "code_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_code_changes_project_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id", "run_id"],
            ["agent_runs.project_id", "agent_runs.task_id", "agent_runs.id"],
            name="fk_code_changes_project_task_run",
            ondelete="CASCADE",
        ),
        CheckConstraint("dirty IN (0,1)", name="ck_code_changes_dirty"),
        Index("ix_code_changes_project_task", "project_id", "task_id"),
        Index("ix_code_changes_project_source", "project_id", "source_ref"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36))
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    diff_sha: Mapped[str | None] = mapped_column(String(64))


class TestRun(Base):
    __tablename__ = "test_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_test_runs_project_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_id", "run_id"],
            ["agent_runs.project_id", "agent_runs.task_id", "agent_runs.id"],
            name="fk_test_runs_project_task_run",
            ondelete="CASCADE",
        ),
        Index("ix_test_runs_project_task_started", "project_id", "task_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36))
    test_name: Mapped[str] = mapped_column(String(300), nullable=False)
    command: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    output_sha: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(32))


class ArchitectureDecision(SourceMixin, TimestampMixin, Base):
    __tablename__ = "architecture_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_architecture_decisions_project_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id", "source_ref", name="uq_architecture_decisions_project_source"
        ),
        CheckConstraint("dirty IN (0,1)", name="ck_architecture_decisions_dirty"),
        Index("ix_architecture_decisions_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    decision_text: Mapped[str] = mapped_column(Text, nullable=False)


class KnownIssue(SourceMixin, TimestampMixin, Base):
    __tablename__ = "known_issues"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_known_issues_project_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "source_ref", name="uq_known_issues_project_source"),
        CheckConstraint(
            "severity IN ('low','medium','high','critical')",
            name="ck_known_issues_severity",
        ),
        CheckConstraint("dirty IN (0,1)", name="ck_known_issues_dirty"),
        Index("ix_known_issues_project_severity_status", "project_id", "severity", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)


class KnowledgeItem(SourceMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id"],
            ["dev_tasks.project_id", "dev_tasks.id"],
            name="fk_knowledge_items_project_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "project_id", "kind", "source_ref", name="uq_knowledge_project_kind_source"
        ),
        CheckConstraint(
            "status IN ('active','tombstoned')", name="ck_knowledge_items_status"
        ),
        CheckConstraint("dirty IN (0,1)", name="ck_knowledge_items_dirty"),
        Index("ix_knowledge_items_project_kind_status", "project_id", "kind", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class ModelUsage(Base):
    __tablename__ = "model_usage"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_id", "run_id"],
            ["agent_runs.project_id", "agent_runs.task_id", "agent_runs.id"],
            name="fk_model_usage_project_task_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "run_id", name="uq_model_usage_project_run"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="ck_model_usage_input_tokens"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_model_usage_output_tokens"
        ),
        Index("ix_model_usage_project_run", "project_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

