"""Hybrid retrieval through one tenant-scoped SQL statement.

RRF combines cosine and Portuguese FTS candidate ranks. A candidate passes when
cosine similarity >= min_similarity, when an FTS hit has cosine similarity >= half
that threshold, or when it contains at least half of the query's distinct lexemes
(lexical coverage) and has similarity >= a quarter of the threshold. FTS alone never
removes the relevance floor. ``sufficient`` needs one returned chunk that clears the
threshold or the coverage rule; FTS hits on one generic word ("loja") never do.
Chunks flagged ``suspicious_instructions`` at ingestion are never served. No unaccent
extension is required; Portuguese stemming follows the stored search vector.
"""

# ruff: noqa: S608 -- SQL concatenates constant clauses; values use bound parameters.
from __future__ import annotations

import json
import math
import re
import unicodedata
import uuid
from difflib import SequenceMatcher

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.rag.types import EmbeddingProvider, RetrievalFilters, RetrievalResult, RetrievedChunk


def _normalized(content: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", content).casefold()))


def _covers_query(matched_terms: int, query_terms: int) -> bool:
    """A chunk containing at least half of the query's distinct lexemes is on topic."""
    return matched_terms > 0 and 2 * matched_terms >= query_terms


def _near_duplicate(content: str, seen: list[str]) -> bool:
    candidate = _normalized(content)
    return any(
        candidate == prior
        or SequenceMatcher(None, candidate, prior, autojunk=False).ratio() >= 0.94
        for prior in seen
    )


async def retrieve(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    query: str,
    embedder: EmbeddingProvider,
    filters: RetrievalFilters | None = None,
    top_k: int | None = None,
    min_similarity: float | None = None,
) -> RetrievalResult:
    settings = get_settings()
    filters = filters or RetrievalFilters()
    limit = top_k if top_k is not None else settings.retrieval_top_k
    threshold = min_similarity if min_similarity is not None else settings.retrieval_min_similarity
    if not query.strip() or limit <= 0 or filters.document_ids == [] or filters.categories == []:
        return RetrievalResult(query=query, chunks=[], best_similarity=None, sufficient=False)
    if not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError("Invalid similarity threshold")
    vector = await embedder.embed_query(query)
    if len(vector) != embedder.dimensions or any(not math.isfinite(v) for v in vector):
        raise ValueError("Invalid query embedding")
    extra = ""
    parameters: dict[str, object] = {
        "tenant": tenant_id,
        "status": filters.document_status,
        "query": query,
        "embedding": json.dumps(vector),
        "candidate_k": max(1, settings.retrieval_candidate_k),
        "rrf_k": max(1, settings.rrf_k),
    }
    expanding: list[str] = []
    if filters.document_ids is not None:
        extra += " AND d.id IN :document_ids"
        parameters["document_ids"] = filters.document_ids
        expanding.append("document_ids")
    if filters.categories is not None:
        extra += " AND d.category IN :categories"
        parameters["categories"] = filters.categories
        expanding.append("categories")
    # Only constant SQL clauses are concatenated; all filter values are bound.
    # FTS uses OR semantics over the query lexemes (plainto_tsquery with & -> |): full
    # questions rarely share every lexeme with the answer chunk; ts_rank_cd still rewards
    # chunks matching more lexemes, and the similarity/coverage gate below still applies.
    statement = text(
        """
        WITH eligible AS NOT MATERIALIZED (
            SELECT c.id AS chunk_id, c.document_id, c.document_version_id,
                   d.title AS document_title, d.category, d.source_url,
                   c.section_title, c.page_start, c.page_end, c.content,
                   c.embedding, c.search_vector
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.tenant_id = :tenant AND d.tenant_id = :tenant
              AND c.document_version_id = d.current_version_id
              AND d.status = :status
              AND COALESCE(c.metadata ->> 'suspicious_instructions', 'false') <> 'true'
        """
        + extra
        + """
        ), terms AS (
            SELECT tsvector_to_array(to_tsvector('portuguese', :query)) AS lexemes,
                   replace(plainto_tsquery('portuguese', :query)::text, ' & ', ' | ')::tsquery
                       AS any_term
        ), vector_hits AS (
            SELECT chunk_id, row_number() OVER (
                ORDER BY embedding <=> CAST(:embedding AS vector), chunk_id
            ) AS position
            FROM eligible
            ORDER BY embedding <=> CAST(:embedding AS vector), chunk_id
            LIMIT :candidate_k
        ), text_hits AS (
            SELECT chunk_id, ts_rank_cd(search_vector, terms.any_term) AS text_rank,
                   cardinality(ARRAY(
                       SELECT unnest(terms.lexemes)
                       INTERSECT SELECT unnest(tsvector_to_array(search_vector))
                   )) AS matched_terms,
                   row_number() OVER (
                       ORDER BY ts_rank_cd(search_vector, terms.any_term) DESC, chunk_id
                   ) AS position
            FROM eligible, terms
            WHERE search_vector @@ terms.any_term
            ORDER BY text_rank DESC, chunk_id
            LIMIT :candidate_k
        ), candidates AS (
            SELECT chunk_id FROM vector_hits UNION SELECT chunk_id FROM text_hits
        )
        SELECT e.chunk_id, e.document_id, e.document_version_id, e.document_title,
               e.category, e.source_url, e.section_title, e.page_start, e.page_end,
               e.content, 1 - (e.embedding <=> CAST(:embedding AS vector)) AS vector_similarity,
               t.text_rank, COALESCE(t.matched_terms, 0) AS matched_terms,
               (SELECT cardinality(lexemes) FROM terms) AS query_terms,
               COALESCE(1.0 / (:rrf_k + v.position), 0) +
               COALESCE(1.0 / (:rrf_k + t.position), 0) AS score
        FROM candidates c JOIN eligible e ON e.chunk_id = c.chunk_id
        LEFT JOIN vector_hits v ON v.chunk_id = c.chunk_id
        LEFT JOIN text_hits t ON t.chunk_id = c.chunk_id
        ORDER BY score DESC, vector_similarity DESC, e.chunk_id
    """
    )
    for name in expanding:
        statement = statement.bindparams(bindparam(name, expanding=True))
    rows = (await session.execute(statement, parameters)).mappings()
    chunks: list[RetrievedChunk] = []
    seen: list[str] = []
    best: float | None = None
    sufficient = False
    for row in rows:
        similarity = float(row["vector_similarity"])
        if not math.isfinite(similarity):
            continue
        best = max(best, similarity) if best is not None else similarity
        if len(chunks) >= limit:
            continue
        lexical = similarity >= threshold * 0.25 and _covers_query(
            int(row["matched_terms"]), int(row["query_terms"] or 0)
        )
        if (
            similarity < threshold
            and not lexical
            and (row["text_rank"] is None or similarity < threshold * 0.5)
        ):
            continue
        if _near_duplicate(row["content"], seen):
            continue
        chunks.append(RetrievedChunk.model_validate(dict(row)))
        seen.append(_normalized(row["content"]))
        # Weak FTS hits (one generic word like "loja") are context, never sufficiency.
        sufficient = sufficient or similarity >= threshold or lexical
    return RetrievalResult(query=query, chunks=chunks, best_similarity=best, sufficient=sufficient)
