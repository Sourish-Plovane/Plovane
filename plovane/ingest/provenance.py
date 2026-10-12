"""Provenance and attribution.

The spec says the server copies the exact tested code into the project. That
copy is a redistribution, so it has to carry what the licence requires -- and
for MIT, BSD and ISC that is the licence text *and* the copyright notice, not a
credit line. This module models that obligation as data, so attribution is
generated from the record rather than written by hand per entry.

Two details that matter more than they look:

**The commit SHA is pinned.** A repository can relicense, or delete a file,
after harvest. Recording the SHA and the licence as it was *at that SHA* is
what makes the record defensible later: it says precisely which bytes were
taken under which terms, rather than pointing at a moving branch.

**An unresolved copyright holder is not an empty holder.** If the holder cannot
be parsed from the licence text the record falls back to the repository owner
and sets ``needs_review``. Shipping a notice that credits
"<year> <copyright holders>" would be worse than shipping none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from plovane.ingest.licensing import LicenseVerdict

COMMENT_SYNTAX: dict[str, tuple[str, str, str]] = {
    # language: (open, line prefix, close); an empty open/close means line comments
    "python": ("", "# ", ""),
    "javascript": ("/*", " * ", " */"),
    "typescript": ("/*", " * ", " */"),
    "cpp": ("/*", " * ", " */"),
    "cuda": ("/*", " * ", " */"),
    "css": ("/*", " * ", " */"),
    "html": ("<!--", "  ", "-->"),
}
"""How to write a comment per MVP language.

The repo's commenting standard fixes the *fields*, not the delimiters; a
JSDoc-style block is a syntax error in Python, so each language gets its own
and the content stays identical.
"""


@dataclass(frozen=True)
class SourceRef:
    """Exactly which bytes were taken, from where."""

    owner: str
    repo: str
    path: str
    commit_sha: str
    ref: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    # Input: none
    # Output: str
    # Processing: Full name
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    # Input: none
    # Output: str
    # Processing: A SHA-pinned URL, so the link still resolves after a force-push.
    # Changed by: Claude Code
    # Date: 2026-10-12
    @property
    def permalink(self) -> str:
        """A SHA-pinned URL, so the link still resolves after a force-push."""
        url = f"https://github.com/{self.owner}/{self.repo}/blob/{self.commit_sha}/{self.path}"
        if self.start_line:
            url += f"#L{self.start_line}"
            if self.end_line and self.end_line != self.start_line:
                url += f"-L{self.end_line}"
        return url


@dataclass(frozen=True)
class Attribution:
    """Everything redistribution of one entry requires."""

    source: SourceRef
    verdict: LicenseVerdict
    copyright_holder: str
    license_text: str
    notice_text: str | None = None
    needs_review: bool = False
    harvested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Input: none
    # Output: None
    # Processing: Post init
    # Changed by: Claude Code
    # Date: 2026-10-12
    def __post_init__(self) -> None:
        if not self.verdict.usable:
            raise ValueError(
                f"refusing to build attribution for ineligible {self.source.full_name}: "
                f"{self.verdict.reason}"
            )
        if not self.license_text.strip():
            raise ValueError(
                f"{self.source.full_name}: licence text is required -- MIT and BSD "
                "oblige us to redistribute it, so an entry without it cannot ship"
            )
        if self.verdict.requires_notice and self.notice_text is None:
            # Not fatal: Apache-2.0 only requires a NOTICE if the project has
            # one. Flag it so a human can confirm absence rather than assume it.
            object.__setattr__(self, "needs_review", True)

    # Input: none
    # Output: str
    # Processing: One line naming the source, holder and licence.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def credit_line(self) -> str:
        """One line naming the source, holder and licence."""
        return (
            f"{self.source.path} from {self.source.full_name} "
            f"(c) {self.copyright_holder}, {self.verdict.spdx_id}"
        )

    # Input: language (str)
    # Output: str
    # Processing: The attribution block to prepend to inserted code.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def header(self, language: str) -> str:
        """The attribution block to prepend to inserted code.

        Separate from the repo's Input/Output/Processing block: that one
        describes what the function does and who changed it, this one discharges
        a legal obligation. Both can sit above the same definition.
        """
        opener, prefix, closer = COMMENT_SYNTAX.get(language.lower(), ("", "# ", ""))
        lines = [
            f"Vendored from {self.source.full_name} at {self.source.commit_sha[:12]}",
            f"Source: {self.source.permalink}",
            f"Copyright (c) {self.copyright_holder}",
            f"Licence: {self.verdict.spdx_id} -- full text in THIRD_PARTY_LICENSES",
        ]
        if self.notice_text:
            lines.append("NOTICE from the upstream project is reproduced alongside.")

        body = "\n".join(f"{prefix}{line}".rstrip() for line in lines)
        if opener:
            return f"{opener}\n{body}\n{closer}"
        return body

    # Input: none
    # Output: dict[str, object]
    # Processing: Flat row for the entry's provenance column.
    # Changed by: Claude Code
    # Date: 2026-10-12
    def as_record(self) -> dict[str, object]:
        """Flat row for the entry's provenance column."""
        return {
            "source_owner": self.source.owner,
            "source_repo": self.source.repo,
            "source_path": self.source.path,
            "source_commit_sha": self.source.commit_sha,
            "source_permalink": self.source.permalink,
            "license_spdx": self.verdict.spdx_id,
            "copyright_holder": self.copyright_holder,
            "requires_notice": self.verdict.requires_notice,
            "has_notice": self.notice_text is not None,
            "needs_review": self.needs_review,
            "harvested_at": self.harvested_at.isoformat(),
        }


