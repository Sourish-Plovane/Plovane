"""One module per command category from the whiteboard command sheet.

Importing this package registers every operation in
:data:`whiteboard.registry.OPERATIONS`.
"""

from backend.whiteboard.ops import (  # noqa: F401
    agents,
    arrows,
    batch,
    blocks,
    code,
    endpoints,
    english,
    health,
    history,
    layers,
    library,
    placement,
    project,
    style,
    templates,
    view,
)
