"""Plovane CLI (`plovane`): interactive REPL that talks to the local stack.

Handbook §4.1 services this CLI reaches:
    B03 local_backend  (127.0.0.1:8701)
    B05 cloud_server   (127.0.0.1:8500)
    B06 ml serving     (127.0.0.1:8600)

M1 scope is the skeleton: entry point, config load, menu loop, and a
``status`` screen that pings every service. Later milestones add
recommend, project, agent connect, and Claude/Codex login.
"""
