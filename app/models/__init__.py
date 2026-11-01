"""Import every model so Base.metadata is complete (Alembic autogenerate, tests)."""

from app.models.commerce import (
    CommerceCategory,
    CommerceCustomer,
    CommerceOrder,
    CommerceOrderItem,
    CommerceProduct,
    CommercePromotion,
)
from app.models.conversation import Conversation, Message, MessageSource, ToolExecution
from app.models.handoff import HANDOFF_REASONS, AuditEvent, HandoffRequest
from app.models.knowledge import EMBEDDING_DIM, Chunk, Document, DocumentVersion
from app.models.tenant import AgentSettings, ApiKey, Tenant, TenantChannel

__all__ = [
    "EMBEDDING_DIM",
    "HANDOFF_REASONS",
    "AgentSettings",
    "ApiKey",
    "AuditEvent",
    "Chunk",
    "CommerceCategory",
    "CommerceCustomer",
    "CommerceOrder",
    "CommerceOrderItem",
    "CommerceProduct",
    "CommercePromotion",
    "Conversation",
    "Document",
    "DocumentVersion",
    "HandoffRequest",
    "Message",
    "MessageSource",
    "Tenant",
    "TenantChannel",
    "ToolExecution",
]
