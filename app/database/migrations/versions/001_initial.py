"""Initial v1 schema with prefixed table names."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "v1_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_v1_users_id"), "v1_users", ["id"], unique=False)
    op.create_index(op.f("ix_v1_users_username"), "v1_users", ["username"], unique=True)

    op.create_table(
        "v1_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["v1_users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_v1_documents_content_hash"), "v1_documents", ["content_hash"], unique=True)
    op.create_index(op.f("ix_v1_documents_id"), "v1_documents", ["id"], unique=False)

    op.create_table(
        "v1_check_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("similarity_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("matches", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["v1_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_v1_check_results_document_id"), "v1_check_results", ["document_id"], unique=False)
    op.create_index(op.f("ix_v1_check_results_id"), "v1_check_results", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_v1_check_results_id"), table_name="v1_check_results")
    op.drop_index(op.f("ix_v1_check_results_document_id"), table_name="v1_check_results")
    op.drop_table("v1_check_results")
    op.drop_index(op.f("ix_v1_documents_id"), table_name="v1_documents")
    op.drop_index(op.f("ix_v1_documents_content_hash"), table_name="v1_documents")
    op.drop_table("v1_documents")
    op.drop_index(op.f("ix_v1_users_username"), table_name="v1_users")
    op.drop_index(op.f("ix_v1_users_id"), table_name="v1_users")
    op.drop_table("v1_users")