# Input: source (SourceRef), verdict (LicenseVerdict), license_text (str), parsed_holder (str | None), notice_text (str | None)
# Output: Attribution
# Processing: Assemble an attribution, falling back where the holder could not be parsed.
# Changed by: Claude Code
# Date: 2026-10-12
def build_attribution(
    source: SourceRef,
    verdict: LicenseVerdict,
    *,
    license_text: str,
    parsed_holder: str | None,
    notice_text: str | None = None,
) -> Attribution:
    """Assemble an attribution, falling back where the holder could not be parsed."""
    holder = parsed_holder or f"{source.owner} (see {source.full_name})"
    return Attribution(
        source=source,
        verdict=verdict,
        copyright_holder=holder,
        license_text=license_text,
        notice_text=notice_text,
        needs_review=parsed_holder is None,
    )


# Input: attributions (list[Attribution])
# Output: str
# Processing: The THIRD_PARTY_LICENSES file for a project that inserted these entries.
# Changed by: Claude Code
# Date: 2026-10-12
def render_third_party_licenses(attributions: list[Attribution]) -> str:
    """The THIRD_PARTY_LICENSES file for a project that inserted these entries.

    Grouped by source repository and deduplicated, since ten helpers from one
    repository owe one copy of its licence, not ten.
    """
    if not attributions:
        return "# Third-party licences\n\nNo third-party code is vendored in this project.\n"

    by_repo: dict[str, list[Attribution]] = {}
    for attribution in attributions:
        by_repo.setdefault(attribution.source.full_name, []).append(attribution)

    sections = [
        "# Third-party licences",
        "",
        "Code in this project was vendored from the repositories below. Each",
        "licence is reproduced in full, as those licences require.",
        "",
    ]
    for repo_name in sorted(by_repo):
        group = by_repo[repo_name]
        first = group[0]
        sections.append(f"## {repo_name}")
        sections.append("")
        sections.append(f"Licence: {first.verdict.spdx_id}")
        sections.append(f"Copyright (c) {first.copyright_holder}")
        sections.append("")
        sections.append("Files vendored:")
        sections.extend(
            f"- `{a.source.path}` ({a.source.permalink})"
            for a in sorted(group, key=lambda a: a.source.path)
        )
        sections.append("")
        sections.append("```")
        sections.append(first.license_text.strip())
        sections.append("```")
        if first.notice_text:
            sections.extend(["", "NOTICE:", "```", first.notice_text.strip(), "```"])
        sections.append("")
    return "\n".join(sections)
