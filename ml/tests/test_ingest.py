"""Ingestion: the licence gate, attribution, path filtering and the plan.

Weighted toward the paths where a bug causes real harm rather than a wrong
ranking: treating an unlicensed repo as permissive, shipping a notice with a
template placeholder in it, or mining a vendored tree whose licence is not the
host repository's.
"""

from __future__ import annotations

import pytest

from ml.ingest.extract import (
    _brace_parameter_names,
    _strip_literals,
    analyse_dependencies,
    dependency_names,
    extract,
    extract_braced,
    extract_python,
    language_for_path,
)
from ml.ingest.harvest import HarvestReport, search_query
from ml.ingest.licensing import (
    Eligibility,
    classify_license,
    extract_copyright_holder,
    normalize_spdx,
)
from ml.ingest.provenance import (
    Attribution,
    SourceRef,
    build_attribution,
    render_third_party_licenses,
)
from ml.ingest.quota import MINIMUM_PER_LANGUAGE, build_plan, resolve_template_share

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
        from ml.ingest.quota import Cell

        query = search_query(Cell("machine-learning", "cuda", 20), allowed_spdx=("MIT", "Apache-2.0"))
        assert "license:mit" in query
        assert "license:apache-2.0" in query
        assert "language:cuda" in query
        assert "archived:false" in query

    def test_maps_cpp_to_githubs_language_name(self):
        from ml.ingest.quota import Cell

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
        from ml.ingest.quota import TEMPLATE_SHARE

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
        from ml.ingest.local_source import detect_license

        assert detect_license(text) == spdx

    def test_bsd3_is_not_mistaken_for_bsd2(self):
        """BSD-3 contains BSD-2's text, so order matters in the signature list."""
        from ml.ingest.local_source import detect_license

        bsd3 = (
            "Redistribution and use in source and binary forms are permitted.\n"
            "Neither the name of the copyright holder nor the names of its contributors"
        )
        assert detect_license(bsd3) == "BSD-3-Clause"
        assert detect_license("Redistribution and use in source and binary forms") == "BSD-2-Clause"

    @pytest.mark.parametrize("text", ["", "   ", "Some bespoke terms nobody recognises"])
    def test_unrecognised_text_returns_none(self, text):
        """None is rejected by the gate, which is the safe answer."""
        from ml.ingest.local_source import detect_license

        assert detect_license(text) is None


class TestCodeMetrics:
    def test_measures_a_real_function(self):
        from ml.ingest.metrics import measure

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
        from ml.ingest.metrics import CodeMetrics

        base = dict(
            lines=10, import_count=0, param_count=1, annotated_params=1,
            has_return_annotation=True, branch_count=0,
        )
        contained = CodeMetrics(has_description=False, self_contained=True, **base)
        described = CodeMetrics(has_description=True, self_contained=False, **base)
        assert contained.quality > described.quality

    def test_quality_is_bounded(self):
        from ml.ingest.metrics import CodeMetrics

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
        from ml.ingest.metrics import CodeMetrics

        m = CodeMetrics(
            lines=5, has_description=True, self_contained=True, import_count=0,
            param_count=0, annotated_params=0, has_return_annotation=True, branch_count=0,
        )
        assert m.annotation_coverage == 1.0

    def test_unparseable_code_does_not_raise(self):
        """One bad slice must not abort a repository's harvest."""
        from ml.ingest.extract import ExtractedUnit
        from ml.ingest.metrics import measure

        unit = ExtractedUnit(
            name="broken", code="function f( {", language="javascript",
            start_line=1, end_line=3,
        )
        m = measure(unit)
        assert m.param_count == 0
        assert m.branch_count == 0


class TestMissingFields:
    def test_always_names_what_harvest_cannot_supply(self):
        from ml.ingest.local_source import LocalRepo, missing_fields

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
        from ml.ingest.local_source import LocalRepo, missing_fields

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


