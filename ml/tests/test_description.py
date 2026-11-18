"""Description generation. The constraint is honesty, not quality."""

from __future__ import annotations

import pytest

from ml.ingest.description import (
    DescriptionSource,
    LLMDescriber,
    NameSignatureDescriber,
    describe_record,
    parse_signature,
    words_from_identifier,
)


class TestWordsFromIdentifier:
    @pytest.mark.parametrize(
        ("identifier", "expected"),
        [
            ("stratified_split", "stratified split"),
            ("readFileSync", "read file sync"),
            ("url_for", "url for"),
            ("saxpy", "saxpy"),
            ("_private_helper", "private helper"),
            ("HTTPError", "httperror"),
        ],
    )
    def test_splits_both_conventions(self, identifier, expected):
        assert words_from_identifier(identifier) == expected

    def test_empty_identifier(self):
        assert words_from_identifier("") == ""


class TestParseSignature:
    def test_drops_defaults_and_annotations(self):
        params, returns = parse_signature("(df, label_col: str, test_size=0.2) -> tuple")
        assert params == ["df", "label_col", "test_size"]
        assert returns == "tuple"

    def test_keeps_nested_generics_whole(self):
        """A comma inside brackets is not a parameter separator."""
        params, returns = parse_signature("(a: dict[str, int], b) -> tuple[A, B]")
        assert params == ["a", "b"]
        assert returns == "tuple[A, B]"

    def test_c_style_types_yield_the_name_only(self):
        params, _ = parse_signature("(int n, float a, float *x)")
        assert params == ["n", "a", "x"]

    def test_cpp_references_and_varargs(self):
        params, returns = parse_signature("(OutputIt out, const S& fmt, Args&&... args) -> OutputIt")
        assert params == ["out", "fmt", "args"]
        assert returns == "OutputIt"

    def test_self_is_dropped(self):
        params, _ = parse_signature("(self, path)")
        assert params == ["path"]

    @pytest.mark.parametrize("returns", ["None", "void"])
    def test_a_null_return_is_not_mentioned(self, returns):
        _, parsed = parse_signature(f"(a) -> {returns}")
        assert parsed is None

    def test_no_signature(self):
        assert parse_signature(None) == ([], None)


class TestNameSignatureDescriber:
    @pytest.fixture
    def describer(self) -> NameSignatureDescriber:
        return NameSignatureDescriber()

    def test_restates_name_and_signature(self, describer):
        text = describer.describe(
            name="stratified_split",
            signature="(df, label_col, test_size=0.2) -> tuple",
            language="python",
            code="",
        )
        assert text == "Stratified split. Takes df, label_col, test_size; returns tuple."

    def test_claims_nothing_beyond_the_declaration(self, describer):
        """A plausible invented sentence is worse than a thin true one."""
        text = describer.describe(
            name="process", signature="(data)", language="python",
            code="def process(data):\n    return data * 2\n",
        )
        # Nothing about doubling, which is in the body but not the declaration.
        assert "double" not in text.lower()
        assert "multipl" not in text.lower()
        assert text == "Process. Takes data."

    def test_handles_a_name_with_no_signature(self, describer):
        assert describer.describe(
            name="main_css", signature=None, language="css", code=""
        ) == "Main css."

    def test_empty_name_yields_nothing(self, describer):
        assert describer.describe(name="", signature="(a)", language="python", code="") == ""

    def test_source_is_labelled(self, describer):
        assert describer.source is DescriptionSource.NAME_AND_SIGNATURE
        assert not describer.source.is_authored


class TestLLMDescriber:
    def test_raises_without_a_client_rather_than_degrading(self):
        """A silent fallback would make the source field a lie."""
        with pytest.raises(RuntimeError, match="no client"):
            LLMDescriber().describe(name="f", signature="(a)", language="python", code="x")

    def test_uses_the_client_when_given_one(self):
        class Stub:
            def complete(self, prompt):
                assert "python" in prompt
                return "  Splits a frame by label.  "

        text = LLMDescriber(Stub()).describe(
            name="f", signature="(a)", language="python", code="def f(a): pass"
        )
        assert text == "Splits a frame by label."

    def test_source_is_labelled_generated(self):
        assert LLMDescriber().source is DescriptionSource.GENERATED


class TestDescribeRecord:
    def test_fills_a_missing_description(self):
        record = describe_record(
            {"name": "flatten", "signature": "(nested) -> list", "language": "python",
             "code": "", "description": None, "missing": ["tests"]}
        )
        assert record["description"] == "Flatten. Takes nested; returns list."
        assert record["description_source"] == "name_and_signature"

    def test_a_name_derived_description_stays_in_missing(self):
        """A restated identifier is a placeholder, not a description."""
        record = describe_record(
            {"name": "flatten", "signature": "(nested)", "language": "python",
             "code": "", "description": None, "missing": ["tests"]}
        )
        assert "description" in record["missing"]

    def test_an_authored_description_is_never_overwritten(self):
        record = describe_record(
            {"name": "f", "signature": "()", "language": "python", "code": "",
             "description": "Splits a dataframe preserving labels.", "missing": []}
        )
        assert record["description"] == "Splits a dataframe preserving labels."
        assert record["description_source"] == "docstring"

    def test_a_generated_description_clears_the_missing_flag(self):
        class Stub:
            def complete(self, prompt):
                return "Splits a frame by label."

        record = describe_record(
            {"name": "f", "signature": "(a)", "language": "python", "code": "x",
             "description": None, "missing": ["tests", "description"]},
            LLMDescriber(Stub()),
        )
        assert record["description_source"] == "generated"
        assert "description" not in record["missing"]
        assert "tests" in record["missing"]

    def test_an_undescribable_record_records_no_source(self):
        record = describe_record(
            {"name": "", "signature": None, "language": "python", "code": "",
             "description": None, "missing": []}
        )
        assert record["description"] is None
        assert record["description_source"] is None
