"""Shared test helper: reset the scene, in background runs and in a visible Blender window."""
import bpy


def reset_scene() -> None:
    """Background: load factory settings. Visible window: clear the data in place, so the
    window, its screen and the test runner's timer stay valid."""
    if bpy.app.background:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        return
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.cameras,
                 bpy.data.lights, bpy.data.curves, bpy.data.hair_curves, bpy.data.actions, bpy.data.images,
                 bpy.data.node_groups):
        for block in list(coll):
            coll.remove(block)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for t in list(bpy.data.texts):
        if t.name != "MFB Test Results":
            bpy.data.texts.remove(t)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end, scene.frame_current = 1, 250, 1
    for prop in ("mfb_assembler", "mfb_pose", "mfb_expr", "mfb_groom"):
        if hasattr(scene, prop):
            try:
                scene.property_unset(prop)
            except Exception:
                pass