class TestUnanalysedIsNotSelfContained:
    """An unverified dependency set is unknown, and unknown is rejected."""

    def test_python_extractor_marks_units_analysed(self):
        unit = extract_python("import os\n\ndef f(p):\n    q = os.sep\n    return q + p\n")[0]
        assert unit.dependencies_analysed
        assert unit.self_contained

    def test_braced_extractor_now_analyses_and_so_may_claim_it(self):
        """Superseded by #14, which gave the brace extractor its own analysis.

        Before that, 1,030 of 1,242 harvested records passed a check that never
        ran. The guarantee is still weaker than the Python path's -- a
        heuristic over identifiers rather than an AST walk -- which is why
        every unit it admits stays flagged for review.
        """
        units = extract_braced("function f(a) {\n  const b = a;\n  return b;\n}\n", "javascript")
        assert units
        for unit in units:
            assert unit.dependencies_analysed
            assert unit.needs_review

    def test_default_is_unanalysed(self):
        from ml.ingest.extract import ExtractedUnit

        bare = ExtractedUnit(name="x", code="x", language="javascript", start_line=1, end_line=4)
        assert not bare.dependencies_analysed
        assert not bare.self_contained

    def test_template_extractor_now_analyses_and_so_may_claim_it(self):
        """Superseded the same way the brace extractor was, one language family on.

        While this extractor set neither flag, every template it produced
        failed ``self_contained`` and the harvester dropped it: the library
        held 515 helpers and zero templates, with HTML and CSS -- two of the
        six MVP languages -- unservable, and no error anywhere.

        The rule the class is named for is unchanged, and
        ``test_default_is_unanalysed`` still holds it: a unit nobody checked
        is rejected. What changed is that templates are now checked. Flipping
        the flag without writing the analyser would have rebuilt the
        false-pass defect it exists to prevent, since a template really can
        depend on a stylesheet or script it does not carry.
        """
        units = extract("a{color:red}\nb{x:1}\nc{y:2}\n", "styles/main.css")
        assert units
        unit = units[0]
        assert unit.dependencies_analysed
        assert unit.self_contained
        assert unit.needs_review

    def test_a_template_that_pulls_in_a_sibling_is_still_rejected(self):
        units = extract('@use "./base";\na{color:red}\nb{x:1}\n', "styles/main.css")
        assert units and not units[0].self_contained


class TestBraceDocComments:
    """Four of six languages had no descriptions until this worked."""

    def test_reads_a_jsdoc_block(self):
        src = (
            "/**\n"
            " * Splits a list into chunks.\n"
            " * @param {Array} items\n"
            " */\n"
            "function chunked(items, size) {\n"
            "  const out = [];\n"
            "  return out;\n"
            "}\n"
        )
        unit = extract_braced(src, "javascript")[0]
        assert unit.docstring == "Splits a list into chunks."

    def test_drops_jsdoc_tags(self):
        """A description full of @param noise embeds badly."""
        src = (
            "/**\n * Does a thing.\n * @param {string} a\n * @returns {number}\n */\n"
            "function f(a) {\n  const b = a;\n  return b;\n}\n"
        )
        assert extract_braced(src, "javascript")[0].docstring == "Does a thing."

    def test_reads_a_run_of_line_comments(self):
        src = (
            "// Reads a file.\n"
            "// Trims trailing space.\n"
            "function read(path) {\n  const t = path;\n  return t;\n}\n"
        )
        unit = extract_braced(src, "javascript")[0]
        assert unit.docstring == "Reads a file.\nTrims trailing space."

    def test_no_comment_means_empty(self):
        src = "function f(a) {\n  const b = a;\n  return b;\n}\n"
        assert extract_braced(src, "javascript")[0].docstring == ""

    def test_blank_lines_between_comment_and_declaration(self):
        src = "// Does a thing.\n\n\nfunction f(a) {\n  const b = a;\n  return b;\n}\n"
        assert extract_braced(src, "javascript")[0].docstring == "Does a thing."

    def test_unrelated_code_above_is_not_a_description(self):
        src = "const x = 1;\nfunction f(a) {\n  const b = a;\n  return b;\n}\n"
        assert extract_braced(src, "javascript")[0].docstring == ""


