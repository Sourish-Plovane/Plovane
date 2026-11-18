"""The board: layers, blocks, arrows, endpoints, presets, templates and agents.

Plain data with a JSON round trip. Nothing here touches the project's files;
operations in ``whiteboard/ops`` do that through the engine, which records
every change so it can be undone.

Coordinates follow the command sheet: 1 unit is 1 pixel at 100% zoom, (0,0)
is the top-left of each layer, x grows right and y grows down, and a block's
position is relative to its parent block when it has one.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.whiteboard.registry import OpError

BOARD_VERSION = 1

LAYER_FOR_KIND = {
    "agent": 0,
    "service": 1, "server": 1, "external": 1, "database": 1, "queue": 1, "endpoint": 1,
    "folder": 2, "group": 2, "slot": 2,
    "file": 3,
    "function": 4, "class": 4, "step": 4,
    "code": 5,
}
"""The default layer each kind of block lives on (sheet: Default Layers)."""

BLOCK_KINDS = frozenset(LAYER_FOR_KIND) | {"note", "scan"}

DEFAULT_SIZE = {
    "service": (200, 100), "server": (200, 100), "external": (180, 90), "database": (140, 100),
    "queue": (160, 80), "folder": (220, 140), "group": (240, 160), "file": (160, 60),
    "function": (180, 50), "class": (240, 80), "step": (260, 40), "agent": (120, 60), "slot": (160, 60),
    "note": (160, 80), "scan": (240, 160), "endpoint": (120, 24), "code": (400, 300),
}

ARROW_TYPES = ("api", "import", "database", "event", "external", "calls")

DEFAULT_LAYERS = (
    (0, "Agents and access", 0, 10, ["agent"]),
    (1, "Services and endpoints", 10, 20, ["service", "server", "external", "database", "queue", "endpoint"]),
    (2, "Folders", 20, 40, ["folder", "group", "slot"]),
    (3, "Files", 40, 70, ["file"]),
    (4, "English steps", 70, 90, ["function", "step"]),
    (5, "Code", 90, 100, ["code"]),
)

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

BLOCK_STYLE = {
    "fill": "hex", "border": "hex", "border_width": "number",
    "border_style": ("solid", "dashed", "dotted"), "radius": "number",
    "shape": ("rectangle", "rounded", "pill", "circle", "hexagon", "cylinder"),
    "shadow": ("none", "soft", "strong"), "icon": "text", "label_size": "number",
    "opacity": "unit",
}
"""Block style properties and what each accepts (sheet: Style Properties)."""

ARROW_STYLE = {
    "curve": ("straight", "smooth", "orthogonal"), "curvature": "unit", "width": "number",
    "dash": "number", "head": ("arrow", "open", "dot", "none"), "color": "hex", "label": "text",
}

ARROW_TYPE_STYLE = {
    "api": {"color": "#1A73E8", "width": 2, "dash": 0, "head": "arrow", "curve": "smooth"},
    "import": {"color": "#5F6368", "width": 1, "dash": 0, "head": "open", "curve": "straight"},
    "database": {"color": "#188038", "width": 2, "dash": 0, "head": "arrow", "curve": "orthogonal"},
    "event": {"color": "#E37400", "width": 2, "dash": 6, "head": "arrow", "curve": "smooth"},
    "external": {"color": "#9334E6", "width": 2, "dash": 4, "head": "arrow", "curve": "smooth"},
    "calls": {"color": "#3C4043", "width": 1, "dash": 0, "head": "arrow", "curve": "smooth"},
}
"""Each arrow type's default look, so arrows read correctly with no flags."""

METHOD_COLOR = {"GET": "#188038", "POST": "#1A73E8", "PUT": "#E37400", "PATCH": "#9334E6", "DELETE": "#D93025"}

DEFAULT_PRESETS = {
    "service": {"fill": "#E8F0FE", "border": "#1A73E8", "radius": 12, "icon": "server"},
    "database": {"fill": "#E6F4EA", "border": "#188038", "shape": "cylinder", "icon": "database"},
    "external": {"fill": "#F3E8FD", "border": "#9334E6", "radius": 12, "border_style": "dashed", "icon": "cloud"},
    "folder": {"fill": "#FEF7E0", "border": "#F9AB00", "radius": 8, "icon": "folder"},
    "file": {"fill": "#FFFFFF", "border": "#5F6368", "radius": 4, "icon": "file"},
    "step": {"fill": "#F1F3F4", "border": "#DADCE0", "radius": 6},
    "agent": {"fill": "#FCE8E6", "border": "#D93025", "shape": "pill", "icon": "robot"},
    "slot": {"fill": "#FFFFFF", "border": "#9AA0A6", "border_style": "dashed", "radius": 8},
    "scan": {"fill": "#FCE8E6", "border": "#D93025", "border_style": "dashed", "opacity": 0.3},
}

