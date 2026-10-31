# project_ingestion (B09): built

**Owner:** Nirmal. **Status:** service built end to end with a B10 stub.

Receives a §5.5 submission, adds the §9.1 `language` per file, calls B10 for
a §5.4 diagram (stubbed until Sourish ships B10), runs the deterministic
validator (B11), and stores the result so a later §5.3 request can read it.

## Contracts it follows

| Connection | Handbook |
|---|---|
| Client → B05 submission | §5.5 in, `{submission_id, project_id, status: accepted}` out |
| B05 → B09 ingestion | §9.1 (adds `language` per file) |
| B09 → B10 diagram model | §9.1 in, §5.4 out |
| B10 → B11 validator | §5.4 in, §10.2 out |
| Client → B05 diagram read | §5.3 in, §5.4 out |

## What is built

- `language.py:detect_language(path)` picks the §9.1 `language` from a file
  extension; falls back to the lowercased extension or `"text"`.
- `diagram_client.py` exports:
  - `StubDiagramClient` (default): one `file` node per submission file, no
    edges. Keeps the pipeline exercised before B10 is written.
  - `HttpDiagramClient(base_url)`: ready for `POST /v1/diagram/generate` on
    `ml/serving` once Sourish exposes it.
- `store.py:InMemoryStore` is pluggable; prod can swap in a sqlite backend
  without touching the service.
- `service.py:IngestionService` glues validate → language → diagram → validator
  → store together. `ingest()` and `diagram()` are the only two methods B05
  calls.

## Open items

- **OD-1:** large project transfer. v1 accepts JSON bodies only; large
  projects wait on the handbook decision.
- **Gap G1:** B11 → B12 contract. B09 stores §10.2 next to the diagram so B12
  can pull it when B12 is built.
- B10 is a stub. Swap `StubDiagramClient` for `HttpDiagramClient` the moment
  Sourish ships the service.