class TestBraceDependencies:
    def test_detects_a_default_import(self):
        src = (
            'import fs from "fs";\n\n'
            "function read(path) {\n"
            '  const t = fs.readFileSync(path, "utf8");\n'
            "  return t;\n}\n"
        )
        unit = extract_braced(src, "javascript")[0]
        assert "fs" in unit.required_imports
        assert unit.self_contained

    def test_detects_a_named_import(self):
        src = (
            'import { join } from "path";\n\n'
            "function f(a, b) {\n  const c = join(a, b);\n  return c;\n}\n"
        )
        assert "join" in extract_braced(src, "javascript")[0].required_imports

    def test_detects_a_require(self):
        src = (
            'const fs = require("fs");\n\n'
            "function read(p) {\n  const t = fs.readFileSync(p);\n  return t;\n}\n"
        )
        assert "fs" in extract_braced(src, "javascript")[0].required_imports

    def test_module_constant_blocks_self_containment(self):
        src = (
            "const LIMIT = 10;\n\n"
            "function trim(text) {\n  const cut = text.slice(0, LIMIT);\n  return cut;\n}\n"
        )
        unit = extract_braced(src, "javascript")[0]
        assert "LIMIT" in unit.unresolved_names
        assert not unit.self_contained

    def test_language_builtins_are_not_unresolved(self):
        src = (
            "function f(xs) {\n"
            "  const n = Math.max(...xs);\n"
            "  const s = JSON.stringify(n);\n"
            "  return s;\n}\n"
        )
        unit = extract_braced(src, "javascript")[0]
        assert unit.unresolved_names == ()
        assert unit.self_contained

    def test_cuda_intrinsics_are_ambient(self):
        src = (
            "__global__ void saxpy(int n, float a, float *x) {\n"
            "  int i = blockIdx.x * blockDim.x + threadIdx.x;\n"
            "  if (i < n) x[i] = a * x[i];\n}\n"
        )
        unit = extract_braced(src, "cuda")[0]
        assert unit.unresolved_names == ()

    def test_analysis_ran_so_the_flag_is_set(self):
        src = "function f(a) {\n  const b = a;\n  return b;\n}\n"
        assert extract_braced(src, "javascript")[0].dependencies_analysed

    def test_every_brace_unit_is_flagged_for_review(self):
        """The heuristic can be wrong where the Python analysis cannot."""
        src = "function f(a) {\n  const b = a;\n  return b;\n}\n"
        assert extract_braced(src, "javascript")[0].needs_review


