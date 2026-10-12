"""Harvest from an already-cloned repository on disk.

The API path needs search access this environment does not have, while
anonymous ``git clone`` of a public repository does work. So the same
extraction and the same licence gate run against a directory instead of REST,
and the dataset becomes producible.

The substantive difference is licence detection. GitHub's API hands back an
SPDX identifier; a clone hands back a text file, so the identifier has to be
recovered from the text. :func:`detect_license` matches the distinctive
sentences of the permissive licences rather than trying to be a full SPDX
matcher, and returns None when it is not sure -- which the gate treats as
ineligible, the same as no licence at all.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ml.ingest.description import DescriptionGenerator, describe_record
from ml.ingest.extract import extract, language_for_path
from ml.ingest.licensing import LicenseVerdict, classify_license, extract_copyright_holder
from ml.ingest.metrics import measure
from ml.ingest.provenance import Attribution, SourceRef, build_attribution

LICENSE_FILENAMES = ("LICENSE", "LICENCE", "COPYING", "LICENSE.txt", "LICENSE.md")

# Ordered: the more specific pattern must win. BSD-3 contains BSD-2's text, so
# testing BSD-2 first would mislabel every BSD-3 repository.
LICENSE_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BSD-3-Clause", ("Redistribution and use in source and binary", "Neither the name")),
    ("BSD-2-Clause", ("Redistribution and use in source and binary",)),
    ("Apache-2.0", ("Apache License", "Version 2.0")),
    ("MIT", ("Permission is hereby granted, free of charge",)),
    ("ISC", ("Permission to use, copy, modify, and/or distribute this software",)),
    ("0BSD", ("Permission to use, copy, modify, and/or distribute this software for any purpose",)),
    ("Unlicense", ("This is free and unencumbered software released into the public domain",)),
    ("MPL-2.0", ("Mozilla Public License Version 2.0",)),
    ("AGPL-3.0", ("GNU AFFERO GENERAL PUBLIC LICENSE",)),
    ("LGPL-3.0", ("GNU LESSER GENERAL PUBLIC LICENSE",)),
    ("GPL-3.0", ("GNU GENERAL PUBLIC LICENSE", "Version 3")),
    ("GPL-2.0", ("GNU GENERAL PUBLIC LICENSE",)),
)


# Input: text (str)
# Output: str | None
# Processing: Recover an SPDX identifier from licence file text
# Changed by: Claude Code
# Date: 2026-10-12
def detect_license(text: str) -> str | None:
    """Match licence text to an SPDX id, or None when unsure.

    None is the safe answer: the gate rejects it, exactly as it rejects a
    repository with no licence. Guessing would be the only way this function
    could cause harm.
    """
    if not text or not text.strip():
        return None
    for spdx, needles in LICENSE_SIGNATURES:
        if all(needle.lower() in text.lower() for needle in needles):
            return spdx
    return None


@dataclass(frozen=True)
class LocalRepo:
    """A cloned repository, with the facts a harvest needs about it."""

    root: Path
    owner: str
    repo: str
    commit_sha: str
    license_text: str
    verdict: LicenseVerdict
    copyright_holder: str | None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


# Input: root (Path | str)
# Output: LocalRepo | None
# Processing: Read a clone's owner, sha, licence and holder; None if ineligible
# Changed by: Claude Code
# Date: 2026-10-12
def open_repo(root: Path | str) -> LocalRepo | None:
    """Inspect a clone. Returns None when it cannot be harvested.

    The licence is read before anything else, so an ineligible repository costs
    one file read rather than a full tree walk.
    """
    root = Path(root)
    if not (root / ".git").is_dir():
        return None

    remote = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True, text=True,
    ).stdout.strip()
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not remote or not sha:
        return None

    trimmed = remote.removesuffix(".git").rstrip("/")
    parts = trimmed.split("/")
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]

    text = ""
    for name in LICENSE_FILENAMES:
        candidate = root / name
        if candidate.is_file():
            text = candidate.read_text(errors="replace")
            break

    verdict = classify_license(detect_license(text))
    return LocalRepo(
        root=root, owner=owner, repo=repo, commit_sha=sha,
        license_text=text, verdict=verdict,
        copyright_holder=extract_copyright_holder(text),
    )


# Input: unit (ExtractedUnit), attribution (Attribution), repo (LocalRepo)
# Output: list[str]
# Processing: Name every field that could not be determined at harvest time
# Changed by: Claude Code
# Date: 2026-10-12
def missing_fields(unit, attribution: Attribution, repo: LocalRepo) -> list[str]:
    """What this entry does not have, named rather than omitted.

    Three of these are absent for every harvested entry and will stay absent
    until the entry is processed further: ``tests``, ``mutation_score`` and
    ``embedding``. Listing them anyway is the point -- a consumer should not
    have to infer a gap from a key that is not there.
    """
    missing = ["tests", "mutation_score", "embedding"]
    if not unit.docstring.strip():
        missing.append("description")
    if unit.signature is None:
        missing.append("signature")
    if repo.copyright_holder is None:
        missing.append("copyright_holder")
    if attribution.verdict.requires_notice and attribution.notice_text is None:
        missing.append("notice")
    if unit.annotation_only_names:
        missing.append("resolvable_annotations")
    return missing


# Input: repo (LocalRepo), unit (ExtractedUnit), rel_path (str), category (str), describer (DescriptionGenerator | None)
# Output: dict[str, object]
# Processing: Build the entry record for one extracted unit
# Changed by: Claude Code
# Date: 2026-10-12
def build_record(
    repo: LocalRepo,
    unit,
    rel_path: str,
    category: str,
    describer: DescriptionGenerator | None = None,
) -> dict[str, object]:
    """One dataset row, with provenance, metrics and explicit gaps."""
    attribution = build_attribution(
        SourceRef(
            owner=repo.owner, repo=repo.repo, path=rel_path,
            commit_sha=repo.commit_sha,
            start_line=unit.start_line, end_line=unit.end_line,
        ),
        repo.verdict,
        license_text=repo.license_text,
        parsed_holder=repo.copyright_holder,
    )
    metrics = measure(unit)
    record = {
        "id": f"{repo.owner}_{repo.repo}:{unit.name}",
        "name": unit.name,
        "type": "template" if unit.is_template else "helper",
        "language": unit.language,
        "category": category,
        "signature": unit.signature,
        "description": unit.docstring.strip().splitlines()[0] if unit.docstring.strip() else None,
        "code": unit.insertable_code,
        "required_imports": list(unit.required_imports),
        # source is the platform; author is who owns the repository there.
        # copyright_holder comes from the licence and is the better authorship
        # signal, since an owner is often an organisation or a later transferee.
        "source": "github",
        "source_repo": repo.full_name,
        "source_url": f"https://github.com/{repo.full_name}",
        "author": repo.owner,
        "copyright_holder": attribution.copyright_holder,
        "source_path": rel_path,
        "source_commit_sha": repo.commit_sha,
        "source_permalink": attribution.source.permalink,
        "license_spdx": attribution.verdict.spdx_id,
        "license_text": repo.license_text,
        "credit": attribution.credit_line(),
        "attribution_header": attribution.header(unit.language),
        "code_metrics": metrics.as_dict(),
        "needs_review": unit.needs_review or attribution.needs_review,
        "annotation_only_names": list(unit.annotation_only_names),
        "missing": missing_fields(unit, attribution, repo),
    }
    # Filled after the record exists, so the generator sees the same signature
    # and code a consumer would, and so the source is recorded either way.
    return describe_record(record, describer)


# Input: roots (list of Path | str), out_path (Path), category (str), min_quality (float)
# Output: dict[str, object]
# Processing: Harvest every eligible clone into one JSONL file
# Changed by: Claude Code
# Date: 2026-10-12
def harvest_clones(
    roots,
    out_path: Path,
    *,
    category: str = "data-processing",
    min_quality: float = 0.0,
    target: int | None = None,
    describer: DescriptionGenerator | None = None,
) -> dict[str, object]:
    """Walk each clone, write eligible entries as JSONL, return a report.

    Written and flushed per entry so an interrupted run leaves a usable file
    rather than a truncated one.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "written": 0, "repos_ok": 0, "repos_rejected": 0,
        "rejected": {}, "skipped_quality": 0, "by_language": {},
    }
    seen: set[str] = set()

    with out_path.open("w", encoding="utf-8") as handle:
        for root in roots:
            repo = open_repo(root)
            if repo is None:
                report["repos_rejected"] += 1
                report["rejected"]["not a clone"] = report["rejected"].get("not a clone", 0) + 1
                continue
            if not repo.verdict.usable:
                report["repos_rejected"] += 1
                key = repo.verdict.eligibility.value
                report["rejected"][key] = report["rejected"].get(key, 0) + 1
                continue
            report["repos_ok"] += 1

            for path in sorted(repo.root.rglob("*")):
                if target is not None and report["written"] >= target:
                    break
                if not path.is_file():
                    continue
                rel = str(path.relative_to(repo.root))
                if language_for_path(rel) is None:
                    continue
                try:
                    units = extract(path.read_text(errors="replace"), rel)
                except Exception:
                    continue
                for unit in units:
                    if target is not None and report["written"] >= target:
                        break
                    if not unit.self_contained:
                        continue
                    record = build_record(repo, unit, rel, category, describer)
                    if record["code_metrics"]["quality"] < min_quality:
                        report["skipped_quality"] += 1
                        continue
                    if record["id"] in seen:
                        continue
                    seen.add(record["id"])
                    handle.write(json.dumps(record) + "\n")
                    handle.flush()
                    report["written"] += 1
                    lang = unit.language
                    report["by_language"][lang] = report["by_language"].get(lang, 0) + 1
    return report
