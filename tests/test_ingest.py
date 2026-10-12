"""Ingestion: the licence gate, attribution, path filtering and the plan.

Weighted toward the paths where a bug causes real harm rather than a wrong
ranking: treating an unlicensed repo as permissive, shipping a notice with a
template placeholder in it, or mining a vendored tree whose licence is not the
host repository's.
"""

from __future__ import annotations

import pytest

from plovane.ingest.extract import (
    analyse_dependencies,
    extract,
    extract_braced,
    extract_python,
    language_for_path,
)
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


class TestSelfContainment:
    """A unit that cannot run once pasted is not a library entry."""

    def test_captures_the_imports_a_function_needs(self):
        src = (
            "import os\n"
            "import sys\n"
            "import json\n"
            "\n"
            "def f(path):\n"
            "    p = os.path.realpath(path)\n"
            "    sys.path.insert(0, p)\n"
            "    return p\n"
        )
        u = extract_python(src)[0]
        assert u.required_imports == ("import os", "import sys")
        assert "import json" not in u.required_imports, "unused imports must not ride along"
        assert u.self_contained

    def test_insertable_code_prepends_the_imports(self):
        src = "import os\n\ndef f(p):\n    q = os.sep\n    return q + p\n"
        u = extract_python(src)[0]
        assert u.insertable_code.startswith("import os\n\ndef f")

    def test_no_imports_means_code_unchanged(self):
        src = "def f(a, b):\n    c = a + b\n    return c\n"
        u = extract_python(src)[0]
        assert u.insertable_code == u.code

    def test_sibling_function_makes_it_not_self_contained(self):
        src = (
            "def _helper(x):\n    return x\n\n"
            "def f(a):\n    b = _helper(a)\n    return b\n"
        )
        f = [u for u in extract_python(src) if u.name == "f"][0]
        assert not f.self_contained
        assert "_helper" in f.unresolved_names

    def test_module_constant_makes_it_not_self_contained(self):
        src = "LIMIT = 10\n\ndef f(x):\n    y = x + LIMIT\n    return y\n"
        u = extract_python(src)[0]
        assert not u.self_contained
        assert "LIMIT" in u.unresolved_names

    def test_builtins_are_not_unresolved(self):
        src = "def f(xs):\n    n = len(xs)\n    return str(n)\n"
        u = extract_python(src)[0]
        assert u.self_contained
        assert u.unresolved_names == ()

    def test_from_import_is_rendered_correctly(self):
        src = "from os.path import join\n\ndef f(a, b):\n    c = join(a, b)\n    return c\n"
        u = extract_python(src)[0]
        assert u.required_imports == ("from os.path import join",)

    def test_aliased_import_keeps_its_alias(self):
        src = "import numpy as np\n\ndef f(x):\n    y = np.asarray(x)\n    return y\n"
        u = extract_python(src)[0]
        assert u.required_imports == ("import numpy as np",)

    def test_locally_bound_names_are_not_free(self):
        src = (
            "def f(items):\n"
            "    out = []\n"
            "    for item in items:\n"
            "        out.append(item)\n"
            "    return out\n"
        )
        u = extract_python(src)[0]
        assert u.self_contained

    def test_recursion_is_allowed(self):
        src = "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n"
        u = extract_python(src)[0]
        assert u.self_contained

    def test_decorator_must_be_importable(self):
        """An unimported decorator breaks the paste like a missing import."""
        src = "def f(x):\n    y = x\n    return y\n"
        decorated = "@cache\n" + src
        u = extract_python(decorated)[0]
        assert not u.self_contained
        assert "cache" in u.unresolved_names

    def test_imported_decorator_is_captured(self):
        src = "from functools import cache\n\n@cache\ndef f(x):\n    y = x\n    return y\n"
        u = extract_python(src)[0]
        assert u.required_imports == ("from functools import cache",)
        assert u.self_contained

    def test_analyse_dependencies_on_a_real_file(self):
        import ast

        src = (
            "import os\n"
            "SENTINEL = 1\n"
            "def f(p):\n"
            "    a = os.sep\n"
            "    b = SENTINEL\n"
            "    return a, b\n"
        )
        tree = ast.parse(src)
        fn = [n for n in tree.body if isinstance(n, ast.FunctionDef)][0]
        required, unresolved, annotation_only = analyse_dependencies(fn, tree)
        assert required == ("import os",)
        assert unresolved == ("SENTINEL",)
        assert annotation_only == ()


