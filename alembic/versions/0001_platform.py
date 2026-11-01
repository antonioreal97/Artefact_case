"""Initial tenant, commerce, conversation and document schema.

Revision ID: 0001_platform
Revises: None

The vector extension is shared database infrastructure and is deliberately retained
on downgrade. Embeddings are fixed at 1536 dimensions; search is Portuguese FTS.
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "tenants",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "agent_settings",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_name", sa.String(length=100), nullable=False),
        sa.Column("business_description", sa.Text(), nullable=False),
        sa.Column("tone_of_voice", sa.Text(), nullable=False),
        sa.Column("default_language", sa.String(length=16), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("handoff_message", sa.Text(), nullable=False),
        sa.Column("public_contact_values", sa.JSON(), nullable=False),
        sa.Column("retrieval_min_similarity", sa.Float(), nullable=True),
        sa.Column("chat_model", sa.String(length=100), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_table(
        "api_keys",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_prefix"),
    )
    op.create_index(op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"], unique=False)
    op.create_table(
        "audit_events",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_events_event_type"), "audit_events", ["event_type"], unique=False
    )
    op.create_index(op.f("ix_audit_events_tenant_id"), "audit_events", ["tenant_id"], unique=False)
    op.create_table(
        "commerce_categories",
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id"),
    )
    op.create_index(
        op.f("ix_commerce_categories_tenant_id"), "commerce_categories", ["tenant_id"], unique=False
    )
    op.create_table(
        "commerce_customers",
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id"),
    )
    op.create_index(
        op.f("ix_commerce_customers_tenant_id"), "commerce_customers", ["tenant_id"], unique=False
    )
    op.create_table(
        "commerce_order_items",
        sa.Column("order_external_id", sa.Integer(), nullable=False),
        sa.Column("product_external_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "order_external_id", "product_external_id"),
    )
    op.create_index(
        op.f("ix_commerce_order_items_order_external_id"),
        "commerce_order_items",
        ["order_external_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_commerce_order_items_tenant_id"),
        "commerce_order_items",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "commerce_orders",
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("customer_external_id", sa.Integer(), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("payment_method", sa.String(length=32), nullable=False),
        sa.Column("tracking_code", sa.String(length=64), nullable=True),
        sa.Column("estimated_delivery", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id"),
    )
    op.create_index(
        op.f("ix_commerce_orders_tenant_id"), "commerce_orders", ["tenant_id"], unique=False
    )
    op.create_table(
        "commerce_products",
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("category_external_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("specs", sa.JSON(), nullable=False),
        sa.Column("created_on", sa.Date(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id"),
    )
    op.create_index(
        op.f("ix_commerce_products_tenant_id"), "commerce_products", ["tenant_id"], unique=False
    )
    op.create_table(
        "commerce_promotions",
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("product_external_id", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_id"),
    )
    op.create_index(
        op.f("ix_commerce_promotions_tenant_id"), "commerce_promotions", ["tenant_id"], unique=False
    )
    op.create_table(
        "conversations",
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_user_ref", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversations_external_user_ref"),
        "conversations",
        ["external_user_ref"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversations_tenant_id"), "conversations", ["tenant_id"], unique=False
    )
    op.create_table(
        "documents",
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_documents_category"), "documents", ["category"], unique=False)
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False)
    op.create_table(
        "tenant_channels",
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "external_id"),
    )
    op.create_index(
        op.f("ix_tenant_channels_tenant_id"), "tenant_channels", ["tenant_id"], unique=False
    )
    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("raw_content", sa.LargeBinary(), nullable=False),
        sa.Column("ingestion_status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "document_id", "content_hash"),
    )
    op.create_index(
        op.f("ix_document_versions_content_hash"),
        "document_versions",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_versions_document_id"), "document_versions", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_document_versions_ingestion_status"),
        "document_versions",
        ["ingestion_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_versions_tenant_id"), "document_versions", ["tenant_id"], unique=False
    )
    op.create_table(
        "handoff_requests",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_handoff_requests_conversation_id"),
        "handoff_requests",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_handoff_requests_tenant_id"), "handoff_requests", ["tenant_id"], unique=False
    )
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_redacted", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_messages_tenant_id"), "messages", ["tenant_id"], unique=False)
    op.create_table(
        "tool_executions",
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("arguments_redacted", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tool_executions_conversation_id"),
        "tool_executions",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_executions_tenant_id"), "tool_executions", ["tenant_id"], unique=False
    )
    op.create_table(
        "chunks",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(length=300), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('portuguese', content)", persisted=True),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chunks_document_id"), "chunks", ["document_id"], unique=False)
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunks_search_vector", "chunks", ["search_vector"], unique=False, postgresql_using="gin"
    )
    op.create_index(op.f("ix_chunks_tenant_id"), "chunks", ["tenant_id"], unique=False)
    op.create_index(
        "ix_chunks_tenant_version", "chunks", ["tenant_id", "document_version_id"], unique=False
    )
    op.create_table(
        "message_sources",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("document_version_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("section", sa.String(length=300), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_message_sources_message_id"), "message_sources", ["message_id"], unique=False
    )
    op.create_index(
        op.f("ix_message_sources_tenant_id"), "message_sources", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("message_sources")
    op.drop_table("chunks")
    op.drop_table("tool_executions")
    op.drop_table("messages")
    op.drop_table("handoff_requests")
    op.drop_table("document_versions")
    op.drop_table("tenant_channels")
    op.drop_table("documents")
    op.drop_table("conversations")
    op.drop_table("commerce_promotions")
    op.drop_table("commerce_products")
    op.drop_table("commerce_orders")
    op.drop_table("commerce_order_items")
    op.drop_table("commerce_customers")
    op.drop_table("commerce_categories")
    op.drop_table("audit_events")
    op.drop_table("api_keys")
    op.drop_table("agent_settings")
    op.drop_table("tenants")
