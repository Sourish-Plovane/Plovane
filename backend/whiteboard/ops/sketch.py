"""Sketch: the short command language an AI writes, one plain line per change.

    service orders, payments
    database postgres
    queue order-events
    external stripe
    orders -> payments "POST /charge"
    orders, payments -> postgres
    rename orders "order service"
    unlink orders -> postgres
    remove drivers

Each line is one small function in :data:`VERBS`. They only draw: no files
are written and no endpoints generated, so nothing appears on other layers.
Names are matched to blocks that already exist, so a repeated line changes
nothing instead of adding ``orders-2``, and an arrow to a name that does not
exist is refused (with the closest name) instead of making a stray block.
After any change the services layer is laid out again in clean columns.
"""

from __future__ import annotations

import difflib
import re
import shlex
from typing import Any

from backend.whiteboard.engine import Context
from backend.whiteboard.model import Arrow, Block, slug
from backend.whiteboard.ops.arrows import arrow_record, new_arrow
from backend.whiteboard.registry import OpError, operation

KINDS = ("service", "database", "queue", "external", "server")
LAYER = 1
COLUMN, ROW, CELL_W, MARGIN = 300, 150, 200, 80
"""Layout grid: column pitch, row pitch, the width a block is centred in, and the top-left margin."""

HELP = ("lines look like: service orders | database postgres | queue events | external stripe | "
        'a -> b "label" | unlink a -> b | rename a "new name" | remove a | tidy | show')


# Input: ctx (Context), name (str)
# Output: Block | None
# Processing: The services-layer block a sketch name means, or None.
# Changed by: Claude
# Date: 2026-11-10
def find(ctx: Context, name: str) -> Block | None:
    wanted, key = name.strip().lower(), slug(name)
    for block in ctx.board.blocks.values():
        if block.layer == LAYER and (block.id == key or block.name.lower() == wanted):
            return block
    return None


# Input: ctx (Context), name (str)
# Output: Block
# Processing: The block a name means; refuse with the closest names instead of inventing one.
# Changed by: Claude
# Date: 2026-11-10
def need(ctx: Context, name: str) -> Block:
    block = find(ctx, name)
    if block is None:
        names = sorted(b.name for b in ctx.board.blocks.values() if b.layer == LAYER)
        close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
        hint = f"; did you mean {', '.join(close)}?" if close else f"; add it first, e.g. 'service {name}'"
        raise OpError("NOT_FOUND", f"no block {name!r}{hint}")
    return block


# Input: text (str)
# Output: list[str]
# Processing: Comma-separated names, quotes allowed.
# Changed by: Claude
# Date: 2026-11-10
def names(text: str) -> list[str]:
    return [n.strip().strip("\"'") for n in text.split(",") if n.strip().strip("\"'")]


# Input: source (Block), target (Block)
# Output: str
# Processing: The arrow type a link gets from what it points at.
# Changed by: Claude
# Date: 2026-11-10
def arrow_type(source: Block, target: Block) -> str:
    if target.kind == "database":
        return "database"
    if "queue" in (source.kind, target.kind):
        return "event"
    if target.kind == "external":
        return "external"
    return "calls"


# Input: ctx (Context), kind (str), rest (str)
# Output: list[str]
# Processing: Add blocks of one kind, skipping names that already exist.
# Changed by: Claude
# Date: 2026-11-10
def add(ctx: Context, kind: str, rest: str) -> list[str]:
    done = []
    for name in names(rest):
        existing = find(ctx, name)
        if existing:
            if existing.kind != kind:
                raise OpError("CONFLICT", f"{existing.name!r} already exists as a {existing.kind}")
            continue
        ctx.require(None)
        block = ctx.new_block(kind, name, layer=LAYER)
        block.meta["sketch"] = True
        done.append(f"added {kind} {block.id}")
    return done


