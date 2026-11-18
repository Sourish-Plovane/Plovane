"""ml.ingest.find_helpers: which functions in one file stand alone."""

from __future__ import annotations

import json

from ml.ingest.find_helpers import find_helpers, main

SOURCE = '''import re
import json
from pathlib import Path

EMAIL = re.compile(r"^[^@]+@[^@]+$")


def is_valid_email(address: str) -> bool:
    """Return True if address looks like an email address."""
    return bool(EMAIL.match(address))


def slugify(text: str) -> str:
    """Turn a title into a URL-safe slug."""
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


def load_json(path: str) -> dict:
    """Read a JSON file into a dict."""
    return json.loads(Path(path).read_text())


def tiny(x):
    return x


def main():
    print(slugify("A B"))
    return load_json("c.json")
'''


def names(report, key):
    return [h["name"] for h in report[key]]


class TestFindHelpers:
    def test_a_function_needing_only_imports_is_a_helper(self):
        report = find_helpers(SOURCE, "app.py")
        assert names(report, "helpers") == ["slugify", "load_json"]

    def test_a_helper_carries_its_imports_in_pasteable_code(self):
        helper = find_helpers(SOURCE, "app.py")["helpers"][1]
        assert helper["imports"] == ["from pathlib import Path", "import json"]
        assert helper["code"].startswith("from pathlib import Path\nimport json")

    def test_a_function_leaning_on_the_module_is_a_near_helper_saying_what(self):
        near = {h["name"]: h["needs"] for h in find_helpers(SOURCE, "app.py")["near_helpers"]}
        assert near["is_valid_email"] == ["EMAIL"]
        assert set(near["main"]) == {"slugify", "load_json"}

    def test_the_library_size_rule_applies(self):
        report = find_helpers(SOURCE, "app.py")
        everything = names(report, "helpers") + names(report, "near_helpers")
        assert "tiny" not in everything

    def test_overload_stubs_report_one_function(self):
        source = '''from typing import overload


@overload
def double(x: int) -> int:
    ...


@overload
def double(x: str) -> str:
    ...


def double(x):
    """Double a number or repeat a string."""
    return x * 2
'''
        report = find_helpers(source, "d.py")
        assert names(report, "helpers") == ["double"]
        assert report["helpers"][0]["start_line"] == 14

    def test_javascript_is_read_too(self):
        source = """export default function combineURLs(baseURL, relativeURL) {
  return relativeURL
    ? baseURL.replace(/\\/+$/, '') + '/' + relativeURL.replace(/^\\/+/, '')
    : baseURL;
}
"""
        report = find_helpers(source, "combineURLs.js")
        assert report["language"] == "javascript"
        assert names(report, "helpers") == ["combineURLs"]

    def test_an_unsupported_file_says_so(self, tmp_path, capsys):
        path = tmp_path / "notes.txt"
        path.write_text("hello")
        assert main([str(path)]) == 1
        assert json.loads(capsys.readouterr().out)["error"] == "unsupported file type"
