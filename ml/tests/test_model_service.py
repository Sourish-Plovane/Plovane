"""The HTTP backend for the models, on a two-entry library with the hashing
placeholder encoder so no torch or weights are needed."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("flask")

from ml.retrieval.embedding import HashingEmbeddingModel
from ml.schemas import Entry, EntryType
from ml.serving import registry
from ml.serving.engine import Engine
from ml.serving.service.app import create_app
from ml.serving.service.contract import ContractError, request_from_handbook
from ml.serving.service.store import FeedbackStore

COMBINE = """export default function combineURLs(baseURL, relativeURL) {
  if (!relativeURL) {
    return baseURL;
  }
  return baseURL.replace(/\\/+$/, '') + '/' + relativeURL.replace(/^\\/+/, '');
}
"""


# Input: name (str), code (str), description (str)
# Output: Entry
# Processing: A JavaScript library entry for the test library.
# Changed by: Claude Code
# Date: 2026-10-22
def entry(name: str, code: str, description: str) -> Entry:
    return Entry(
        id=f"demo:{name}", type=EntryType.HELPER, name=name, description=description,
        category="data-processing", language="javascript", signature="(a, b)", code=code,
    )


# Input: tmp_path (Path)
# Output: FlaskClient
# Processing: A test client over a placeholder engine and a temporary log dir.
# Changed by: Claude Code
# Date: 2026-10-22
@pytest.fixture
def client(tmp_path):
    model = HashingEmbeddingModel()
    entries = [
        entry("combineURLs", COMBINE, "Creates a new URL by combining the specified URLs"),
        entry("countWords", "return text.split(/\\s+/).length;", "Count the words in a piece of text"),
    ]
    for e, vector in zip(entries, model.encode_entries(entries)):
        e.embedding = vector
    engine = Engine(entries=entries, embedding_model=model)
    app = create_app(engine_factory=lambda: engine, store=FeedbackStore(tmp_path))
    return app.test_client()


# Input: client (FlaskClient)
# Output: dict
# Processing: One §8.1 recommendation round trip.
# Changed by: Claude Code
# Date: 2026-10-22
def recommend(client, **overrides):
    payload = {"request_id": "req_1", "user_id": "usr_1", "project_id": "proj_1",
               "description": "combine a base url with a relative url", "language": "javascript",
               "tech_stack": ["javascript", "axios"], "requested_count": 1} | overrides
    return client.post("/v1/recommend", json=payload)


# Input: client (FlaskClient)
# Output: None
# Processing: §8.1 in gives §8.2 out with the request id echoed.
# Changed by: Claude Code
# Date: 2026-10-22
def test_recommend_speaks_section_8(client):
    reply = recommend(client)
    assert reply.status_code == 200
    data = reply.get_json()
    assert data["request_id"] == "req_1"
    assert data["query_id"].startswith("q_")
    assert [r["block_id"] for r in data["recommendations"]] == ["demo:combineURLs"]


# Input: client (FlaskClient)
# Output: None
# Processing: A missing description is a 400 with the error object.
# Changed by: Claude Code
# Date: 2026-10-22
def test_recommend_rejects_a_bad_body(client):
    reply = recommend(client, description="")
    assert reply.status_code == 400
    assert reply.get_json()["error"]["code"] == "INVALID_REQUEST"
    assert reply.get_json()["request_id"] == "req_1"
    assert client.post("/v1/recommend", data="nope").status_code == 400


# Input: client (FlaskClient), tmp_path (Path)
# Output: None
# Processing: Feedback is accepted only for blocks actually served.
# Changed by: Claude Code
# Date: 2026-10-22
def test_feedback_joins_on_what_was_served(client, tmp_path):
    query_id = recommend(client).get_json()["query_id"]
    ok = client.post("/v1/feedback", json={"query_id": query_id, "block_id": "demo:combineURLs",
                                            "outcome": "accepted", "user_id": "usr_1"})
    assert ok.status_code == 200
    assert client.post("/v1/feedback", json={"query_id": query_id, "block_id": "demo:countWords",
                                              "outcome": "accepted"}).status_code == 404
    assert client.post("/v1/feedback", json={"query_id": "q_unknown", "block_id": "x",
                                              "outcome": "accepted"}).status_code == 404
    assert client.post("/v1/feedback", json={"query_id": query_id, "block_id": "demo:combineURLs",
                                              "outcome": "maybe"}).status_code == 400
    served = [json.loads(line) for line in (tmp_path / "recommendations.jsonl").read_text().splitlines()]
    assert served[0]["request_id"] == "req_1"
    assert len((tmp_path / "feedback.jsonl").read_text().splitlines()) == 1


# Input: tmp_path (Path)
# Output: None
# Processing: A restarted store still knows what it served.
# Changed by: Claude Code
# Date: 2026-10-22
def test_store_survives_a_restart(client, tmp_path):
    query_id = recommend(client).get_json()["query_id"]
    reopened = FeedbackStore(tmp_path)
    assert reopened.record_feedback(query_id, "demo:combineURLs", "rejected")["outcome"] == "rejected"


# Input: client (FlaskClient)
# Output: None
# Processing: File routes, block lookup, models and health respond.
# Changed by: Claude Code
# Date: 2026-10-22
def test_file_and_lookup_routes(client):
    checked = client.post("/v1/check-file", json={"filename": "urls.js", "code": COMBINE}).get_json()
    assert checked["functions"][0]["verdict"] == "in_library"
    helpers = client.post("/v1/find-helpers", json={"filename": "urls.js", "code": COMBINE}).get_json()
    assert [h["name"] for h in helpers["helpers"]] == ["combineURLs"]
    assert client.post("/v1/check-file", json={"filename": "urls.js"}).status_code == 400
    assert client.get("/v1/blocks/demo:combineURLs").get_json()["code"] == COMBINE
    assert client.get("/v1/blocks/nothing").status_code == 404
    assert len(client.get("/v1/models").get_json()["models"]) == len(registry.MODELS)
    assert client.get("/healthz").get_json() == {"ok": True}
    assert client.get("/readyz").status_code == 200


# Input: none
# Output: None
# Processing: The §8.1 mapping fills the B06 request as documented.
# Changed by: Claude Code
# Date: 2026-10-22
def test_contract_mapping():
    req = request_from_handbook({
        "description": "Add CSV parsing", "language": "Python", "tech_stack": ["python", "pandas"],
        "context_code": "def load_data(path):\n    return open(path).read()\n\n\n", "requested_count": 5,
        "user_id": "usr_9",
    })
    assert req.query == "Add CSV parsing"
    assert req.project_context.languages == ("python",)
    assert req.project_context.dependencies == ("pandas",)
    assert req.k == 5 and req.requester_id == "usr_9"
    with pytest.raises(ContractError):
        request_from_handbook({"description": "x", "requested_count": 0})
