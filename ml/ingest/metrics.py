"""What can be measured about harvested code before it has tests.

Deliberately **not** the spec's ``mutation_score``. Mutation score requires a
test suite, and harvested code arrives with none, so anything computed here is
a structural proxy: it says how *complete* a candidate looks, never whether it
is correct. Naming it ``quality`` and documenting the limit is the honest
option; calling it a correctness signal would license a false comparison
against entries that really were mutation-tested.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass

BRANCH_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
    ast.ExceptHandler, ast.BoolOp, ast.IfExp, ast.comprehension,
)


@dataclass(frozen=True)
class CodeMetrics:
    """Structural measurements of one harvested unit."""

    lines: int
    has_description: bool
    self_contained: bool
    import_count: int
    param_count: int
    annotated_params: int
    has_return_annotation: bool
    branch_count: int

    # Input: none
    # Output: float
    # Processing: Share of parameters carrying a type annotation
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def annotation_coverage(self) -> float:
        if self.param_count == 0:
            return 1.0
        return self.annotated_params / self.param_count

    # Input: none
    # Output: float
    # Processing: Composite completeness score in 0 to 1, not a correctness signal
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def quality(self) -> float:
        """Weighted completeness, 0 to 1.

        Self-containment dominates because a unit that cannot run is worthless
        whatever else it has. A description is next, since without one there is
        nothing for a query to match. Annotations and a tight line count are
        mild positives. Branch count is deliberately absent: a helper with
        branches is not worse than one without, and penalising it would favour
        trivial one-liners.
        """
        score = 0.0
        score += 0.45 if self.self_contained else 0.0
        score += 0.30 if self.has_description else 0.0
        score += 0.15 * self.annotation_coverage
        score += 0.10 if 3 <= self.lines <= 60 else 0.0
        return round(min(1.0, score), 3)

    # Input: none
    # Output: dict[str, object]
    # Processing: Flat dict for the entry JSON, derived values included
    # Changed by: Sourish
    # Date: 2026-10-12
    def as_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["annotation_coverage"] = round(self.annotation_coverage, 3)
        out["quality"] = self.quality
        return out


# Input: unit (ExtractedUnit)
# Output: CodeMetrics
# Processing: Measure one extracted unit's structure
# Changed by: Sourish
# Date: 2026-10-12
def measure(unit) -> CodeMetrics:
    """Measure an :class:`~ml.ingest.extract.ExtractedUnit`.

    Falls back to zeroed structural counts when the code will not parse, which
    happens for the brace-language extractor's heuristic slices. Returning
    metrics rather than raising keeps one unparseable candidate from aborting a
    whole repository's harvest.
    """
    params = annotated = branches = 0
    has_return_annotation = False
    try:
        tree = ast.parse(unit.code.strip())
        fn = next(
            (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
            None,
        )
        if fn is not None:
            args = fn.args
            everything = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            everything += [a for a in (args.vararg, args.kwarg) if a is not None]
            for arg in everything:
                if arg.arg in {"self", "cls"}:
                    continue
                params += 1
                if arg.annotation is not None:
                    annotated += 1
            has_return_annotation = fn.returns is not None
            branches = sum(1 for n in ast.walk(fn) if isinstance(n, BRANCH_NODES))
    except SyntaxError:
        pass

    return CodeMetrics(
        lines=unit.line_count,
        has_description=bool(unit.docstring.strip()),
        self_contained=unit.self_contained,
        import_count=len(unit.required_imports),
        param_count=params,
        annotated_params=annotated,
        has_return_annotation=has_return_annotation,
        branch_count=branches,
    )
