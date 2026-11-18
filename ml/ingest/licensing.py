"""Licence eligibility: the gate every harvested entry passes before anything else.

This module exists because "used and credited" is not a licence. Plovane copies
the exact code into a user's project, which is redistribution, and
redistribution is governed by the source repository's licence whatever the
attribution says:

* **Permissive** (MIT, BSD, ISC, Apache-2.0) allows it, but only if the licence
  text *and* the copyright notice travel with the code. A credit line naming
  the repo does not satisfy MIT. That is why
  :class:`ml.ingest.provenance.Attribution` carries the full text.
* **Copyleft** (GPL, AGPL, LGPL, MPL) allows copying but imposes terms on the
  combined work. Pasted into a user's proprietary project it is a trap set for
  them, not for us, so it is rejected here rather than flagged downstream.
* **No licence at all is the dangerous case**, and it is a large share of
  GitHub. No licence means all rights reserved: the code may be read but not
  copied. A repository with no ``LICENSE`` is not permissively licensed by
  default, and treating silence as permission is the single most likely way
  this pipeline causes real harm.

So the gate is an allowlist of SPDX identifiers, not a denylist. Anything
unrecognised, missing, or ambiguous is ineligible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

PERMISSIVE_SPDX: frozenset[str] = frozenset(
    {
        "MIT",
        "MIT-0",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "0BSD",
        "Unlicense",
        "CC0-1.0",
        "Zlib",
        "BSL-1.0",
    }
)
"""Redistribution-safe with notice preservation. The project's hard gate."""

COPYLEFT_SPDX: frozenset[str] = frozenset(
    {
        "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
        "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
        "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
        "LGPL-2.1", "LGPL-2.1-only", "LGPL-2.1-or-later",
        "LGPL-3.0", "LGPL-3.0-only", "LGPL-3.0-or-later",
        "MPL-2.0", "EPL-2.0", "CDDL-1.0", "SSPL-1.0",
    }
)
"""Known and deliberately rejected. Tracked separately from "unknown" so the
harvest report can say *why* a repository was skipped -- a copyleft rejection
is a policy decision, an unknown one may be a detection failure worth fixing."""

NOTICE_REQUIRED_SPDX: frozenset[str] = frozenset({"Apache-2.0"})
"""Licences whose NOTICE file, where present, must also be redistributed."""

_GITHUB_UNKNOWN = {"", "other", "noassertion", "no-license", "none"}

_COPYRIGHT_RE = re.compile(
    r"^\s*(?:/\*|#|//|\*)?\s*copyright\s*(?:\(c\)|©|\(C\))?\s*"
    r"(?P<years>\d{4}(?:\s*[-–,]\s*\d{4})*)?\s*(?P<holder>[^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)


class Eligibility(str, Enum):
    ELIGIBLE = "eligible"
    COPYLEFT = "copyleft"
    UNLICENSED = "unlicensed"
    UNKNOWN = "unknown"

    # Input: none
    # Output: bool
    # Processing: Usable
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def usable(self) -> bool:
        return self is Eligibility.ELIGIBLE


@dataclass(frozen=True)
class LicenseVerdict:
    """Why a repository's code may or may not be redistributed."""

    eligibility: Eligibility
    spdx_id: str | None
    reason: str
    requires_notice: bool = False

    # Input: none
    # Output: bool
    # Processing: Usable
    # Changed by: Sourish
    # Date: 2026-10-12
    @property
    def usable(self) -> bool:
        return self.eligibility.usable


# Input: raw (str | None)
# Output: str | None
# Processing: Canonicalise a GitHub licence id, or None when it says nothing.
# Changed by: Sourish
# Date: 2026-10-12
def normalize_spdx(raw: str | None) -> str | None:
    """Canonicalise a GitHub licence id, or None when it says nothing.

    GitHub reports an unrecognised or missing licence as ``other``,
    ``NOASSERTION`` or null. All three mean "we could not determine this",
    which is not the same as permissive and must not be read as such.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if cleaned.lower() in _GITHUB_UNKNOWN:
        return None
    # GitHub lowercases ids ("mit", "apache-2.0"); SPDX is case-sensitive.
    for known in (*PERMISSIVE_SPDX, *COPYLEFT_SPDX):
        if known.lower() == cleaned.lower():
            return known
    return cleaned


# Input: raw_spdx (str | None), allowed (frozenset[str])
# Output: LicenseVerdict
# Processing: Decide eligibility from a repository's SPDX identifier.
# Changed by: Sourish
# Date: 2026-10-12
def classify_license(
    raw_spdx: str | None,
    *,
    allowed: frozenset[str] = PERMISSIVE_SPDX,
) -> LicenseVerdict:
    """Decide eligibility from a repository's SPDX identifier."""
    spdx = normalize_spdx(raw_spdx)

    if spdx is None:
        return LicenseVerdict(
            eligibility=Eligibility.UNLICENSED,
            spdx_id=None,
            reason=(
                "no licence detected; all rights reserved by default, so the code "
                "may be read but not copied into a user's project"
            ),
        )
    if spdx in allowed:
        return LicenseVerdict(
            eligibility=Eligibility.ELIGIBLE,
            spdx_id=spdx,
            reason=f"{spdx} permits redistribution with notice preserved",
            requires_notice=spdx in NOTICE_REQUIRED_SPDX,
        )
    if spdx in COPYLEFT_SPDX:
        return LicenseVerdict(
            eligibility=Eligibility.COPYLEFT,
            spdx_id=spdx,
            reason=(
                f"{spdx} is copyleft; copying it into a user's project would impose "
                "its terms on their work"
            ),
        )
    return LicenseVerdict(
        eligibility=Eligibility.UNKNOWN,
        spdx_id=spdx,
        reason=f"{spdx} is not on the allowlist and has not been reviewed",
    )


# Input: license_text (str)
# Output: str | None
# Processing: Pull the copyright holder out of a licence body.
# Changed by: Sourish
# Date: 2026-10-12
def extract_copyright_holder(license_text: str) -> str | None:
    """Pull the copyright holder out of a licence body.

    MIT and BSD require the *copyright notice* to be preserved, not just the
    licence type, so the holder is part of what has to be redistributed. The
    parse is best-effort: licences are prose, and a repository that has edited
    the template can defeat any regex. A miss returns None, which the
    provenance record treats as a reason to fall back to the repository owner
    and mark the attribution as needing review -- never as "no holder".
    """
    if not license_text:
        return None
    for match in _COPYRIGHT_RE.finditer(license_text):
        holder = (match.group("holder") or "").strip(" .,;:-\t")
        # Template placeholders are worse than nothing: they would ship a
        # notice crediting "<year> <copyright holders>".
        if not holder or holder.startswith("<") or "copyright holder" in holder.lower():
            continue
        if holder.lower().startswith(("and ", "all rights")):
            continue
        return holder[:200]
    return None
