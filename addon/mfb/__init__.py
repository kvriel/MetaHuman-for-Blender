"""MetaHuman for Blender.

Brings the MetaHuman workflow to Blender: Character Assembler, Expression
Editor, Pose Editor and Groom Exporter, on our own DNA reader/writer and RigLogic.
"""

_MODULES = ("menu", "ui", "rig", "pose_ui", "expression_ui", "export_ue")


def _blender_modules():
    import importlib
    mods = []
    for name in _MODULES:
        try:
            mods.append(importlib.import_module(f".blender.{name}", __package__))
        except ModuleNotFoundError as exc:      # a tool not built yet
            if not exc.name or not exc.name.endswith(name):
                raise
    try:
        mods.append(importlib.import_module(".groom", __package__))
    except ModuleNotFoundError as exc:
        if not exc.name or not exc.name.endswith("groom"):
            raise
    return mods


def register():
    for mod in _blender_modules():
        mod.register()


def unregister():
    for mod in reversed(_blender_modules()):
        mod.unregister()