# Input: ctx (Context), rest (str)
# Output: list[str]
# Processing: Draw arrows along "a, b -> c -> d "label"", skipping ones that exist.
# Changed by: Claude
# Date: 2026-11-10
def link(ctx: Context, rest: str) -> list[str]:
    label = None
    quoted = re.search(r"\s+[\"']([^\"']+)[\"']\s*$", rest)
    if quoted and "->" not in quoted.group(1):
        label, rest = quoted.group(1), rest[: quoted.start()]
    stages = [[need(ctx, n) for n in names(part)] for part in rest.split("->")]
    if len(stages) < 2 or not all(stages):
        raise OpError("INVALID_ARGS", f"an arrow looks like 'a -> b'; {HELP}")
    done = []
    for sources, targets in zip(stages, stages[1:]):
        for a in sources:
            for b in targets:
                if a.id == b.id:
                    raise OpError("INVALID_ARGS", f"{a.id} cannot point at itself")
                ctx.require(a)
                existing = [x for x in ctx.board.arrows.values() if x.source == a.id and x.target == b.id]
                if existing:
                    if label and existing[0].style.get("label") != label:
                        existing[0].style["label"] = label
                        done.append(f"labelled {a.id} -> {b.id}")
                    continue
                arrow = new_arrow(ctx, a, b, arrow_type(a, b))
                arrow.meta["sketch"] = True
                if label:
                    arrow.style["label"] = label
                done.append(f"linked {a.id} -> {b.id}")
    return done


# Input: ctx (Context), rest (str)
# Output: list[str]
# Processing: Remove the arrows along "a -> b".
# Changed by: Claude
# Date: 2026-11-10
def unlink(ctx: Context, rest: str) -> list[str]:
    stages = [[need(ctx, n) for n in names(part)] for part in rest.split("->")]
    if len(stages) != 2:
        raise OpError("INVALID_ARGS", "unlink looks like 'unlink a -> b'")
    done = []
    for a in stages[0]:
        for b in stages[1]:
            for arrow in [x for x in ctx.board.arrows.values() if x.source == a.id and x.target == b.id]:
                guard(arrow)
                ctx.require(a)
                del ctx.board.arrows[arrow.id]
                done.append(f"unlinked {a.id} -> {b.id}")
    return done


# Input: arrow (Arrow)
# Output: None
# Processing: Refuse to drop an arrow that stands for code.
# Changed by: Claude
# Date: 2026-11-10
def guard(arrow: Arrow) -> None:
    if arrow.type == "import" or arrow.endpoint:
        raise OpError("INVALID_ARGS", f"{arrow.id} is backed by code; remove it with /disconnect")


# Input: ctx (Context), rest (str)
# Output: list[str]
# Processing: Remove drawn blocks and their arrows; code-backed blocks are refused.
# Changed by: Claude
# Date: 2026-11-10
def remove(ctx: Context, rest: str) -> list[str]:
    done = []
    for name in names(rest):
        block = need(ctx, name)
        if block.path or any(e.service == block.id for e in ctx.board.endpoints.values()) or \
                any(b.parent == block.id for b in ctx.board.blocks.values()):
            raise OpError("INVALID_ARGS", f"{block.id} has code, endpoints or children; use /block delete")
        touching = [a for a in ctx.board.arrows.values() if block.id in (a.source, a.target)]
        for arrow in touching:
            guard(arrow)
        ctx.require(block)
        for arrow in touching:
            del ctx.board.arrows[arrow.id]
        del ctx.board.blocks[block.id]
        done.append(f"removed {block.id}")
    return done


# Input: ctx (Context), rest (str)
# Output: list[str]
# Processing: Give a block a new display name; its id stays so arrows keep pointing at it.
# Changed by: Claude
# Date: 2026-11-10
def rename(ctx: Context, rest: str) -> list[str]:
    try:
        words = shlex.split(rest)
    except ValueError:
        words = []
    if len(words) != 2:
        raise OpError("INVALID_ARGS", "rename looks like: rename orders \"order service\"")
    block = need(ctx, words[0])
    if find(ctx, words[1]) not in (None, block):
        raise OpError("CONFLICT", f"a block called {words[1]!r} already exists")
    ctx.require(block)
    block.name = words[1]
    return [f"renamed {block.id} to {words[1]!r}"]


