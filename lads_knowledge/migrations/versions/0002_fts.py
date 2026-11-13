"""Add transactional standalone FTS5 trigram search."""

from __future__ import annotations

from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            knowledge_id UNINDEXED,
            project_id UNINDEXED,
            title,
            body,
            source_ref UNINDEXED,
            tokenize='trigram'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER knowledge_fts_ai
        AFTER INSERT ON knowledge_items
        WHEN NEW.status = 'active'
        BEGIN
            INSERT INTO knowledge_fts(
                knowledge_id, project_id, title, body, source_ref
            ) VALUES (
                NEW.id, NEW.project_id, NEW.title, NEW.body, NEW.source_ref
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER knowledge_fts_au
        AFTER UPDATE ON knowledge_items
        BEGIN
            DELETE FROM knowledge_fts WHERE knowledge_id = OLD.id;
            INSERT INTO knowledge_fts(
                knowledge_id, project_id, title, body, source_ref
            )
            SELECT NEW.id, NEW.project_id, NEW.title, NEW.body, NEW.source_ref
            WHERE NEW.status = 'active';
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER knowledge_fts_ad
        AFTER DELETE ON knowledge_items
        BEGIN
            DELETE FROM knowledge_fts WHERE knowledge_id = OLD.id;
        END
        """
    )
    op.execute(
        """
        INSERT INTO knowledge_fts(knowledge_id, project_id, title, body, source_ref)
        SELECT id, project_id, title, body, source_ref
        FROM knowledge_items
        WHERE status = 'active'
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER knowledge_fts_ad")
    op.execute("DROP TRIGGER knowledge_fts_au")
    op.execute("DROP TRIGGER knowledge_fts_ai")
    op.execute("DROP TABLE knowledge_fts")