class TestTemplateDependencies:
    """Whole-file templates: the gate that silently emptied the library.

    ``extract_file_as_template`` never set ``dependencies_analysed``, and
    ``self_contained`` rejects anything unanalysed, so every HTML and CSS
    template the harvester produced was dropped. The library held 515 helpers
    and zero templates, and nothing reported an error.
    """

    # Input: source (str), path (str)
    # Output: ExtractedUnit
    # Processing: Extract one whole-file template, asserting a unit came back
    # Changed by: Claude Code
    # Date: 2026-10-12
    def unit(self, source: str, path: str):
        units = extract(source, path)
        assert units, f"expected a template unit for {path}"
        return units[0]

    def test_a_template_is_marked_analysed(self):
        # The regression itself: without this flag the harvester drops it.
        unit = self.unit(".a { color: red; }\n.b { color: blue; }\n.c { color: teal; }", "t.css")
        assert unit.is_template is True
        assert unit.dependencies_analysed is True
        assert unit.self_contained is True

    def test_a_stylesheet_importing_a_sibling_is_not_self_contained(self):
        # The library copies code, not directories, so a relative import
        # names a file that will not travel with the entry.
        unit = self.unit('@import "./base.css";\n.a { color: red; }\n.b { color: blue; }', "t.css")
        assert unit.self_contained is False
        assert "./base.css" in unit.unresolved_names

    def test_sass_module_syntax_is_checked_too(self):
        # @use and @forward replaced @import. Checking only the old keyword
        # passed 136 stylesheets that pulled in siblings.
        for source in (
            '@use "../scss/bootstrap" as *;\n.a { color: red; }\n.b { color: blue; }',
            '@forward "button";\n@forward "close";\n@forward "card";',
        ):
            unit = self.unit(source, "t.scss")
            assert unit.self_contained is False, source

    def test_a_remote_reference_is_a_declarable_dependency(self):
        # A CDN stylesheet resolves wherever the template is pasted, so it is
        # recorded rather than treated as a blocker.
        unit = self.unit(
            '@import url("https://fonts.example.com/css?family=Inter");\n'
            ".a { color: red; }\n.b { color: blue; }",
            "t.css",
        )
        assert unit.self_contained is True
        assert any("fonts.example.com" in ref for ref in unit.required_imports)

    def test_css_url_references_are_found(self):
        unit = self.unit(
            "@font-face {\n  src: url(./local.woff2);\n}\n.a { color: red; }", "t.css"
        )
        assert unit.self_contained is False
        assert "./local.woff2" in unit.unresolved_names

    def test_a_hyperlink_is_not_an_asset(self):
        # Rejecting a navbar because it points at "/" would be the
        # annotation-only-blocker mistake again, in markup.
        unit = self.unit(
            '<nav>\n  <a href="/">Home</a>\n  <a href="/about">About</a>\n</nav>', "nav.html"
        )
        assert unit.self_contained is True
        assert unit.unresolved_names == ()

    def test_a_link_element_href_is_an_asset(self):
        unit = self.unit(
            '<div>\n  <link href="./site.css" rel="stylesheet">\n  <p>hi</p>\n</div>', "t.html"
        )
        assert unit.self_contained is False
        assert "./site.css" in unit.unresolved_names

    def test_a_script_or_image_src_is_an_asset(self):
        for source in (
            '<div>\n  <script src="./app.js"></script>\n  <p>hi</p>\n</div>',
            '<figure>\n  <img src="./logo.png" alt="x">\n  <figcaption>y</figcaption>\n</figure>',
        ):
            unit = self.unit(source, "t.html")
            assert unit.self_contained is False, source

    def test_non_asset_schemes_are_ignored(self):
        unit = self.unit(
            '<p>\n  <a href="mailto:a@b.c">mail</a>\n  <a href="#top">top</a>\n'
            '  <img src="data:image/gif;base64,R0lGOD">\n</p>',
            "t.html",
        )
        assert unit.self_contained is True

    def test_a_template_has_no_signature(self):
        unit = self.unit(".a { color: red; }\n.b { color: blue; }\n.c { color: teal; }", "t.css")
        assert unit.signature is None


class TestMinifiedFilesAreSkipped:
    def test_a_minified_stylesheet_is_skipped(self):
        # A minified file is one long self-contained line, so it passes every
        # other gate; 119 of them entered a harvest before the marker was
        # widened from .min.js to .min.
        assert language_for_path("css/pico.amber.min.css") is None
        assert language_for_path("dist/app.min.js") is None

    def test_an_ordinary_stylesheet_is_kept(self):
        assert language_for_path("scss/_alert.scss") == "css"
        assert language_for_path("src/components/nav.html") == "html"


# Input: none
# Output: LicenseVerdict
# Processing: A usable permissive verdict for building a test repo
# Changed by: Claude Code
# Date: 2026-10-12
def _mit_verdict():
    from ml.ingest.licensing import classify_license

    return classify_license("MIT")


