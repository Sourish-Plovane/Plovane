"""Guess the §9.1 ``language`` for a file from its path.

The handbook requires the field but does not define the vocabulary. We pick
the names the recommender already uses ("python", "javascript", "typescript",
"cpp") and fall back to a lowercased extension for everything else so the
downstream diagram generator can still bucket files.
"""

from __future__ import annotations

import os


# Input: path (str)
# Output: str
# Processing: Return the §9.1 ``language`` string for a file path.
# Changed by: Nirmal
# Date: 2026-10-31
def detect_language(path: str) -> str:
    name = os.path.basename(path).lower()
    if "." not in name:
        return "text"
    extension = name.rsplit(".", 1)[1]
    return {
        "py": "python",
        "js": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "cpp": "cpp",
        "cc": "cpp",
        "cxx": "cpp",
        "c": "c",
        "h": "c",
        "hpp": "cpp",
        "go": "go",
        "rs": "rust",
        "java": "java",
        "rb": "ruby",
        "md": "markdown",
        "json": "json",
        "yml": "yaml",
        "yaml": "yaml",
        "toml": "toml",
    }.get(extension, extension)
