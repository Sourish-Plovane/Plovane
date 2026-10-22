"""Every function in the source folders carries the five-field comment block
from .claude/agents/README.md (checked by scripts/check_comments.py)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_comments import check_file  # noqa: E402

SOURCE_FOLDERS = ("ml", "ml_models", "mcp_server", "whiteboard", "scripts")


# Input: none
# Output: None
# Processing: Check that no source function lacks its comment block.
# Changed by: Claude Code
# Date: 2026-10-22
def test_source_functions_carry_the_comment_block():
    files = [f for folder in SOURCE_FOLDERS for f in sorted((ROOT / folder).rglob("*.py"))]
    problems = [p for f in files for p in check_file(f)]
    assert problems == [], "\n".join(problems)