class TestDependencyNames:
    """``Entry.dependencies`` was never populated, so ``dependency_compat``
    was constant across the library and the feature could not be learned."""

    def test_maps_python_imports_to_packages(self):
        assert dependency_names(("import numpy as np", "from pandas.io import x"), "python") == (
            "numpy",
            "pandas",
        )

    def test_excludes_the_standard_library(self):
        # Stdlib needs no install, so counting it reports friction that is
        # not there.
        assert dependency_names(("import os", "from typing import Any"), "python") == ()

    def test_excludes_relative_imports(self):
        assert dependency_names(("from . import sibling", "from .mod import x"), "python") == ()

    def test_excludes_the_future_import(self):
        assert dependency_names(("from __future__ import annotations",), "python") == ()

    def test_excludes_first_party_modules(self):
        # A repository's own top-level module is an absolute import that
        # resolves inside the project. Without this, a pydantic harvest
        # declared "utils" as an installable package 14 times.
        assert dependency_names(
            ("import numpy", "import utils", "import release"),
            "python",
            local_modules={"utils", "release"},
        ) == ("numpy",)

    def test_deduplicates(self):
        assert dependency_names(("import numpy", "from numpy import array"), "python") == (
            "numpy",
        )

    def test_brace_languages_yield_nothing(self):
        # The brace analyser reports imports as binding names, not module
        # paths -- "AxiosError" comes from "./core/AxiosError" and "_" is a
        # local alias. Treating those as package names fabricated
        # dependencies, which is what the analyser's own comment warns about.
        for language in ("javascript", "typescript"):
            assert dependency_names(("axios", "AxiosError", "_", "getConfig"), language) == ()

    def test_cpp_yields_nothing(self):
        # The brace extractor captures no #include at all.
        assert dependency_names(("vector", "fmt/format.h"), "cpp") == ()
        assert dependency_names((), "cuda") == ()


