"""One module per command category from the whiteboard command sheet.

Importing this package registers every operation in
:data:`whiteboard.registry.OPERATIONS`.
"""

from backend.whiteboard.ops import (  # noqa: F401
    agents,
    arrows,
    batch,
    blocks,
    boardfile,
    code,
    endpoints,
    english,
    health,
    history,
    layers,
    legibility,
    library,
    live,
    logs,
    outline,
    overlay_propose,
    overlays,
    placement,
    sketch,
    project,
    readable,
    routes,
    style,
    outside_apis,
    templates,
    trace,
    view,
    views,
)
