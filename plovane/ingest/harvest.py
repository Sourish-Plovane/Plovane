"""The harvester: plan in, licence-cleared entries out.

Order of operations, and the order is the safety property: a repository's
licence is checked **before** any of its code is fetched. Nothing downstream
can rescue an entry whose licence does not permit redistribution, so the gate
runs first and a rejected repository costs one request instead of fifty.

Run it as a CLI:

    GITHUB_TOKEN=... python -m plovane.ingest --target 1200 --out seed/
    python -m plovane.ingest --target 1200 --plan-only   # no API calls

Output is JSONL -- one entry per line with its provenance -- plus a report
counting what was rejected and why. JSONL because a harvest is resumable and
partial: a run that dies at entry 900 should leave 900 usable entries, not a
corrupt file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from plovane.ingest.extract import extract, language_for_path
from plovane.ingest.github_client import GitHubClient, GitHubError, RateLimited
from plovane.ingest.licensing import (
    Eligibility,
    classify_license,
    extract_copyright_holder,
)
from plovane.ingest.provenance import Attribution, SourceRef, build_attribution
from plovane.ingest.quota import Cell, HarvestPlan, build_plan

MAX_REPOS_PER_CELL = 25
MAX_FILES_PER_REPO = 40
MAX_ENTRIES_PER_REPO = 8
"""Cap per repository, so the corpus is not one project wearing 300 hats.