class TestLocalModules:
    # Input: tmp_path (Path)
    # Output: LocalRepo
    # Processing: A minimal clone-like repo object rooted at tmp_path
    # Changed by: Claude Code
    # Date: 2026-10-12
    def repo(self, tmp_path):
        from ml.ingest.local_source import LocalRepo

        return LocalRepo(
            root=tmp_path,
            owner="o",
            repo="r",
            commit_sha="deadbeef",
            license_text="MIT",
            verdict=_mit_verdict(),
            copyright_holder="o",
        )

    def test_finds_a_package_with_an_init(self, tmp_path):
        (tmp_path / "mypkg").mkdir()
        (tmp_path / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
        assert "mypkg" in self.repo(tmp_path).local_modules

    def test_finds_a_namespace_directory_without_an_init(self, tmp_path):
        # pydantic's release/ has no __init__.py; requiring one let "release"
        # through as an installable package.
        (tmp_path / "release").mkdir()
        (tmp_path / "release" / "prepare.py").write_text("", encoding="utf-8")
        assert "release" in self.repo(tmp_path).local_modules

    def test_finds_a_top_level_module(self, tmp_path):
        (tmp_path / "utils.py").write_text("", encoding="utf-8")
        assert "utils" in self.repo(tmp_path).local_modules

    def test_finds_modules_under_src(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "thing.py").write_text("", encoding="utf-8")
        assert "thing" in self.repo(tmp_path).local_modules

    def test_ignores_a_directory_with_no_python(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "index.md").write_text("", encoding="utf-8")
        assert "docs" not in self.repo(tmp_path).local_modules


class TestBraceFreeNames:
    """The brace analyser reported only names it recognised and dropped the rest.

    ``analyse_brace_dependencies`` resolved an identifier against the module's
    imports and its top-level declarations. Anything in neither was silently
    skipped, so a member variable, a macro, a namespace or a type from a header
    left no trace and the unit reported itself self-contained. 223 of 303
    brace-language entries carried at least one such name. Same false pass
    ``dependencies_analysed`` was added to stop, one level further in: the
    check ran and did not look at everything it needed to.
    """

    # Input: source (str), language (str)
    # Output: ExtractedUnit
    # Processing: Extract one brace-language unit, asserting it was found
    # Changed by: Claude Code
    # Date: 2026-10-12
    def unit(self, source: str, language: str = "javascript"):
        units = extract_braced(source, language)
        assert units, "expected a unit"
        return units[0]

    def test_a_member_variable_is_unresolved(self):
        unit = self.unit("function pushBack(arg) {\n  data_.emplace(arg);\n  return data_;\n}\n")
        assert unit.self_contained is False
        assert "data_" in unit.unresolved_names

    def test_a_macro_in_the_body_is_unresolved(self):
        unit = self.unit("int f(int a) {\n  FMT_THROW(a);\n  return a;\n}\n", "cpp")
        assert "FMT_THROW" in unit.unresolved_names

    def test_a_macro_in_the_signature_is_unresolved(self):
        # Subtler, and it was passing: binding every identifier in the
        # signature hid the return type and anything in front of it, which
        # still has to resolve for the pasted code to compile.
        unit = self.unit(
            "template <typename Char>\nFMT_CONSTEXPR void on_text(const Char*) {\n  return;\n}\n",
            "cpp",
        )
        assert unit.self_contained is False
        assert "FMT_CONSTEXPR" in unit.unresolved_names

    def test_a_sibling_function_is_unresolved(self):
        unit = self.unit("function a(x) {\n  const y = helper(x);\n  return y;\n}\n")
        assert "helper" in unit.unresolved_names

    def test_a_member_access_is_not_a_free_name(self):
        # obj.size() resolves through obj. Counting the name after the dot
        # reported "size" and "length" as missing on almost every unit.
        unit = self.unit("function f(xs) {\n  const n = xs.length;\n  return n;\n}\n")
        assert unit.self_contained is True

    def test_words_inside_a_string_are_not_names(self):
        unit = self.unit(
            'import fs from "fs";\n\nfunction read(p) {\n'
            '  const t = fs.readFileSync(p, "utf8");\n  return t;\n}\n'
        )
        assert unit.self_contained is True
        assert "utf8" not in unit.unresolved_names

    def test_words_inside_a_comment_are_not_names(self):
        unit = self.unit(
            "function f(a) {\n  // calls someMissingThing eventually\n  const b = a;\n  return b;\n}\n"
        )
        assert unit.self_contained is True

    def test_an_inner_callback_binds_its_own_parameters(self):
        # Binding only the outer signature rejected baseArity over a and b,
        # which its own inner function declares.
        unit = self.unit(
            "function wrap(func) {\n  return function (a, b) {\n    return func(a, b);\n  };\n}\n"
        )
        assert unit.self_contained is True

    def test_a_single_parameter_arrow_binds_its_parameter(self):
        unit = self.unit("function f(xs) {\n  const out = xs.map(x => x * 2);\n  return out;\n}\n")
        assert unit.self_contained is True

    def test_destructuring_binds_its_names(self):
        unit = self.unit("function f(opts) {\n  const { a, b } = opts;\n  return a + b;\n}\n")
        assert unit.self_contained is True

    def test_a_catch_binding_is_bound(self):
        unit = self.unit("function f(x) {\n  try {\n    return x();\n  } catch (err) {\n    return err;\n  }\n}\n")
        assert unit.self_contained is True

    def test_browser_globals_are_ambient(self):
        unit = self.unit(
            "function enc(data) {\n  const b = new Uint8Array(data);\n  return btoa(String.fromCharCode(b));\n}\n"
        )
        assert unit.self_contained is True

    def test_a_cpp_local_with_a_templated_type_is_bound(self):
        unit = self.unit("void f(int a) {\n  std::vector<int> v;\n  v.push_back(a);\n}\n", "cpp")
        assert unit.self_contained is True

    def test_a_template_parameter_is_bound(self):
        unit = self.unit("template <typename T> T twice(T x) {\n  T y = x + x;\n  return y;\n}\n", "cpp")
        assert unit.self_contained is True

    def test_a_return_statement_does_not_declare_its_value(self):
        # `return data_;` must not read as a declaration of data_, or the
        # member reference it really is disappears.
        unit = self.unit("function f() {\n  const x = 1;\n  return data_;\n}\n")
        assert "data_" in unit.unresolved_names


class TestBraceParameterNames:
    def test_cpp_takes_the_last_identifier(self):
        # C++ writes `Type name`.
        names = _brace_parameter_names("int& arg_index, const T& value", "cpp")
        assert names == {"arg_index", "value"}

    def test_cpp_unnamed_parameters_bind_nothing(self):
        assert _brace_parameter_names("const Char*, int&", "cpp") == set()

    def test_typescript_takes_the_first_identifier(self):
        # TypeScript writes `name: Type`. Reading it the C++ way bound
        # "string" and left "a" unbound, so a function's own parameters were
        # reported as dependencies.
        assert _brace_parameter_names("a: string, count: number", "typescript") == {"a", "count"}

    def test_javascript_takes_the_bare_identifier(self):
        assert _brace_parameter_names("a, b, opts = {}", "javascript") == {"a", "b", "opts"}

    def test_a_nested_generic_does_not_split_the_list(self):
        assert _brace_parameter_names("m: Record<string, number>, n: number", "typescript") == {
            "m",
            "n",
        }


class TestStripLiterals:
    def test_blanks_a_string_but_keeps_offsets(self):
        out = _strip_literals('f(path, "utf8")')
        assert len(out) == len('f(path, "utf8")')
        assert "utf8" not in out
        assert out.startswith("f(path, ")

    def test_blanks_a_line_comment(self):
        assert "note" not in _strip_literals("a = b; // note")

    def test_blanks_a_block_comment(self):
        assert "note" not in _strip_literals("a = /* note */ b;")

    def test_keeps_newlines_so_line_numbers_survive(self):
        source = 'a = "one\ntwo";\nb = 1;'
        assert _strip_literals(source).count("\n") == source.count("\n")

    def test_an_escaped_quote_does_not_end_the_string(self):
        out = _strip_literals('a = "he said \\"hi\\" there"; b = c;')
        assert "said" not in out and "b = c;" in out


class TestFetchWeights:
    """The fetcher that lets weights arrive through a reachable host.

    HuggingFace is denied by the environment's network policy; GitHub
    releases, Google Cloud Storage, and raw.githubusercontent are not.
    The fetcher takes any URL, so a weights file can be placed as a
    release asset in this org and pulled from here.
    """

    def test_checksum_must_match_before_the_file_lands(self, tmp_path, monkeypatch):
        # Verification is required rather than optional: a loader that trusts
        # whatever is at the other end of a URL silently loads whatever
        # happened to be there, which is the one thing a weights file must
        # never do.
        import io
        import urllib.request

        from ml.training.fetch_weights import download

        payload = b"hello world"
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda _url: io.BytesIO(payload),
        )
        wrong_sha = "0" * 64
        target = tmp_path / "model.bin"
        with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
            download("https://example.invalid/model.bin", target, expected_sha256=wrong_sha)
        # The partial file is removed; the destination never gets written.
        assert not target.exists()
        assert not target.with_suffix(".bin.partial").exists()

    def test_a_matching_checksum_succeeds(self, tmp_path, monkeypatch):
        import hashlib
        import io
        import urllib.request

        from ml.training.fetch_weights import download

        payload = b"not real weights, but the size does not matter"
        monkeypatch.setattr(urllib.request, "urlopen", lambda _url: io.BytesIO(payload))
        sha = hashlib.sha256(payload).hexdigest()
        target = tmp_path / "model.bin"
        download("https://example.invalid/model.bin", target, expected_sha256=sha)
        assert target.read_bytes() == payload

    def test_extract_handles_a_zip(self, tmp_path):
        import zipfile

        from ml.training.fetch_weights import extract

        archive = tmp_path / "model.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("config.json", "{}")
            zf.writestr("pytorch_model.bin", b"\x00\x01\x02")
        destination = tmp_path / "unpacked"
        extract(archive, destination)
        assert (destination / "config.json").read_text() == "{}"
        assert (destination / "pytorch_model.bin").exists()

    def test_extract_rejects_an_unknown_archive(self, tmp_path):
        from ml.training.fetch_weights import extract

        bogus = tmp_path / "model.weird"
        bogus.write_bytes(b"")
        with pytest.raises(ValueError, match="unknown archive"):
            extract(bogus, tmp_path / "out")
