"""The MCP server's tools, on a small library with the hashing placeholder
encoder so no torch or weights are needed."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")

from ml_models import llm
from ml_models.engine import Engine, set_engine
from ml.retrieval.embedding import HashingEmbeddingModel
from ml.schemas import Entry, EntryType

COMBINE = """export default function combineURLs(baseURL, relativeURL) {
  if (!relativeURL) {
    return baseURL;
  }
  return baseURL.replace(/\\/+$/, '') + '/' + relativeURL.replace(/^\\/+/, '');
}
"""

COUNT = """export function countWords(text) {
  const words = text.trim().split(/\\s+/);
  const total = words.filter(Boolean).length;
  return total;
}
"""


def entry(name: str, code: str, description: str) -> Entry:
    return Entry(
        id=f"demo:{name}", type=EntryType.HELPER, name=name, description=description,
        category="data-processing", language="javascript", signature="(a, b)", code=code,
    )


@pytest.fixture
def engine():
    model = HashingEmbeddingModel()
    entries = [
        entry("combineURLs", COMBINE, "Creates a new URL by combining the specified URLs"),
        entry("countWords", COUNT, "Count the words in a piece of text"),
    ]
    for e, vector in zip(entries, model.encode_entries(entries)):
        e.embedding = vector
    built = Engine(entries=entries, embedding_model=model)
    set_engine(built)
    yield built
    set_engine(None)


def test_search_returns_the_b06_envelope(engine):
    result = engine.search("combine a base url with a relative url", language="javascript", k=1)
    assert result["ok"] is True
    assert [r["block_id"] for r in result["data"]["recommendations"]] == ["demo:combineURLs"]


def test_get_unknown_id_suggests_close_ones(engine):
    assert engine.get("demo:combineURLs")["code"] == COMBINE
    assert engine.get("combine")["did_you_mean"] == ["demo:combineURLs"]


def test_categories_count_by_language(engine):
    assert engine.categories("javascript")["total"] == 2
    assert engine.categories("python")["total"] == 0


def test_check_file_marks_a_verbatim_copy_in_library(engine):
    report = engine.check_file(COMBINE, "urls.js")
    (function,) = report["functions"]
    assert function["verdict"] == "in_library"
    assert function["best_match"]["block_id"] == "demo:combineURLs"


def test_check_file_rejects_unknown_types(engine):
    assert engine.check_file("x", "notes.txt")["error"] == "unsupported file type"


def test_recommend_for_file_leaves_out_what_the_file_has(engine):
    result = engine.recommend_for_file(COMBINE, "urls.js", k=5)
    ids = [r["block_id"] for r in result["data"]["recommendations"]]
    assert "demo:combineURLs" not in ids
    assert result["data"]["already_in_file"] == ["demo:combineURLs"]


def test_rerank_needs_the_cross_encoder(engine):
    assert "error" in engine.rerank("q", [{"name": "a", "code": "x"}])


def test_explain_reports_every_stage(engine):
    (top, *_) = engine.explain("count the words in text", language="javascript")["results"]
    assert {"bm25_rank", "vector_rank", "rrf_rank", "cross_encoder_score", "final_score"} <= set(top)


def test_server_registers_the_tools(engine):
    from mcp_server.server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {
        "search_helpers", "find_helpers_in_file", "get_helper", "list_categories", "model_status",
        "rerank_candidates", "check_file_against_library", "recommend_for_file", "explain_ranking",
        "batch_search", "ask_llm",
    }


def test_ask_llm_without_a_key_names_the_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.ask("hi")["error"].startswith("ANTHROPIC_API_KEY is not set")
    assert "error" in llm.ask("hi", provider="other")