# Input: ctx (Context)
# Output: list[str]
# Processing: Lay the services layer out in columns along the arrows, rows ordered to cut crossings.
# Changed by: Claude
# Date: 2026-11-10
def tidy(ctx: Context) -> list[str]:
    blocks = {b.id: b for b in ctx.board.blocks.values()
              if b.layer == LAYER and b.parent is None and b.kind in KINDS}
    if not blocks:
        return []
    edges = [(a.source, a.target) for a in ctx.board.arrows.values() if a.source in blocks and a.target in blocks]
    rank = dict.fromkeys(blocks, 0)
    for _ in range(len(blocks)):  # longest path from a block nothing points at; a cycle stops growing
        changed = False
        for source, target in edges:
            if rank[target] < rank[source] + 1 < len(blocks):
                rank[target], changed = rank[source] + 1, True
        if not changed:
            break
    columns: dict[int, list[str]] = {}
    for block_id in sorted(blocks, key=lambda b: (blocks[b].y, blocks[b].x)):
        columns.setdefault(rank[block_id], []).append(block_id)
    row: dict[str, float] = {}
    for column in sorted(columns):  # order each column by the average row of what points into it
        members = columns[column]

        place = {b: i for i, b in enumerate(members)}

        # Input: block_id (str), place (dict[str, int])
        # Output: float
        # Processing: The average row of the blocks pointing into this one, else its current place.
        # Changed by: Claude
        # Date: 2026-11-10
        def weight(block_id: str, place: dict[str, int] = place) -> float:
            feeders = [row[s] for s, t in edges if t == block_id and s in row]
            return sum(feeders) / len(feeders) if feeders else place[block_id]
        members.sort(key=weight)
        row.update({b: i for i, b in enumerate(members)})
    tallest = max(len(m) for m in columns.values())
    for column, members in columns.items():
        top = MARGIN + (tallest - len(members)) * ROW / 2
        for index, block_id in enumerate(members):
            block = blocks[block_id]
            block.x = MARGIN + column * COLUMN + (CELL_W - block.w) / 2
            block.y = top + index * ROW + (ROW - 50 - block.h) / 2
            ctx.require(block)
    return [f"tidied {len(blocks)} blocks"]


VERBS = {
    **{kind: (lambda kind: lambda ctx, rest: add(ctx, kind, rest))(kind) for kind in KINDS},
    "db": lambda ctx, rest: add(ctx, "database", rest),
    "link": link, "unlink": unlink, "remove": remove, "rename": rename,
    "tidy": lambda ctx, rest: tidy(ctx),
    "show": lambda ctx, rest: [],
}


# Input: script (Any)
# Output: list[str]
# Processing: The non-empty, non-comment lines of a sketch, from text or a list.
# Changed by: Claude
# Date: 2026-11-10
def lines_of(script: Any) -> list[str]:
    text = "\n".join(map(str, script)) if isinstance(script, list) else str(script or "")
    return [line.strip() for line in re.split(r"[\n;]", text) if line.strip() and not line.strip().startswith("#")]


# Input: ctx (Context), script (Any)
# Output: dict[str, Any]
# Processing: Run sketch lines as one change, then tidy the layout.
# Changed by: Claude
# Date: 2026-11-10
@operation("sketch", command="/sketch", tool="plovane_sketch", group="plovane_sketch")
def sketch(ctx: Context, script: Any) -> dict[str, Any]:
    """Draws with short lines (service orders | a -> b "label" | remove a); all apply or none do."""
    lines = lines_of(script)
    if not lines:
        raise OpError("INVALID_ARGS", f"nothing to draw; {HELP}")
    done: list[str] = []
    for number, line in enumerate(lines, start=1):
        verb, _, rest = line.partition(" ")
        verb = verb.lower()
        try:
            if verb in VERBS:
                done += VERBS[verb](ctx, rest.strip())
            elif "->" in line:
                done += link(ctx, line)
            else:
                raise OpError("UNKNOWN_COMMAND", f"don't know {verb!r}; {HELP}")
        except OpError as exc:
            raise OpError(exc.code, f"line {number} ({line}): {exc.message} (nothing was changed)") from None
    if done and not all(d.startswith("tidied") for d in done):
        done += tidy(ctx)
    blocks = sorted(b.id for b in ctx.board.blocks.values() if b.layer == LAYER and b.kind in KINDS)
    arrows = [arrow_record(a) for a in ctx.board.arrows.values() if a.source in blocks and a.target in blocks]
    return {"changes": done or ["nothing to change"], "blocks": blocks,
            "arrows": [f"{a['from']} -> {a['to']}" + (f" ({a['style']['label']})" if a["style"].get("label") else "")
                       for a in arrows]}
