"""Ingestion: the licence gate, attribution, path filtering and the plan.

Weighted toward the paths where a bug causes real harm rather than a wrong
ranking: treating an unlicensed repo as permissive, shipping a notice with a
template placeholder in it, or mining a vendored tree whose licence is not the
host repository's.
"""

from __future__ import annotations

import pytest

from plovane.ingest.extract import extract, extract_braced, extract_python, language_for_path
from plovane.ingest.harvest import HarvestReport, search_query
from plovane.ingest.licensing import (
    Eligibility,
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
from plovane.ingest.quota import MINIMUM_PER_LANGUAGE, build_plan, resolve_template_share

MIT_TEXT = "MIT License\n\nCopyright (c) 2024 Jane Doe\n\nPermission is hereby granted, free of charge..."


def source() -> SourceRef:
    return SourceRef(owner="acme", repo="utils", path="src/split.py", commit_sha="a" * 40)


class TestLicenceGate:
    @pytest.mark.parametrize("raw", ["mit", "MIT", "Apache-2.0", "bsd-3-clause", "ISC"])
    def test_permissive_is_eligible(self, raw):
        assert classify_license(raw).usable

    @pytest.mark.parametrize("raw", ["GPL-3.0", "agpl-3.0", "LGPL-2.1", "MPL-2.0", "SSPL-1.0"])
    def test_copyleft_is_rejected(self, raw):
        verdict = classify_license(raw)
        assert not verdict.usable
        assert verdict.eligibility is Eligibility.COPYLEFT

    @pytest.mark.parametrize("raw", [None, "", "other", "NOASSERTION", "noassertion"])
    def test_absent_licence_is_not_treated_as_permissive(self, raw):
        """The most dangerous default in the pipeline. No licence means no copying."""
        verdict = classify_license(raw)
        assert not verdict.usable
        assert verdict.eligibility is Eligibility.UNLICENSED
        assert "all rights reserved" in verdict.reason

    def test_unrecognised_licence_is_rejected_not_waved_through(self):
        verdict = classify_license("WTFPL")
        assert not verdict.usable
        assert verdict.eligibility is Eligibility.UNKNOWN

    def test_apache_flags_notice_requirement(self):
        assert classify_license("Apache-2.0").requires_notice
        assert not classify_license("MIT").requires_notice

    def test_normalize_restores_spdx_casing(self):
        assert normalize_spdx("apache-2.0") == "Apache-2.0"
        assert normalize_spdx("other") is None


class TestCopyrightHolder:
    def test_parses_a_standard_notice(self):
        assert extract_copyright_holder(MIT_TEXT) == "Jane Doe"

    def test_rejects_an_unfilled_template(self):
        """Shipping "<year> <copyright holders>" is worse than shipping nothing."""
        assert extract_copyright_holder("Copyright (c) <year> <copyright holders>") is None

    def test_handles_year_ranges_and_symbols(self):
        assert extract_copyright_holder("Copyright © 2019-2024 Acme Inc") == "Acme Inc"

    def test_empty_text(self):
        assert extract_copyright_holder("") is None


class TestAttribution:
    def test_refuses_to_attribute_ineligible_code(self):
        with pytest.raises(ValueError, match="refusing to build attribution"):
            build_attribution(
                source(), classify_license("gpl-3.0"), license_text=MIT_TEXT, parsed_holder="X"
            )

    def test_requires_the_licence_text(self):
        with pytest.raises(ValueError, match="licence text is required"):
            Attribution(
                source=source(),
                verdict=classify_license("mit"),
                copyright_holder="Jane Doe",
                license_text="   ",
            )

    def test_unparsed_holder_falls_back_and_flags_review(self):
        attribution = build_attribution(
            source(), classify_license("mit"), license_text=MIT_TEXT, parsed_holder=None
        )
        assert "acme" in attribution.copyright_holder
        assert attribution.needs_review

    def test_missing_apache_notice_flags_review(self):
        attribution = build_attribution(
            source(), classify_license("Apache-2.0"), license_text=MIT_TEXT, parsed_holder="Jane Doe"
        )
        assert attribution.needs_review

    def test_permalink_is_pinned_to_a_sha_not_a_branch(self):
        ref = SourceRef(
            owner="acme", repo="utils", path="src/a.py", commit_sha="b" * 40, start_line=3, end_line=9
        )
        link = ref.permalink
        assert "b" * 40 in link
        assert "#L3-L9" in link
        assert "/main/" not in link and "/HEAD/" not in link

    @pytest.mark.parametrize(
        ("language", "marker"),
        [("python", "#"), ("javascript", "/*"), ("cuda", "/*"), ("css", "/*"), ("html", "<!--")],
    )
    def test_header_uses_each_language_comment_syntax(self, language, marker):
        attribution = build_attribution(
            source(), classify_license("mit"), license_text=MIT_TEXT, parsed_holder="Jane Doe"
        )
        header = attribution.header(language)
        assert header.lstrip().startswith(marker)
        assert "Jane Doe" in header
        assert "MIT" in header

    def test_python_header_is_valid_python(self):
        """A JSDoc block here would be a syntax error, which is the whole point."""
        import ast

        attribution = build_attribution(
            source(), classify_license("mit"), license_text=MIT_TEXT, parsed_holder="Jane Doe"
        )
        ast.parse(attribution.header("python") + "\ndef f():\n    return 1\n")

    def test_record_is_flat_and_complete(self):
        record = build_attribution(
            source(), classify_license("mit"), license_text=MIT_TEXT, parsed_holder="Jane Doe"
        ).as_record()
        for key in ("source_commit_sha", "license_spdx", "copyright_holder", "source_permalink"):
            assert record[key]


class TestThirdPartyLicenses:
    def test_deduplicates_one_licence_per_repository(self):
        """Ten helpers from one repo owe one copy of its licence, not ten."""
        attributions = [
            build_attribution(
                SourceRef(owner="acme", repo="utils", path=f"src/f{i}.py", commit_sha="c" * 40),
                classify_license("mit"),
                license_text=MIT_TEXT,
                parsed_holder="Jane Doe",
            )
            for i in range(10)
        ]
        rendered = render_third_party_licenses(attributions)
        assert rendered.count("Permission is hereby granted") == 1
        assert rendered.count("## acme/utils") == 1
        assert all(f"src/f{i}.py" in rendered for i in range(10))

    def test_empty_input_is_explicit(self):
        assert "No third-party code" in render_third_party_licenses([])


class TestPathFiltering:
    @pytest.mark.parametrize(
        "path",
        [
            "node_modules/a.js",
            "src/node_modules/a.js",
            "vendor/x.cpp",
            "third_party/z.py",
            "dist/b.js",
            "build/c.js",
            "tests/t.py",
            "src/__tests__/a.js",
            "a.spec.js",
            "bundle.min.js",
            "proto/x_pb2.py",
        ],
    )
    def test_skips_trees_whose_licence_is_not_the_repositorys(self, path):
        """Vendored and generated code carries someone else's licence.

        Root-level matters as much as nested: GitHub tree paths have no leading
        slash, so an unanchored marker misses `node_modules/` at the root.
        """
        assert language_for_path(path) is None

    @pytest.mark.parametrize(
        ("path", "language"),
        [("src/a.py", "python"), ("app/lib.js", "javascript"), ("k.cu", "cuda"), ("s/main.css", "css")],
    )
    def test_recognises_real_source_files(self, path, language):
        assert language_for_path(path) == language


class TestExtraction:
    def test_python_extraction_is_exact(self):
        units = extract_python(
            'def split(df, label, test_size=0.2) -> tuple:\n'
            '    """Doc."""\n'
            "    a = 1\n"
            "    return a, df\n"
        )
        assert len(units) == 1
        assert units[0].name == "split"
        assert units[0].signature == "(df, label, test_size) -> tuple"
        assert units[0].docstring == "Doc."
        assert not units[0].needs_review

    def test_skips_private_and_trivial_functions(self):
        assert extract_python("def _p(x):\n    return x\n\n\ndef t(): return 1\n") == []

    def test_unparseable_python_yields_nothing(self):
        assert extract_python("def broken(:\n") == []

    def test_braced_extraction_survives_a_brace_in_a_string(self):
        units = extract_braced(
            'function f(a) {\n  const s = "} fake {";\n  return a;\n}\n', "javascript"
        )
        assert len(units) == 1
        assert "fake" in units[0].code
        assert units[0].code.rstrip().endswith("}")

    def test_braced_extraction_is_flagged_for_review(self):
        """The heuristic is right often, not always, and says so."""
        units = extract_braced("function f(a) {\n  return a;\n  // pad\n}\n", "javascript")
        assert units and all(u.needs_review for u in units)

    def test_cuda_kernel_is_recognised(self):
        units = extract_braced(
            "__global__ void saxpy(int n, float a) {\n  int i = 0;\n  a += i;\n}\n", "cuda"
        )
        assert [u.name for u in units] == ["saxpy"]

    def test_unbalanced_braces_are_skipped_not_guessed(self):
        assert extract_braced("function f(a) {\n  return a;\n", "javascript") == []

    def test_css_becomes_a_template_without_a_signature(self):
        units = extract("a{color:red}\nb{x:1}\nc{y:2}\n", "styles/main.css")
        assert units[0].is_template
        assert units[0].signature is None or units[0].is_template


class TestPlan:
    def test_allocates_the_exact_target(self):
        assert build_plan(1200).allocated == 1200

    def test_every_mvp_language_clears_the_floor(self):
        """A language below the floor cannot be measured per-language."""
        plan = build_plan(1200)
        assert plan.thin_languages() == ()
        assert all(count >= MINIMUM_PER_LANGUAGE for count in plan.by_language().values())

    def test_cuda_gets_a_measurable_share(self):
        """Where the spec warns training data is scarcest."""
        assert build_plan(1200).by_language()["cuda"] >= MINIMUM_PER_LANGUAGE

    def test_cells_split_into_helpers_and_templates(self):
        for cell in build_plan(1200).cells:
            assert cell.helpers + cell.templates == cell.total

    def test_infeasible_target_is_rejected_with_the_arithmetic(self):
        with pytest.raises(ValueError, match="floor"):
            build_plan(100)

    def test_rejects_non_positive_target(self):
        with pytest.raises(ValueError, match="positive"):
            build_plan(0)

    def test_no_nonsense_cells(self):
        """No CUDA UI components, no CSS databases."""
        pairs = {(c.category, c.language) for c in build_plan(1200).cells}
        assert ("ui-components", "cuda") not in pairs
        assert ("database", "css") not in pairs


class TestSearchQuery:
    def test_filters_licence_server_side(self):
        from plovane.ingest.quota import Cell

        query = search_query(Cell("machine-learning", "cuda", 20), allowed_spdx=("MIT", "Apache-2.0"))
        assert "license:mit" in query
        assert "license:apache-2.0" in query
        assert "language:cuda" in query
        assert "archived:false" in query

    def test_maps_cpp_to_githubs_language_name(self):
        from plovane.ingest.quota import Cell

        assert "language:c++" in search_query(Cell("gpu-kernels", "cpp", 5), allowed_spdx=("MIT",))


class TestReport:
    def test_counts_rejections_by_reason(self):
        report = HarvestReport(planned=10)
        report.reject("copyleft licence")
        report.reject("copyleft licence")
        report.reject("no licence (all rights reserved)")
        summary = report.summary()
        assert "copyleft licence" in summary
        assert report.rejections["copyleft licence"] == 2


class TestTemplateSplit:
    """You choose how many of the target are templates."""

    def test_default_is_a_quarter(self):
        plan = build_plan(1200)
        assert 0.2 < plan.templates / plan.allocated < 0.3

    def test_absolute_count_is_honoured(self):
        plan = build_plan(1200, templates=900)
        # Per-cell rounding makes this approximate, not exact.
        assert abs(plan.templates - 900) <= 10
        assert plan.templates + plan.helpers == plan.allocated

    def test_fraction_is_honoured(self):
        plan = build_plan(1200, template_share=0.75)
        assert abs(plan.templates - 900) <= 10

    def test_all_templates(self):
        plan = build_plan(1200, templates=1200)
        assert plan.helpers == 0
        assert plan.templates == plan.allocated

    def test_no_templates(self):
        plan = build_plan(1200, templates=0)
        assert plan.templates == 0
        assert plan.helpers == plan.allocated

    def test_split_holds_in_every_cell(self):
        for cell in build_plan(1200, templates=600).cells:
            assert cell.templates + cell.helpers == cell.total

    def test_both_arguments_is_rejected(self):
        """Disagreeing arguments are a caller bug, not a preference."""
        with pytest.raises(ValueError, match="not both"):
            build_plan(1200, templates=600, template_share=0.5)

    @pytest.mark.parametrize("bad", [-1, 5000])
    def test_count_outside_target_is_rejected(self, bad):
        with pytest.raises(ValueError, match="templates must be"):
            build_plan(1200, templates=bad)

    @pytest.mark.parametrize("bad", [-0.1, 1.5])
    def test_share_outside_range_is_rejected(self, bad):
        with pytest.raises(ValueError, match="template_share"):
            build_plan(1200, template_share=bad)

    def test_resolve_falls_back_to_the_default(self):
        from plovane.ingest.quota import TEMPLATE_SHARE

        assert resolve_template_share(1200) == TEMPLATE_SHARE

    def test_summary_reports_the_split(self):
        assert "templates" in build_plan(1200, templates=600).summary()
