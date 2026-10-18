"""The models behind the MCP tools, loaded once and shared by every call.

Loading is lazy: the server answers ``initialize`` at once and pays for the
encoder, the library embeddings and the cross-encoder on the first tool call
that needs them. Library embeddings are cached in ``mcp_server/.cache`` keyed
by the library file's hash, so only the first start after a library change
re-encodes it (about a minute on a laptop CPU for ~400 entries).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ml.ingest.extract import extract, language_for_path
from ml.ingest.find_helpers import find_helpers
from ml.retrieval.query import build_query
from ml.schemas import Candidate, Entry, EntryType, ProjectContext, Request

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "seed" / "entries.jsonl"
BASE_ENCODER = ROOT / "weights" / "unixcoder-base"
CROSS_ENCODER = ROOT / "weights" / "cross-encoder-stage2" / "cross_encoder_final.pt"
CROSS_ENCODER_VERSION = "cross-encoder-20261017T142721Z"
CACHE = Path(__file__).resolve().parent / ".cache"

SAME_THRESHOLD = 0.92
"""Cosine at which a file's helper counts as already in the library.

The duplicate line from :mod:`ml.reuse.similarity`. Checked on one file
only, so treat it as a starting point: a copy of axios's combineURLs scored
0.994 against the library entry, and a different URL-joining helper scored
0.487 against the same entry."""

SIMILAR_THRESHOLD = 0.75
"""Cosine above which a library entry is worth showing as a neighbour."""


# Input: none
# Output: int
# Processing: How many shortlisted candidates the cross-encoder scores.
# Changed by: Claude Code
# Date: 2026-10-18
def rerank_depth(device: str) -> int:
    """How many shortlisted candidates the cross-encoder scores per query.

    100 on a GPU (@1 69.7%, under a second); 20 on CPU (@1 67.1%, a few
    seconds instead of 10-30). ``PLOVANE_RERANK_DEPTH`` overrides both.
    """
    override = os.environ.get("PLOVANE_RERANK_DEPTH")
    if override:
        return max(1, int(override))
    return 100 if device != "cpu" else 20


# Input: path (Path)
# Output: str
# Processing: Short SHA-256 of a file.
# Changed by: Claude Code
# Date: 2026-10-18
def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# Input: unit (ExtractedUnit)
# Output: Entry
# Processing: A function from the user's file, shaped like a library entry.
# Changed by: Claude Code
# Date: 2026-10-18
def unit_as_entry(unit) -> Entry:
    """A function from the user's file, shaped like a library entry so the
    encoders read it exactly as they read the library."""
    return Entry(
        id=f"file:{unit.name}", type=EntryType.HELPER, name=unit.name,
        description=unit.docstring, category="", language=unit.language,
        signature=unit.signature, docstring=unit.docstring, code=unit.code,
    )


# Input: entry (Entry), full (bool)
# Output: dict[str, Any]
# Processing: A library entry as JSON.
# Changed by: Claude Code
# Date: 2026-10-18
def entry_record(entry: Entry, *, full: bool = False) -> dict[str, Any]:
    record = {
        "block_id": entry.id,
        "name": entry.name,
        "language": entry.language,
        "category": entry.category,
        "signature": entry.signature,
        "description": entry.description,
        "license": entry.license,
        "source_permalink": entry.source_permalink,
    }
    if full:
        record |= {
            "type": entry.type.value,
            "docstring": entry.docstring,
            "dependencies": list(entry.dependencies),
            "tags": list(entry.tags),
            "framework": entry.framework,
            "source_url": entry.source_url,
            "code": entry.code,
        }
    return record


@dataclass
class Engine:
    """Everything the tools need. Build with :meth:`load`, or pass pieces in
    directly (the tests use the hashing placeholder so they need no torch)."""

    entries: list[Entry]
    embedding_model: Any
    cross_encoder: Any | None = None
    device: str = "cpu"
    load_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    # Input: none
    # Output: None
    # Processing: Build retrieval, the pipeline and the duplicate index.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def __post_init__(self) -> None:
        from ml.ranking.pipeline import ModelVersions, RecommendationPipeline
        from ml.ranking.stage1 import LinearFeatureRanker
        from ml.ranking.stage3 import FinalRanker, LinearFinalRanker
        from ml.retrieval.keyword import InMemoryBM25Index
        from ml.retrieval.service import RetrievalService
        from ml.retrieval.vector import InMemoryVectorIndex
        from ml.reuse.similarity import LibraryIndex

        self.by_id = {e.id: e for e in self.entries}
        self.retrieval = RetrievalService(
            keyword_index=InMemoryBM25Index(self.entries),
            vector_index=InMemoryVectorIndex(self.entries),
            embedding_model=self.embedding_model,
            entry_store=self.by_id,
        )
        self.pipeline = RecommendationPipeline(
            retrieval=self.retrieval,
            stage1_ranker=LinearFeatureRanker(),
            final_ranker=FinalRanker(model=LinearFinalRanker(), version="hand"),
            cross_encoder=self.cross_encoder,
            stage1_top_k=rerank_depth(self.device) if self.cross_encoder is not None else 100,
            versions=ModelVersions(
                cross_encoder=CROSS_ENCODER_VERSION if self.cross_encoder is not None else "none"
            ),
        )
        self.library_index = LibraryIndex(self.entries)
        # Scoring flips the cross-encoder's train/eval mode, so calls must not
        # overlap (docs/b06_recommendation_api.md).
        self.lock = threading.Lock()

    # Input: none
    # Output: Engine
    # Processing: Load the library, encoder and cross-encoder from disk.
    # Changed by: Claude Code
    # Date: 2026-10-18
    @classmethod
    def load(cls) -> Engine:
        """Load the real models. Raises with the fix when a file is missing."""
        started = time.time()
        if not LIBRARY.exists():
            raise RuntimeError(
                f"{LIBRARY.relative_to(ROOT)} is missing. Run: python -m mcp_server.setup"
            )
        if not BASE_ENCODER.exists():
            raise RuntimeError("weights/unixcoder-base is missing. Run: python -m mcp_server.setup")

        import torch

        from ml.ingest.dataset import load_dataset
        from ml.retrieval.bi_encoder import CodeBiEncoder

        device = "cuda" if torch.cuda.is_available() else "cpu"
        entries = list(load_dataset(LIBRARY).entries)
        encoder = CodeBiEncoder(str(BASE_ENCODER), dimension=768).to(device)
        encoder.eval()

        notes: list[str] = []
        cache = CACHE / f"library-{file_hash(LIBRARY)}.npy"
        if cache.exists():
            vectors = np.load(cache)
        else:
            with torch.no_grad():
                vectors = np.asarray(encoder.encode_entries(entries), dtype=np.float32)
            CACHE.mkdir(exist_ok=True)
            np.save(cache, vectors)
            notes.append("library embeddings computed and cached")
        for entry, vector in zip(entries, vectors, strict=True):
            entry.embedding = vector

        cross_encoder = None
        if os.environ.get("PLOVANE_NO_CROSS_ENCODER"):
            notes.append("cross-encoder disabled by PLOVANE_NO_CROSS_ENCODER")
        elif CROSS_ENCODER.exists():
            from ml.ranking.cross_encoder import load_cross_encoder

            cross_encoder = load_cross_encoder(CROSS_ENCODER, encoder_name=str(BASE_ENCODER), device=device)
        else:
            notes.append(
                "trained cross-encoder not found: results use retrieval order (@1 52% instead "
                "of 70%). Run: python -m mcp_server.setup"
            )
        return cls(
            entries=entries, embedding_model=encoder, cross_encoder=cross_encoder,
            device=device, load_seconds=time.time() - started, notes=notes,
        )

    # -- Basic ------------------------------------------------------------------

    # Input: query (str), language (str | None), k (int), dependencies (list[str]), drop_in_only (bool)
    # Output: dict[str, Any]
    # Processing: Search the library with the full B06 pipeline.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def search(
        self, query: str, *, language: str | None = None, k: int = 5,
        dependencies: list[str] | None = None, drop_in_only: bool = False,
    ) -> dict[str, Any]:
        """The §4.1 envelope that docs/b06_recommendation_api.md documents."""
        request = Request(
            query=query,
            project_context=ProjectContext(
                languages=(language.lower(),) if language else (),
                dependencies=tuple(dependencies or ()),
            ),
            k=k,
            drop_in_only=drop_in_only,
        )
        with self.lock:
            response, _ = self.pipeline.recommend(request)
        return response.to_handbook_response()

    # Input: block_id (str)
    # Output: dict[str, Any]
    # Processing: One library entry, code included.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def get(self, block_id: str) -> dict[str, Any]:
        entry = self.by_id.get(block_id)
        if entry is None:
            close = [i for i in self.by_id if block_id.lower() in i.lower()][:10]
            return {"error": f"no entry {block_id!r}", "did_you_mean": close}
        return entry_record(entry, full=True)

    # Input: language (str | None)
    # Output: dict[str, Any]
    # Processing: Library contents by category and language.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def categories(self, language: str | None = None) -> dict[str, Any]:
        entries = [e for e in self.entries if language is None or e.language == language.lower()]
        return {
            "total": len(entries),
            "categories": dict(Counter(e.category for e in entries).most_common()),
            "languages": dict(Counter(e.language for e in entries).most_common()),
        }

    # Input: none
    # Output: dict[str, Any]
    # Processing: What is loaded and how good it is.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def status(self) -> dict[str, Any]:
        return {
            "library_entries": len(self.entries),
            "embedding_model": type(self.embedding_model).__name__,
            "cross_encoder": CROSS_ENCODER_VERSION if self.cross_encoder is not None else None,
            "device": self.device,
            "rerank_depth": self.pipeline.stage1_top_k if self.cross_encoder is not None else 0,
            "load_seconds": round(self.load_seconds, 1),
            "measured_accuracy": (
                {"@1": 0.697, "@3": 0.883, "@10": 0.960} if self.cross_encoder is not None
                else {"@1": 0.520, "@3": 0.697, "@10": 0.814}
            ),
            "certified": False,
            "notes": self.notes,
        }

    # -- Complex ----------------------------------------------------------------

    # Input: query (str), candidates (list[dict]), language (str | None)
    # Output: dict[str, Any]
    # Processing: Cross-encoder scores for caller-supplied code.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def rerank(self, query: str, candidates: list[dict], *, language: str | None = None) -> dict[str, Any]:
        """Score the caller's own snippets against a query, best first."""
        if self.cross_encoder is None:
            return {"error": "the cross-encoder is not loaded; see model_status"}
        entries = [
            Entry(
                id=c.get("id") or f"candidate:{i}", type=EntryType.HELPER,
                name=c.get("name", f"candidate_{i}"), description=c.get("description", ""),
                category="", language=(c.get("language") or language or "").lower(),
                signature=c.get("signature"), docstring=c.get("docstring", ""), code=c.get("code", ""),
            )
            for i, c in enumerate(candidates)
        ]
        request = Request(query=query, project_context=ProjectContext(
            languages=(language.lower(),) if language else ()))
        with self.lock:
            scores = self.cross_encoder.score_candidates(
                build_query(request).cross_encoder_text, [Candidate(entry=e) for e in entries])
        ranked = sorted(zip(entries, scores), key=lambda pair: -float(pair[1]))
        return {
            "query": query,
            "ranked": [{"id": e.id, "name": e.name, "score": round(float(s), 4)} for e, s in ranked],
            "note": "scores are logits: they order these candidates, they are not probabilities",
        }

    # Input: source (str), path (str)
    # Output: list[tuple[ExtractedUnit, np.ndarray]]
    # Processing: The file's functions with their embeddings.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def _embedded_units(self, source: str, path: str):
        units = {u.name: u for u in extract(source, path) if not u.is_template}
        ordered = sorted(units.values(), key=lambda u: u.start_line)
        if not ordered:
            return []
        vectors = self.embedding_model.encode_entries([unit_as_entry(u) for u in ordered])
        return list(zip(ordered, vectors, strict=True))

    # Input: source (str), path (str)
    # Output: dict[str, Any]
    # Processing: Mark each function in a file as in the library or new.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def check_file(self, source: str, path: str) -> dict[str, Any]:
        """For each function in the file: is it already in the library?"""
        language = language_for_path(path)
        if language is None:
            return {"file": path, "error": "unsupported file type"}
        helpers = {h["name"] for h in find_helpers(source, path)["helpers"]}
        results = []
        with self.lock:
            embedded = self._embedded_units(source, path)
        for unit, vector in embedded:
            report = self.library_index.compare(vector, language=language)
            best = report.best
            similarity = best.similarity if best else 0.0
            verdict = (
                "in_library" if similarity >= SAME_THRESHOLD
                else "similar_exists" if similarity >= SIMILAR_THRESHOLD
                else "new"
            )
            results.append({
                "name": unit.name,
                "start_line": unit.start_line,
                "standalone_helper": unit.name in helpers,
                "verdict": verdict,
                "best_match": (
                    entry_record(best.entry) | {"similarity": round(similarity, 3)} if best else None
                ),
            })
        return {
            "file": path,
            "language": language,
            "functions": results,
            "thresholds": {"in_library": SAME_THRESHOLD, "similar_exists": SIMILAR_THRESHOLD},
        }

    # Input: source (str), path (str), k (int)
    # Output: dict[str, Any]
    # Processing: Library helpers that fit what a file does.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def recommend_for_file(self, source: str, path: str, *, k: int = 5) -> dict[str, Any]:
        """Search the library with a query built from the file itself, and
        leave out whatever the file already contains."""
        language = language_for_path(path)
        if language is None:
            return {"file": path, "error": "unsupported file type"}
        units = [u for u in extract(source, path) if not u.is_template]
        summary = "; ".join(
            f"{u.name}: {u.docstring.splitlines()[0]}" if u.docstring else u.name for u in units[:20]
        )
        imports = sorted({i.split()[1].split(".")[0] for u in units for i in u.required_imports
                          if len(i.split()) > 1})
        query = f"helpers for a file that does: {summary}" if summary else Path(path).stem
        present = {r["best_match"]["block_id"] for r in self.check_file(source, path)["functions"]
                   if r["verdict"] == "in_library"}
        found = self.search(query, language=language, k=k + len(present), dependencies=imports)
        recs = [r for r in found["data"]["recommendations"] if r["block_id"] not in present][:k]
        found["data"]["recommendations"] = recs
        found["data"]["query_used"] = query
        found["data"]["already_in_file"] = sorted(present)
        return found

    # Input: query (str), language (str | None), k (int)
    # Output: dict[str, Any]
    # Processing: Per-stage scores for the top results.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def explain(self, query: str, *, language: str | None = None, k: int = 5) -> dict[str, Any]:
        """Why each result ranks where it does: every stage's score and rank."""
        request = Request(query=query, project_context=ProjectContext(
            languages=(language.lower(),) if language else ()))
        with self.lock:
            retrieved = self.retrieval.retrieve(request)
            ordered = self.pipeline.rank_only(request, retrieved.candidates)
        return {
            "query": query,
            "candidates_retrieved": len(retrieved.candidates),
            "results": [
                {
                    "rank": rank,
                    "block_id": c.id,
                    "name": c.entry.name,
                    "bm25_rank": c.bm25_rank,
                    "vector_rank": c.vector_rank,
                    "cosine": round(c.cosine_score, 4),
                    "rrf_rank": c.rrf_rank,
                    "stage1_score": None if c.stage1_score is None else round(c.stage1_score, 4),
                    "cross_encoder_score": (
                        None if c.cross_encoder_score is None else round(c.cross_encoder_score, 4)
                    ),
                    "final_score": None if c.final_score is None else round(c.final_score, 4),
                }
                for rank, c in enumerate(ordered[:k], start=1)
            ],
            "how_to_read": (
                "rrf_rank fuses keyword (bm25) and vector search; the cross-encoder rereads query "
                "and code together; final_score is Stage 3's blend and sets the order"
            ),
        }

    # Input: queries (list[str]), language (str | None), k (int)
    # Output: dict[str, Any]
    # Processing: Several searches in one call.
    # Changed by: Claude Code
    # Date: 2026-10-18
    def batch(self, queries: list[str], *, language: str | None = None, k: int = 3) -> dict[str, Any]:
        return {
            "results": [
                {"query": q, "recommendations": self.search(q, language=language, k=k)["data"]["recommendations"]}
                for q in queries
            ]
        }


_engine: Engine | None = None
_engine_lock = threading.Lock()


# Input: none
# Output: Engine
# Processing: The shared engine, loaded on first use.
# Changed by: Claude Code
# Date: 2026-10-18
def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine.load()
        return _engine


# Input: engine (Engine | None)
# Output: None
# Processing: Replace the shared engine (tests).
# Changed by: Claude Code
# Date: 2026-10-18
def set_engine(engine: Engine | None) -> None:
    global _engine
    _engine = engine


__all__ = ["Engine", "get_engine", "set_engine"]
