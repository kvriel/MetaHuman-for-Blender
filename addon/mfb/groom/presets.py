"""Groom export presets: per-groom settings and export options as JSON."""
from __future__ import annotations

import json
from pathlib import Path

PRESET_VERSION = 1
OPTION_KEYS = ("filepath", "up_axis", "unit_scale", "export_root_uv", "export_color", "export_ids",
               "default_radius_mm")


def settings_to_dict(settings) -> dict:
    return {
        "version": PRESET_VERSION,
        "options": {k: getattr(settings, k) for k in OPTION_KEYS},
        "grooms": [
            {"object": it.obj.name if it.obj else it.name, "include": it.include, "group_id": it.group_id,
             "group_name": it.group_name, "guides": it.guides}
            for it in settings.items
        ],
    }


def save_preset(settings, path: str) -> None:
    Path(path).write_text(json.dumps(settings_to_dict(settings), indent=2), encoding="utf-8")


def load_preset(settings, path: str, objects) -> tuple[int, list[str]]:
    """Apply a preset. Grooms are matched to scene objects by name. Returns (matched, missing names)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for k, v in data.get("options", {}).items():
        if k in OPTION_KEYS and hasattr(settings, k):
            setattr(settings, k, v)
    by_name = {it.obj.name if it.obj else it.name: it for it in settings.items}
    matched, missing = 0, []
    for g in data.get("grooms", []):
        name = g.get("object", "")
        item = by_name.get(name)
        if item is None:
            obj = objects.get(name)
            if obj is None or obj.type != "CURVES":
                missing.append(name)
                continue
            item = settings.items.add()
            item.obj, item.name = obj, obj.name
        item.include = bool(g.get("include", True))
        item.group_id = int(g.get("group_id", 0))
        item.group_name = str(g.get("group_name", ""))
        item.guides = bool(g.get("guides", False))
        matched += 1
    return matched, missing
