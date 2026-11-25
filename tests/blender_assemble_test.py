"""Headless test of the Character Assembler operator.

blender -b --factory-startup --python tests/blender_assemble_test.py -- <folder> <out_prefix> [split]
"""
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import assemble as asm, rig  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
folder, prefix, split = args[0], args[1], "split" in args[2:]

reset_scene()
mfb.register()
s = bpy.context.scene.mfb_assembler
scan = asm.scan_folder(folder)
print("scan:", scan.head, "|", scan.body, "| textures:", scan.textures or "-")
s.head_dna, s.body_dna, s.name = scan.head, scan.body, "Archetype"
s.lods[1] = True
s.combine_skeletons = not split
bpy.ops.mfb.refresh_meshes()
print("mesh list:", len(s.meshes), "items, e.g.", [m.name for m in s.meshes][:4])
t = time.perf_counter()
print("assemble:", bpy.ops.mfb.assemble(), f"{time.perf_counter() - t:.1f}s")

root = bpy.data.collections["Archetype"]
for c in root.children:
    print(f"  {c.name}: {len(c.objects)} objects, hidden={bpy.context.view_layer.layer_collection.children['Archetype'].children[c.name].hide_viewport}")
arms = [o for o in bpy.data.objects if o.type == "ARMATURE" and not o.get("mfb_gui_map")]
for a in arms:
    follows = sum(1 for pb in a.pose.bones for c in pb.constraints if c.name == "mfb_follow_body")
    print(f"  armature {a.name}: {len(a.data.bones)} bones, role={a.get('mfb_role')}, follow constraints={follows}")
print("units:", bpy.context.scene.unit_settings.length_unit)

# pose the face through the board and render the whole character
arm = next(o for o in bpy.data.objects if o.get(rig.PROP_RIG))
board = bpy.data.objects[arm[rig.PROP_BOARD_NAME]]
unit = board["mfb_board_unit"]
board.pose.bones["CTRL_L_mouth_cornerPull"].location.y = unit
board.pose.bones["CTRL_R_mouth_cornerPull"].location.y = unit
rig._on_update()
print("smile applied:", round(max(arm["mfb_wrinkle_maps"].values()), 2) > 0)

scene = bpy.context.scene
cam_data = bpy.data.cameras.new("cam"); cam_data.type = "ORTHO"
cam = bpy.data.objects.new("cam", cam_data); scene.collection.objects.link(cam); scene.camera = cam
scene.render.engine = "BLENDER_WORKBENCH"; scene.display.shading.light = "STUDIO"
for o in bpy.data.objects:
    if o.type in {"ARMATURE", "CURVE", "FONT"} or "_faceboard" in o.name or any(
            k in o.name for k in ("eyeshell", "eyeEdge", "saliva", "cartilage")):
        o.hide_render = True
views = (("body", 2.0, (0, -3, 0.9), 512, 768), ("face", 0.34, (0, -3, 1.50), 512, 512))
for view, scale, loc, w, h in views:
    cam_data.ortho_scale = scale; cam.location = loc; cam.rotation_euler = (math.radians(90), 0, 0)
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.filepath = f"{prefix}_{view}.png"
    bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=f"{prefix}.blend")
print("done")