class TestAnnotationOnlyNames:
    """A type a helper cannot import does not stop the helper running."""

    def test_unresolvable_annotation_does_not_block(self):
        src = (
            "import os\n\n"
            "def f(x: CoreSchema, y: int) -> SerSchema:\n"
            "    p = os.sep\n"
            "    return p + str(y)\n"
        )
        u = extract_python(src)[0]
        assert u.self_contained
        assert u.unresolved_names == ()
        assert u.annotation_only_names == ("CoreSchema", "SerSchema")

    def test_future_import_leads_the_insertable_code(self):
        """Without it the annotation is evaluated and raises NameError."""
        src = "def f(x: Missing) -> Missing:\n    y = x\n    return y\n"
        u = extract_python(src)[0]
        assert u.insertable_code.startswith("from __future__ import annotations")

    def test_insertable_code_actually_runs(self):
        src = "import os\n\ndef f(x: Missing) -> Missing:\n    y = os.sep\n    return y + str(x)\n"
        u = extract_python(src)[0]
        namespace: dict = {}
        exec(compile(u.insertable_code, "<entry>", "exec"), namespace)
        assert namespace["f"](1) == os_sep_plus_one()

    def test_runtime_use_outranks_an_annotation(self):
        """A name used in the body is a real blocker even if also annotated."""
        src = (
            "HELPERS = {}\n\n"
            "def f(x: HELPERS) -> int:\n"
            "    y = HELPERS.get(x)\n"
            "    return y\n"
        )
        u = extract_python(src)[0]
        assert not u.self_contained
        assert "HELPERS" in u.unresolved_names
        assert "HELPERS" not in u.annotation_only_names

    def test_importable_annotation_becomes_a_required_import(self):
        src = (
            "from typing import Sequence\n\n"
            "def f(xs: Sequence[int]) -> int:\n"
            "    n = len(xs)\n"
            "    return n\n"
        )
        u = extract_python(src)[0]
        assert u.required_imports == ("from typing import Sequence",)
        assert u.annotation_only_names == ()
        assert u.self_contained

    def test_annotation_only_unit_is_flagged_for_review(self):
        """Python runs it; the user's type checker still cannot resolve it."""
        src = "def f(x: Missing) -> int:\n    y = 1\n    return y\n"
        assert extract_python(src)[0].needs_review

    def test_sibling_call_is_still_rejected(self):
        """The looser rule must not let real blockers through."""
        src = "def _h(x):\n    return x\n\ndef f(a):\n    b = _h(a)\n    return b\n"
        f = [u for u in extract_python(src) if u.name == "f"][0]
        assert not f.self_contained


def os_sep_plus_one() -> str:
    import os

    return os.sep + "1"


class TestLicenseTextDetection:
    """Clone harvesting has no SPDX field, so the id comes from the text."""

    @pytest.mark.parametrize(
        ("spdx", "text"),
        [
            ("MIT", "Permission is hereby granted, free of charge, to any person"),
            ("Apache-2.0", "Apache License\nVersion 2.0, January 2004"),
            ("ISC", "Permission to use, copy, modify, and/or distribute this software"),
            ("GPL-3.0", "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007"),
        ],
    )
    def test_matches_known_licences(self, spdx, text):
        from plovane.ingest.local_source import detect_license

        assert detect_license(text) == spdx

    def test_bsd3_is_not_mistaken_for_bsd2(self):
        """BSD-3 contains BSD-2's text, so order matters in the signature list."""
        from plovane.ingest.local_source import detect_license

        bsd3 = (
            "Redistribution and use in source and binary forms are permitted.\n"
            "Neither the name of the copyright holder nor the names of its contributors"
        )
        assert detect_license(bsd3) == "BSD-3-Clause"
        assert detect_license("Redistribution and use in source and binary forms") == "BSD-2-Clause"

    @pytest.mark.parametrize("text", ["", "   ", "Some bespoke terms nobody recognises"])
    def test_unrecognised_text_returns_none(self, text):
        """None is rejected by the gate, which is the safe answer."""
        from plovane.ingest.local_source import detect_license

        assert detect_license(text) is None


