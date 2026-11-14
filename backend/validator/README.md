# validator (B11): deterministic diagram validator

**Owner:** Nirmal. **Status:** built as a library; transport not finalized.

Checks the canonical diagram from handbook §5.4 and returns the §10.2 result.
The handbook defines the result shape, not the checking logic, so the rules
here are B11's own:

- `project_id` and `diagram` are required; `nodes` and `edges` are arrays.
- Every node needs `node_id`, `label`, `node_type` (`function`, `file`,
  `folder`) and `source_path`; `node_id` values are unique.
- Every edge needs `source_node_id`, `target_node_id` and `relationship`; the
  endpoints must name nodes in the same diagram. The relationship enumeration
  is still open (OD-2), so any non-empty string passes.

Stable issue codes: `INVALID_DIAGRAM`, `MISSING_FIELD`, `INVALID_NODE_TYPE`,
`DUPLICATE_NODE_ID`, `INVALID_EDGE_REFERENCE`.

No transport is assumed (handbook §4.3); call `validate_diagram` in-process,
or add an HTTP/queue wrapper once the connection is finalized.

```python
from backend.validator.diagram import validate_diagram
validate_diagram({"project_id": "proj_123", "diagram": {"nodes": [], "edges": []}})
# {"project_id": "proj_123", "valid": True, "issues": []}
```

Tests: `backend/validator/tests/`.
