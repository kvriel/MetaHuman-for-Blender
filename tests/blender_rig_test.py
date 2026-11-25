"""Headless test of live RigLogic: build a head, set GUI controls, render.

blender -b --factory-startup --python tests/blender_rig_test.py -- <file.dna> <out_prefix> CTRL=value ...
"""
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
from mfb.blender import faceboard, rig  # noqa: E402
from mfb.blender.build import BuildOptions, build_armature, build_mesh  # noqa: E402
from mfb.core.space import Space  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
dna_path, prefix, sets = args[0], args[1], args[2:]

reset_scene()
scene = bpy.context.scene
dna = read_dna(dna_path)
space = Space.for_dna(dna)
root = bpy.data.collections.new("head"); scene.collection.children.link(root)
arm = build_armature(dna, space, "head_rig", root)
heads = [build_mesh(dna, space, int(i), root, arm, BuildOptions()) for i in dna.definition.lod_meshes.for_lod(0)]
arm["mfb_head_dna"] = str(dna_path)                # as the assembler does: live RigLogic runs on this DNA
t = time.perf_counter()
board = rig.setup_rig(arm, root)
print(f"face board with {len(board.pose.bones)} knobs in {time.perf_counter() - t:.2f}s")

names = dna.definition.gui_control_names
gui = faceboard.read_gui(board)
for s in sets:
    k, v = s.split("=")
    gui[names.index(k)] = float(v)
faceboard.write_gui(board, gui)
t = time.perf_counter()
rig.update_rig(arm, force=True)
print(f"update {1000 * (time.perf_counter() - t):.1f} ms; gui read back ok: "
      f"{np.allclose(faceboard.read_gui(board), gui, atol=1e-5)}")
maps = {k: round(v, 2) for k, v in arm[rig.PROP_MAPS].items() if v > 0}
print("wrinkle maps on:", maps)

head = heads[0]
lo, hi = Vector(head.bound_box[0]), Vector(head.bound_box[6])
centre = (lo + hi) / 2
cam_data = bpy.data.cameras.new("cam"); cam_data.type = "ORTHO"; cam_data.ortho_scale = 0.36
cam = bpy.data.objects.new("cam", cam_data); scene.collection.objects.link(cam); scene.camera = cam
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.render.resolution_x = scene.render.resolution_y = 512
for o in (arm, board): o.hide_render = True
for o in bpy.data.objects:
    if o.name.startswith(board.name): o.hide_render = True
for o in heads:
    if any(k in o.name for k in ("eyeshell", "eyeEdge", "saliva", "cartilage")): o.hide_render = True
for view, loc, rot in (("front", (centre.x, lo.y - 1, centre.z + 0.03), (90, 0, 0)),
                       ("side", (hi.x + 1, centre.y, centre.z + 0.03), (90, 0, 90))):
    cam.location = loc; cam.rotation_euler = tuple(math.radians(a) for a in rot)
    scene.render.filepath = f"{prefix}_{view}.png"
    bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=f"{prefix}.blend")
print("done")