class TestCodeMetrics:
    def test_measures_a_real_function(self):
        from plovane.ingest.metrics import measure

        src = (
            "import os\n\n"
            "def f(path: str, flag: bool) -> str:\n"
            '    """Doc."""\n'
            "    if flag:\n"
            "        return os.sep\n"
            "    return path\n"
        )
        unit = extract_python(src)[0]
        m = measure(unit)
        assert m.has_description
        assert m.self_contained
        assert m.import_count == 1
        assert m.param_count == 2 and m.annotated_params == 2
        assert m.annotation_coverage == 1.0
        assert m.has_return_annotation
        assert m.branch_count >= 1

    def test_quality_rewards_self_containment_most(self):
        from plovane.ingest.metrics import CodeMetrics

        base = dict(
            lines=10, import_count=0, param_count=1, annotated_params=1,
            has_return_annotation=True, branch_count=0,
        )
        contained = CodeMetrics(has_description=False, self_contained=True, **base)
        described = CodeMetrics(has_description=True, self_contained=False, **base)
        assert contained.quality > described.quality

    def test_quality_is_bounded(self):
        from plovane.ingest.metrics import CodeMetrics

        best = CodeMetrics(
            lines=10, has_description=True, self_contained=True, import_count=1,
            param_count=2, annotated_params=2, has_return_annotation=True, branch_count=2,
        )
        worst = CodeMetrics(
            lines=900, has_description=False, self_contained=False, import_count=0,
            param_count=3, annotated_params=0, has_return_annotation=False, branch_count=0,
        )
        assert best.quality == 1.0
        assert worst.quality == 0.0

    def test_no_parameters_counts_as_full_coverage(self):
        from plovane.ingest.metrics import CodeMetrics

        m = CodeMetrics(
            lines=5, has_description=True, self_contained=True, import_count=0,
            param_count=0, annotated_params=0, has_return_annotation=True, branch_count=0,
        )
        assert m.annotation_coverage == 1.0

    def test_unparseable_code_does_not_raise(self):
        """One bad slice must not abort a repository's harvest."""
        from plovane.ingest.extract import ExtractedUnit
        from plovane.ingest.metrics import measure

        unit = ExtractedUnit(
            name="broken", code="function f( {", language="javascript",
            start_line=1, end_line=3,
        )
        m = measure(unit)
        assert m.param_count == 0
        assert m.branch_count == 0


class TestMissingFields:
    def test_always_names_what_harvest_cannot_supply(self):
        from plovane.ingest.local_source import LocalRepo, missing_fields

        unit = extract_python("def f(a):\n    b = a\n    return b\n")[0]
        repo = LocalRepo(
            root=pathlib_Path("."), owner="acme", repo="utils", commit_sha="a" * 40,
            license_text=MIT_TEXT, verdict=classify_license("mit"), copyright_holder="Jane Doe",
        )
        attribution = build_attribution(
            source(), classify_license("mit"), license_text=MIT_TEXT, parsed_holder="Jane Doe"
        )
        missing = missing_fields(unit, attribution, repo)
        assert {"tests", "mutation_score", "embedding"} <= set(missing)

    def test_names_an_absent_description(self):
        from plovane.ingest.local_source import LocalRepo, missing_fields

        unit = extract_python("def f(a):\n    b = a\n    return b\n")[0]
        repo = LocalRepo(
            root=pathlib_Path("."), owner="acme", repo="utils", commit_sha="a" * 40,
            license_text=MIT_TEXT, verdict=classify_license("mit"), copyright_holder=None,
        )
        attribution = build_attribution(
            source(), classify_license("mit"), license_text=MIT_TEXT, parsed_holder=None
        )
        missing = missing_fields(unit, attribution, repo)
        assert "description" in missing
        assert "copyright_holder" in missing


def pathlib_Path(p):
    import pathlib

    return pathlib.Path(p)
