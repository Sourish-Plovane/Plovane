"""Seeding the library from GitHub, licence-first.

The gate is :mod:`plovane.ingest.licensing`: an allowlist of permissive SPDX
identifiers checked before any code is fetched. Attribution is generated from
:mod:`plovane.ingest.provenance`, which carries the licence text and copyright
notice that MIT and BSD require to travel with the code -- a credit line alone
does not satisfy them.
"""

from plovane.ingest.extract import ExtractedUnit, extract, language_for_path
from plovane.ingest.github_client import GitHubClient, GitHubError, RateLimited
from plovane.ingest.licensing import (
    COPYLEFT_SPDX,
    PERMISSIVE_SPDX,
    Eligibility,
    LicenseVerdict,
    classify_license,
    extract_copyright_holder,
    normalize_spdx,
)
from plovane.ingest.provenance import (
    Attribution,
    SourceRef,
    build_attribution,
    render_third_party_licenses,
)
from plovane.ingest.quota import CATEGORIES, Cell, HarvestPlan, build_plan
from plovane.ingest.harvest import HarvestedEntry, HarvestReport, harvest, search_query

__all__ = [
    "CATEGORIES",
    "COPYLEFT_SPDX",
    "PERMISSIVE_SPDX",
    "Attribution",
    "Cell",
    "Eligibility",
    "ExtractedUnit",
    "GitHubClient",
    "GitHubError",
    "HarvestPlan",
    "HarvestReport",
    "HarvestedEntry",
    "LicenseVerdict",
    "RateLimited",
    "SourceRef",
    "build_attribution",
    "build_plan",
    "classify_license",
    "extract",
    "extract_copyright_holder",
    "harvest",
    "language_for_path",
    "normalize_spdx",
    "render_third_party_licenses",
    "search_query",
]
