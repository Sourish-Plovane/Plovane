"""Project layouts seen in real repositories: a FastAPI app under backend/, a Vite frontend with "@/" imports
and a generated API client, and a README that opens with a list."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.whiteboard import board_ai, llm_agent
from backend.whiteboard.ops.readable import plain_markdown
from backend.whiteboard.ops.sketch import names
from whiteboard_helpers import project

FULL_STACK = {
    "README.md": "# Full Stack\n\n[![badge](x.svg)](y)\n\n- ⚡ [**FastAPI**](https://fastapi.tiangolo.com) for the backend API.\n- React frontend.\n\nClick the button to start.\n",
    "backend/app/__init__.py": "",
    "backend/app/core/config.py": "class Settings:\n    API_V1_STR: str = \"/api/v1\"\n\nsettings = Settings()\n",
    "backend/app/models.py": "class Item:\n    pass\n",
    "backend/app/api/__init__.py": "",
    "backend/app/api/routes/__init__.py": "",
    "backend/app/api/routes/items.py": (
        "from fastapi import APIRouter\nfrom app.models import Item\n\n"
        "router = APIRouter(prefix=\"/items\", tags=[\"items\"])\n\n"
        "@router.get(\"/\")\ndef read_items():\n    return []\n\n"
        "@router.get(\"/{item_id}\")\ndef read_item(item_id: int):\n    return Item()\n"),
    "backend/app/api/main.py": "from fastapi import APIRouter\nfrom app.api.routes import items\n\napi_router = APIRouter()\napi_router.include_router(items.router)\n",
    "backend/app/main.py": (
        "from fastapi import FastAPI\nfrom app.api.main import api_router\nfrom app.core.config import settings\n\n"
        "app = FastAPI()\napp.include_router(api_router, prefix=settings.API_V1_STR)\n"),
    "frontend/tsconfig.json": '{\n  // the "@" alias\n  "compilerOptions": {"paths": {"@/*": ["./src/*"]},},\n}\n',
    "frontend/src/lib/utils.ts": "export const cn = (...a: string[]) => a.join(' ')\n",
    "frontend/src/client/sdk.gen.ts": (
        "export class ItemsService {\n  public static readItems(options?: any) {\n"
        "    return (options?.client ?? client).get<ItemsResponses, Errors>({\n      responseType: 'json',\n"
        "      url: '/api/v1/items/',\n      ...options\n    })\n  }\n}\n"),
    "frontend/src/components/Items.tsx": "import { cn } from \"@/lib/utils\"\nexport const Items = () => cn('a')\n",
}


# Input: tmp_path (Path)
# Output: None
# Processing: Routes get the prefix the app mounts their router under, and the generated client's calls become API arrows to the route file.
# Changed by: Claude Code
# Date: 2026-11-27
def test_fastapi_mount_prefix_and_generated_client(tmp_path: Path) -> None:
    ws = project(tmp_path, FULL_STACK)
    assert ws.command("/project import", confirm=True)["ok"]
    endpoints = {(e.method, e.path) for e in ws.board.endpoints.values()}
    assert ("GET", "/api/v1/items") in endpoints and ("GET", "/api/v1/items/{item_id}") in endpoints
    api = {(ws.board.blocks[a.source].path, ws.board.blocks[a.target].path) for a in ws.board.arrows.values() if a.type == "api"}
    assert ("frontend/src/client/sdk.gen.ts", "backend/app/api/routes/items.py") in api
    again = ws.command("/endpoint scan")  # a second scan keeps the same routes, no stale copies
    assert again["ok"] and len(ws.board.endpoints) == len(endpoints)


# Input: tmp_path (Path)
# Output: None
# Processing: "from app.models import Item" inside backend/ and "@/lib/utils" in a Vite app both become import arrows.
# Changed by: Claude Code
# Date: 2026-11-27
def test_absolute_python_imports_and_path_aliases(tmp_path: Path) -> None:
    ws = project(tmp_path, FULL_STACK)
    assert ws.command("/project import", confirm=True)["ok"]
    imports = {(ws.board.blocks[a.source].path, ws.board.blocks[a.target].path) for a in ws.board.arrows.values() if a.type == "import"}
    assert ("backend/app/api/routes/items.py", "backend/app/models.py") in imports
    assert ("backend/app/main.py", "backend/app/core/config.py") in imports
    assert ("frontend/src/components/Items.tsx", "frontend/src/lib/utils.ts") in imports


# Input: tmp_path (Path)
# Output: None
# Processing: The free project line is plain words from the README's first item, not markdown and not a later instruction.
# Changed by: Claude Code
# Date: 2026-11-27
def test_readme_line_is_plain_words(tmp_path: Path) -> None:
    ws = project(tmp_path, FULL_STACK)
    assert ws.command("/project import", confirm=True)["ok"]
    assert ws.board.view["about"] == "FastAPI for the backend API."
    assert plain_markdown("- 🚀 [**React**](https://react.dev) for the `frontend`.") == "React for the frontend."


# Input: none
# Output: None
# Processing: A comma inside a quoted sketch name stays part of the name.
# Changed by: Claude Code
# Date: 2026-11-27
def test_sketch_names_keep_quoted_commas() -> None:
    assert names('"Web app (built, served by FastAPI)", api') == ["Web app (built, served by FastAPI)", "api"]


# Input: tmp_path (Path), monkeypatch (pytest.MonkeyPatch)
# Output: None
# Processing: After a build, the whiteboard AI's "About:" line replaces the line guessed from the README; a person's own line is never replaced.
# Changed by: Claude Code
# Date: 2026-11-27
def test_build_takes_the_ai_about_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(llm_agent, "complete", lambda *a, **k: ('service "API"', {"input_tokens": 1, "output_tokens": 1}))
    monkeypatch.setattr(llm_agent, "run", lambda task, **k: {"status": "done", "commands": [],
                                                                "summary": "Added the API.\n\nAbout: A **starter** web app with a React dashboard."})
    ws = project(tmp_path, FULL_STACK)
    board_ai.build(ws)
    assert ws.board.view["about"] == "A starter web app with a React dashboard."
    assert ws.command('/project describe "Our shop"')["ok"]
    board_ai.build(ws)
    assert ws.board.view["about"] == "Our shop"
