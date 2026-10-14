"""Query construction.

The retrieval query is the agent's request text plus project context: languages
in use, framework, requested category, installed dependencies, and a short
summary of the file being edited. Keyword and vector search read the same text
but use it differently, so the two get separate renderings of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ml.schemas import Request


@dataclass(frozen=True)
class RetrievalQuery:
    request: Request

    # Input: none
    # Output: str
    # Processing: Lexical surface. Identifiers in the request text matter most here --
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def keyword_text(self) -> str:
        """Lexical surface. Identifiers in the request text matter most here --
        a query naming ``stratified_split`` should hit it exactly -- so context
        is appended but kept short enough not to drown the request out."""
        ctx = self.request.project_context
        parts = [self.request.query, *ctx.languages]
        if ctx.framework:
            parts.append(ctx.framework)
        if ctx.category:
            parts.append(ctx.category)
        return " ".join(parts)

    # Input: none
    # Output: str
    # Processing: Semantic surface: the full context, written out, so the embedding
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def vector_text(self) -> str:
        """Semantic surface: the context the *filter* cannot express, written
        out, so the embedding model can match meaning when the words differ.

        Language is deliberately absent. ``FilterSpec`` already restricts both
        arms to the requested language, so naming it here cannot change which
        entries are reachable -- it can only spend pooled-vector mass on a
        token that is constant across every surviving candidate, pulling the
        query toward a per-language centroid instead of toward its own meaning.
        Measured on 350 queries with UniXcoder, dropping it from the query text
        and from ``entry_embedding_text`` moved fused retrieval from
        47.1%/66.6%/79.1% to 52.0%/69.7%/81.4% at @1/@3/@10. See issue 31.

        ``category`` is hard-filtered too, so the same argument applies to it,
        but no query in the gold set sets one and an unmeasured change is not
        worth making. ``framework``, ``dependencies`` and ``file_summary`` are
        not filters: nothing else carries them, so they stay.
        """
        ctx = self.request.project_context
        lines = [self.request.query]
        if ctx.framework:
            lines.append(f"framework: {ctx.framework}")
        if ctx.category:
            lines.append(f"category: {ctx.category}")
        if ctx.dependencies:
            lines.append(f"dependencies: {', '.join(ctx.dependencies)}")
        if ctx.file_summary:
            lines.append(f"editing: {ctx.file_summary}")
        return "\n".join(lines)

    # Input: none
    # Output: str
    # Processing: The query half of the cross-encoder's input pair.
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def cross_encoder_text(self) -> str:
        """The query half of the cross-encoder's input pair."""
        return self.vector_text


# Input: request (Request)
# Output: RetrievalQuery
# Processing: Build query
# Changed by: Claude Code
# Date: 2026-10-12
def build_query(request: Request) -> RetrievalQuery:
    return RetrievalQuery(request=request)
