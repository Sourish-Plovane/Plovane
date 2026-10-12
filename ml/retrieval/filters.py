"""Candidate filters.

Every filter here is applied in SQL before results return -- never in Python
after the fact. Filtering late would mean paying to retrieve and rank rows the
requester is not allowed to see, and would quietly shrink the candidate set
below the 1,000 that recall@1000 is measured against.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ml.schemas import DependencyCompat, Entry, Request, Visibility

DEFAULT_ALLOWED_LICENSES: tuple[str, ...] = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD")
"""Permissive licences, eligible for any use. Copyleft entries are withheld
unless the requester's own policy lists them."""


# Input: entry (Entry), installed (Sequence[str])
# Output: DependencyCompat
# Processing: How the entry's dependencies sit against the project's installed set.
# Changed by: Claude Code
# Date: 2026-10-12
def classify_dependency_compat(entry: Entry, installed: Sequence[str]) -> DependencyCompat:
    """How the entry's dependencies sit against the project's installed set.

    A dependency is given as ``name`` or ``name==version``. A name the project
    already has at a different pinned version conflicts; a name it does not
    have at all needs installing.
    """
    installed_versions: dict[str, str | None] = {}
    for dep in installed:
        name, _, version = dep.partition("==")
        installed_versions[name.strip().lower()] = version.strip() or None

    verdict = DependencyCompat.COMPATIBLE
    for dep in entry.dependencies:
        name, _, version = dep.partition("==")
        name = name.strip().lower()
        version = version.strip() or None
        if name not in installed_versions:
            verdict = DependencyCompat.NEEDS_INSTALL
            continue
        have = installed_versions[name]
        if version and have and version != have:
            # A pinned mismatch is the one case we will not paper over.
            return DependencyCompat.CONFLICTS
    return verdict


@dataclass(frozen=True)
class FilterSpec:
    """The filters for one request, in a form both SQL and Python can read."""

    requester_id: str | None = None
    languages: tuple[str, ...] = ()
    framework: str | None = None
    category: str | None = None
    allowed_licenses: tuple[str, ...] = DEFAULT_ALLOWED_LICENSES
    installed_dependencies: tuple[str, ...] = ()
    drop_in_only: bool = False
    entry_types: tuple[str, ...] = ()
    """Restrict to these entry types, e.g. ``("template", "bundle")``.

    Empty means every type. Filtering by type belongs here rather than after
    retrieval: a type-restricted request that post-filters would hand ranking
    far fewer than the 1,000 candidates recall@1000 is measured against, and
    the shortfall would be invisible.
    """

    extra_sql: tuple[str, ...] = field(default_factory=tuple)

    # Input: request (Request), allowed_licenses (Sequence[str])
    # Output: FilterSpec
    # Processing: From request
    # Changed by: Claude Code
    # Date: 2026-10-12
    @classmethod
    def from_request(
        cls,
        request: Request,
        *,
        allowed_licenses: Sequence[str] = DEFAULT_ALLOWED_LICENSES,
    ) -> FilterSpec:
        ctx = request.project_context
        return cls(
            requester_id=request.requester_id,
            languages=tuple(lang.lower() for lang in ctx.languages),
            framework=ctx.framework,
            category=ctx.category,
            allowed_licenses=tuple(allowed_licenses),
            installed_dependencies=ctx.dependencies,
            drop_in_only=request.drop_in_only,
        )

    # -- SQL ---------------------------------------------------------------

    # Input: none
    # Output: tuple[str, dict[str, Any]]
    # Processing: Render the filters as a parameterised SQL ``WHERE`` body.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def where_clause(self) -> tuple[str, dict[str, Any]]:
        """Render the filters as a parameterised SQL ``WHERE`` body.

        Returns the clause and its bind parameters. Values are always bound,
        never interpolated, since language and category arrive from the agent.
        """
        clauses: list[str] = []
        params: dict[str, Any] = {}

        # Visibility: public entries, plus the requester's own private ones.
        if self.requester_id is None:
            clauses.append("e.visibility = 'public'")
        else:
            clauses.append("(e.visibility = 'public' OR e.owner_id = :requester_id)")
            params["requester_id"] = self.requester_id

        if self.languages:
            clauses.append("lower(e.language) = ANY(:languages)")
            params["languages"] = list(self.languages)

        if self.framework:
            # A framework-agnostic entry stays eligible; a mismatched one does not.
            clauses.append("(e.framework IS NULL OR e.framework = :framework)")
            params["framework"] = self.framework

        if self.category:
            clauses.append("e.category = :category")
            params["category"] = self.category

        if self.allowed_licenses:
            clauses.append("e.license = ANY(:allowed_licenses)")
            params["allowed_licenses"] = list(self.allowed_licenses)

        if self.drop_in_only:
            # Drop-in means nothing to install: every dependency the entry
            # names must already be present in the project.
            clauses.append("e.dependencies <@ :installed_dependencies")
            params["installed_dependencies"] = list(self.installed_dependencies)

        if self.entry_types:
            clauses.append("e.type = ANY(:entry_types)")
            params["entry_types"] = list(self.entry_types)

        clauses.extend(self.extra_sql)
        return " AND ".join(clauses), params

    # -- Python ------------------------------------------------------------

    # Input: entry (Entry)
    # Output: bool
    # Processing: The same predicate in Python, for the in-memory backends and tests.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def matches(self, entry: Entry) -> bool:
        """The same predicate in Python, for the in-memory backends and tests.

        Kept deliberately equivalent to :meth:`where_clause`; if the two ever
        disagree, the SQL is the one that is live and this one is the bug.
        """
        if self.entry_types and entry.type.value not in self.entry_types:
            return False
        if entry.visibility is Visibility.PRIVATE:
            if self.requester_id is None or entry.owner_id != self.requester_id:
                return False
        if self.languages and entry.language.lower() not in self.languages:
            return False
        if self.framework and entry.framework is not None and entry.framework != self.framework:
            return False
        if self.category and entry.category != self.category:
            return False
        if self.allowed_licenses and entry.license not in self.allowed_licenses:
            return False
        if self.drop_in_only:
            compat = classify_dependency_compat(entry, self.installed_dependencies)
            if compat is not DependencyCompat.COMPATIBLE:
                return False
        return True
