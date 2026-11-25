"""Groom Exporter: Blender hair (Curves objects) to an Alembic groom for Unreal Engine.

Entry points for the add-on: `from .groom import register as register_groom, unregister as unregister_groom`.
"""


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