Also a diversity guard for the rankers: entries from one repository share
style, naming and dependencies, and a shortlist drawn from one project
collapses MMR's job into nothing.
"""


@dataclass
class HarvestReport:
    """What happened, in enough detail to act on."""

    planned: int = 0
    harvested: int = 0
    templates: int = 0
    helpers: int = 0
    repos_considered: int = 0
    repos_accepted: int = 0
    rejections: Counter[str] = field(default_factory=Counter)
    needs_review: int = 0
    requests_made: int = 0
    errors: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejections[reason] += 1

    def summary(self) -> str:
        lines = [
            f"planned {self.planned} / harvested {self.harvested} "
            f"({self.templates} templates, {self.helpers} helpers)",
            f"repositories: {self.repos_accepted} accepted of {self.repos_considered} considered",
            f"entries needing human review: {self.needs_review}",
            f"API requests: {self.requests_made}",
        ]
        if self.rejections:
            lines.append("rejected:")
            lines += [f"  {reason:<34} {count:>5}" for reason, count in self.rejections.most_common()]
        if self.errors:
            lines.append(f"errors: {len(self.errors)} (first: {self.errors[0][:120]})")
        return "\n".join(lines)


@dataclass
class HarvestedEntry:
    """A candidate entry plus the provenance that lets it be redistributed."""

    name: str
    code: str
    language: str
    category: str
    signature: str | None
    docstring: str
    is_template: bool
    attribution: Attribution
    needs_review: bool

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "code": self.code,
                "language": self.language,
                "category": self.category,
                "signature": self.signature,
                "docstring": self.docstring,
                "type": "template" if self.is_template else "helper",
                "needs_review": self.needs_review,
                "license_text": self.attribution.license_text,
                "credit": self.attribution.credit_line(),
                "attribution_header": self.attribution.header(self.language),
                **self.attribution.as_record(),
            }
        )


def search_query(cell: Cell, *, allowed_spdx: tuple[str, ...]) -> str:
    """A search query narrowed by language, topic and licence.

    The ``license:`` qualifier is the important part: it moves the licence
    filter server-side, so the harvest does not spend its budget fetching
    repositories it will throw away. The filter is still re-checked per
    repository, because GitHub's detection is advisory and a query qualifier
    is not a guarantee.
    """
    language_qualifier = {
        "cpp": "c++",
        "cuda": "cuda",
        "javascript": "javascript",
        "python": "python",
        "css": "css",
        "html": "html",
    }.get(cell.language, cell.language)
    licences = " ".join(f"license:{spdx.lower()}" for spdx in allowed_spdx[:5])
    topic = cell.category.replace("-", " ")
    return f"{topic} language:{language_qualifier} {licences} stars:>25 archived:false"


def vet_repository(
    client: GitHubClient, owner: str, repo: str, report: HarvestReport
) -> tuple[str, str, str | None, str] | None:
    """Licence gate. Returns (sha, licence_text, notice, spdx) or None.

    This is the only place a repository becomes eligible, and it is run before
    any source file is fetched.
    """
    report.repos_considered += 1
    try:
        info = client.license_info(owner, repo)
    except RateLimited:
        raise
    except GitHubError as error:
        report.reject("licence lookup failed")
        report.errors.append(str(error))
        return None

    raw_spdx = (info or {}).get("license", {}).get("spdx_id") if info else None
    verdict = classify_license(raw_spdx)
    if not verdict.usable:
        report.reject(
            {
                Eligibility.COPYLEFT: "copyleft licence",
                Eligibility.UNLICENSED: "no licence (all rights reserved)",
                Eligibility.UNKNOWN: "licence not on allowlist",
            }[verdict.eligibility]
        )
        return None

    license_text = ""
    if info and info.get("content") and info.get("encoding") == "base64":
        from base64 import b64decode

        license_text = b64decode(info["content"]).decode("utf-8", errors="replace")
    if not license_text.strip():
        # Eligible by id but we cannot reproduce the text, which MIT and BSD
        # require us to ship. Reject rather than vendor an incomplete notice.
        report.reject("licence text unavailable")
        return None

    notice = None
    if verdict.requires_notice:
        try:
            notice = client.file_contents(owner, repo, "NOTICE")
        except GitHubError:
            notice = None

    try:
        sha = client.default_branch_sha(owner, repo)
    except GitHubError as error:
        report.reject("could not resolve commit sha")
        report.errors.append(str(error))
        return None

    report.repos_accepted += 1
    return sha, license_text, notice, verdict.spdx_id or ""


def harvest_repository(
    client: GitHubClient,
    owner: str,
    repo: str,
    cell: Cell,
    report: HarvestReport,
    *,
    template_budget: int,
    helper_budget: int,
) -> list[HarvestedEntry]:
    """Vet, then extract from one repository within the two budgets.

    Templates and helpers are counted separately rather than sharing one
    limit, so asking for a template-heavy corpus actually produces one. With a
    single limit the first files encountered fill the quota and the split is
    whatever the repository's file order happened to give.
    """
    vetted = vet_repository(client, owner, repo, report)
    if vetted is None:
        return []
    sha, license_text, notice, _ = vetted
    verdict = classify_license(_ or None)
    holder = extract_copyright_holder(license_text)

    try:
        blobs = client.tree(owner, repo, sha)
    except GitHubError as error:
        report.errors.append(str(error))
        return []

    paths = [
        item["path"]
        for item in blobs
        if language_for_path(item["path"]) == cell.language
    ][:MAX_FILES_PER_REPO]

    entries: list[HarvestedEntry] = []
    templates_left, helpers_left = template_budget, helper_budget
    for path in paths:
        if templates_left <= 0 and helpers_left <= 0:
            break
        try:
            source = client.file_contents(owner, repo, path, ref=sha)
        except GitHubError:
            continue

        # Ask for a whole-file template only while template budget remains;
        # otherwise slice functions out of the same file.
        for unit in extract(source, path, want_template=templates_left > 0):
            if unit.is_template:
                if templates_left <= 0:
                    continue
                templates_left -= 1
            else:
                if helpers_left <= 0:
                    continue
                helpers_left -= 1
            attribution = build_attribution(
                SourceRef(
                    owner=owner,
                    repo=repo,
                    path=path,
                    commit_sha=sha,
                    start_line=unit.start_line,
                    end_line=unit.end_line,
                ),
                verdict,
                license_text=license_text,
                parsed_holder=holder,
                notice_text=notice,
            )
            needs_review = unit.needs_review or attribution.needs_review
            if needs_review:
                report.needs_review += 1
            entries.append(
                HarvestedEntry(
                    name=unit.name,
                    code=unit.code,
                    language=unit.language,
                    category=cell.category,
                    signature=None if unit.is_template else unit.signature,
                    docstring=unit.docstring,
                    is_template=unit.is_template,
                    attribution=attribution,
                    needs_review=needs_review,
                )
            )
    return entries


def harvest(
    plan: HarvestPlan,
    *,
    client: GitHubClient | None = None,
    out_dir: Path | None = None,
    allowed_spdx: tuple[str, ...] = ("MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC"),
) -> HarvestReport:
    """Run the plan. Writes ``entries.jsonl`` incrementally when ``out_dir`` is set."""
    client = client or GitHubClient()
    report = HarvestReport(planned=plan.allocated)

    handle = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        handle = (out_dir / "entries.jsonl").open("a", encoding="utf-8")

    seen_names: set[tuple[str, str]] = set()
    try:
        for cell in plan.cells:
            templates_left, helpers_left = cell.templates, cell.helpers
            try:
                results = client.search_repositories(
                    search_query(cell, allowed_spdx=allowed_spdx), per_page=MAX_REPOS_PER_CELL
                )
            except RateLimited as error:
                report.errors.append(f"rate limited, stopping early: {error}")
                break
            except GitHubError as error:
                report.errors.append(str(error))
                continue

            for item in results.get("items", []):
                if templates_left <= 0 and helpers_left <= 0:
                    break
                owner = item["owner"]["login"]
                repo = item["name"]
                # Cap per repository so one project cannot fill a whole cell.
                room = min(MAX_ENTRIES_PER_REPO, templates_left + helpers_left)
                try:
                    entries = harvest_repository(
                        client,
                        owner,
                        repo,
                        cell,
                        report,
                        template_budget=min(templates_left, room),
                        helper_budget=min(helpers_left, room),
                    )
                except RateLimited as error:
                    report.errors.append(f"rate limited, stopping early: {error}")
                    templates_left = helpers_left = 0
                    break

                for entry in entries:
                    key = (entry.name.lower(), entry.language)
                    if key in seen_names:
                        report.reject("duplicate name in corpus")
                        continue
                    seen_names.add(key)
                    report.harvested += 1
                    if entry.is_template:
                        report.templates += 1
                        templates_left -= 1
                    else:
                        report.helpers += 1
                        helpers_left -= 1
                    if handle:
                        handle.write(entry.to_json() + "\n")
                        handle.flush()
    finally:
        if handle:
            handle.close()
        report.requests_made = client.requests_made

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harvest licence-cleared seed entries from GitHub.")
    parser.add_argument("--target", type=int, default=1200)
    parser.add_argument("--out", type=Path, default=Path("seed"))
    parser.add_argument("--plan-only", action="store_true", help="print the plan, make no API calls")
    parser.add_argument("--min-per-language", type=int, default=60)
    split = parser.add_mutually_exclusive_group()
    split.add_argument(
        "--templates", type=int, default=None,
        help="how many of --target to harvest as templates (default: a quarter)",
    )
    split.add_argument(
        "--template-share", type=float, default=None,
        help="fraction of --target to harvest as templates, 0.0 to 1.0",
    )
    args = parser.parse_args(argv)

    plan = build_plan(
        args.target,
        minimum_per_language=args.min_per_language,
        templates=args.templates,
        template_share=args.template_share,
    )
    print(plan.summary())

    if args.plan_only:
        return 0

    client = GitHubClient()
    if not client.token:
        print(
            "\nNo GITHUB_TOKEN set. Unauthenticated REST allows 60 requests an hour, "
            "which cannot seed a library of this size. Set a token and retry.",
            file=sys.stderr,
        )
        return 2

    print(f"\nHarvesting into {args.out}/entries.jsonl ...\n")
    report = harvest(plan, client=client, out_dir=args.out)
    print(report.summary())
    return 0 if report.harvested else 1


if __name__ == "__main__":
    raise SystemExit(main())