THEMES = ("light", "dark", "high-contrast")


# Input: text (str)
# Output: str
# Processing: A lowercase, hyphenated id fragment from any text.
# Changed by: Sourish
# Date: 2026-10-22
def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "block"


# Input: style (dict[str, Any]), schema (dict[str, Any])
# Output: dict[str, Any]
# Processing: Validate and normalise style properties against a schema.
# Changed by: Sourish
# Date: 2026-10-22
def validate_style(style: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Raise ``OpError`` naming the first bad property; return typed values."""
    clean: dict[str, Any] = {}
    for raw_key, value in style.items():
        key = raw_key.replace("-", "_")
        if key not in schema:
            raise OpError("INVALID_STYLE", f"unknown style property {raw_key!r}; allowed: {sorted(schema)}")
        rule = schema[key]
        if rule == "hex":
            if not isinstance(value, str) or not HEX.match(value):
                raise OpError("INVALID_STYLE", f"{raw_key} must be a hex colour like #1A73E8")
            clean[key] = value.upper()
        elif rule in ("number", "unit"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise OpError("INVALID_STYLE", f"{raw_key} must be a number") from None
            if number < 0 or (rule == "unit" and number > 1):
                raise OpError("INVALID_STYLE", f"{raw_key} must be {'0 to 1' if rule == 'unit' else '0 or more'}")
            clean[key] = int(number) if number.is_integer() and rule == "number" else number
        elif rule == "text":
            clean[key] = str(value)
        elif str(value) not in rule:
            raise OpError("INVALID_STYLE", f"{raw_key} must be one of {list(rule)}")
        else:
            clean[key] = str(value)
    return clean


@dataclass
class Layer:
    number: int
    name: str
    zoom_min: float
    zoom_max: float
    shows: list[str] = field(default_factory=list)


@dataclass
class Block:
    id: str
    kind: str
    name: str
    layer: int
    x: float = 0
    y: float = 0
    w: float = 160
    h: float = 80
    parent: str | None = None
    path: str | None = None
    """Project path a folder or file block stands for; Git is the truth."""
    symbol: str | None = None
    """Function name, for function and step blocks inside a file."""
    style: dict[str, Any] = field(default_factory=dict)
    preset: str | None = None
    status: str = "built"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Arrow:
    id: str
    source: str
    target: str
    type: str = "calls"
    endpoint: str | None = None
    style: dict[str, Any] = field(default_factory=dict)
    status: str = "built"
    """``built`` (solid), ``planned`` (dashed) or ``failing`` (red)."""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Endpoint:
    id: str
    """``<service block id>:<METHOD> <path>``, e.g. ``payments:POST /charge``."""
    service: str
    method: str
    path: str
    request: Any = None
    response: Any = None
    handler: str | None = None
    status: str = "planned"
    """``built``, ``planned``, ``mocked``, ``failing`` or ``external``."""
    mock: Any = None
    last_test: dict[str, Any] | None = None


@dataclass
class Agent:
    name: str
    block: str
    grants: list[dict[str, str]] = field(default_factory=list)
    regions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Template:
    name: str
    description: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    arrows: list[dict[str, Any]] = field(default_factory=list)
    builtin: bool = False


@dataclass
class Board:
    version: int = BOARD_VERSION
    layers: dict[int, Layer] = field(default_factory=dict)
    blocks: dict[str, Block] = field(default_factory=dict)
    arrows: dict[str, Arrow] = field(default_factory=dict)
    endpoints: dict[str, Endpoint] = field(default_factory=dict)
    presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    templates: dict[str, Template] = field(default_factory=dict)
    agents: dict[str, Agent] = field(default_factory=dict)
    view: dict[str, Any] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    compiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- Serialisation ---------------------------------------------------------

    # Input: none
    # Output: dict[str, Any]
    # Processing: The board as JSON-ready data.
    # Changed by: Sourish
    # Date: 2026-10-22
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["layers"] = {str(k): v for k, v in data["layers"].items()}
        return data

    # Input: data (dict[str, Any])
    # Output: Board
    # Processing: Rebuild a board from its JSON data.
    # Changed by: Sourish
    # Date: 2026-10-22
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Board:
        return cls(
            version=data.get("version", BOARD_VERSION),
            layers={int(k): Layer(**v) for k, v in data.get("layers", {}).items()},
            blocks={k: Block(**v) for k, v in data.get("blocks", {}).items()},
            arrows={k: Arrow(**v) for k, v in data.get("arrows", {}).items()},
            endpoints={k: Endpoint(**v) for k, v in data.get("endpoints", {}).items()},
            presets=dict(data.get("presets", {})),
            templates={k: Template(**v) for k, v in data.get("templates", {}).items()},
            agents={k: Agent(**v) for k, v in data.get("agents", {}).items()},
            view=dict(data.get("view", {})),
            alerts=list(data.get("alerts", [])),
            compiles=dict(data.get("compiles", {})),
        )

    # -- Lookup ----------------------------------------------------------------

    # Input: ref (str | Block)
    # Output: Block
    # Processing: Resolve a block by id, then name, then project path.
    # Changed by: Sourish
    # Date: 2026-10-22
    def block(self, ref: str | Block) -> Block:
        """Raise ``OpError`` NOT_FOUND, or AMBIGUOUS listing the candidates."""
        if isinstance(ref, Block):
            return ref
        ref = str(ref).strip()
        if ref in self.blocks:
            return self.blocks[ref]
        for key in ("name", "path"):
            normalised = ref.lower().rstrip("/") if key == "path" else ref.lower()
            found = [
                b for b in self.blocks.values()
                if (getattr(b, key) or "").lower().rstrip("/") == normalised
            ]
            if len(found) == 1:
                return found[0]
            if len(found) > 1:
                raise OpError("AMBIGUOUS", f"{ref!r} matches {[b.id for b in found]}; use an id")
        raise OpError("NOT_FOUND", f"no block {ref!r}")

    # Input: path (str)
    # Output: Block | None
    # Processing: The block standing for a project path, if any.
    # Changed by: Sourish
    # Date: 2026-10-22
    def block_for_path(self, path: str) -> Block | None:
        wanted = path.strip("/")
        for block in self.blocks.values():
            if block.path and block.path.strip("/") == wanted and block.kind in ("file", "folder"):
                return block
        return None

    # Input: base (str)
    # Output: str
    # Processing: A block id not yet used, derived from base.
    # Changed by: Sourish
    # Date: 2026-10-22
    def new_id(self, base: str, *, pool: dict | None = None) -> str:
        taken = self.blocks if pool is None else pool
        candidate, n = slug(base), 2
        while candidate in taken:
            candidate, n = f"{slug(base)}-{n}", n + 1
        return candidate

    # Input: block (Block)
    # Output: tuple[float, float]
    # Processing: A block's absolute position on its layer.
    # Changed by: Sourish
    # Date: 2026-10-22
    def absolute(self, block: Block) -> tuple[float, float]:
        x, y, seen = block.x, block.y, {block.id}
        parent = block.parent
        while parent and parent in self.blocks and parent not in seen:
            seen.add(parent)
            x, y = x + self.blocks[parent].x, y + self.blocks[parent].y
            parent = self.blocks[parent].parent
        return x, y

    # Input: block_id (str)
    # Output: list[Block]
    # Processing: Every block nested under block_id, at any depth.
    # Changed by: Sourish
    # Date: 2026-10-22
    def descendants(self, block_id: str) -> list[Block]:
        found, frontier = [], [block_id]
        while frontier:
            current = frontier.pop()
            for block in self.blocks.values():
                if block.parent == current:
                    found.append(block)
                    frontier.append(block.id)
        return found

    # Input: block_id (str)
    # Output: list[Arrow]
    # Processing: Arrows that start or end at block_id.
    # Changed by: Sourish
    # Date: 2026-10-22
    def arrows_of(self, block_id: str) -> list[Arrow]:
        return [a for a in self.arrows.values() if block_id in (a.source, a.target)]

    # Input: block (Block)
    # Output: dict[str, Any]
    # Processing: A block's style with its preset underneath its own flags.
    # Changed by: Sourish
    # Date: 2026-10-22
    def resolved_style(self, block: Block) -> dict[str, Any]:
        base = dict(self.presets.get(block.preset or "", {}))
        return base | block.style


# Input: none
# Output: Board
# Processing: A new board with the default layers, presets and view.
# Changed by: Sourish
# Date: 2026-10-22
def default_board() -> Board:
    board = Board(
        layers={n: Layer(n, name, lo, hi, list(shows)) for n, name, lo, hi, shows in DEFAULT_LAYERS},
        presets={k: dict(v) for k, v in DEFAULT_PRESETS.items()},
        view={"zoom": 15, "layer": 1, "grid": {"size": 20, "snap": True}, "theme": "light",
              "focus": None, "highlight": [], "filter": {}, "counters": {}},
    )
    from backend.whiteboard.ops.templates import builtin_templates

    board.templates = {t.name: t for t in builtin_templates()}
    return board
