"""Context helpers so operators also work when called from scripts, timers or handlers.

Clicking a button gives Blender operators a full context (window, area, active object).
Code running from a timer or another script often has none, and operators such as
object.mode_set or wm.alembic_export then refuse to run. These helpers supply it.
"""
from __future__ import annotations

import bpy


def window_override() -> dict:
    """The current window, screen, area and region, or the first window's 3D view."""
    ctx = bpy.context
    wm = ctx.window_manager
    win = ctx.window or (wm.windows[0] if wm and len(wm.windows) else None)
    kw: dict = {}
    if win is None:
        return kw
    kw["window"] = win
    kw["screen"] = win.screen
    area = ctx.area if ctx.area and ctx.window == win else next(
        (a for a in win.screen.areas if a.type == "VIEW_3D"), None)
    if area is not None:
        kw["area"] = area
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is not None:
            kw["region"] = region
    return kw


def set_mode(obj: bpy.types.Object, mode: str) -> None:
    """object.mode_set on `obj`, whatever the calling context."""
    kw = window_override()
    kw.update(active_object=obj, object=obj, view_layer=bpy.context.view_layer)
    with bpy.context.temp_override(**kw):
        bpy.ops.object.mode_set(mode=mode)
