"""Headless Blender smoke test for the importer.

blender -b --factory-startup --python tests/blender_smoke.py -- <file.dna> <out.png> [lod]
Builds the armature and the chosen LOD's meshes, prints checks, renders a front view.
"""
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))

from mfb.blender.build import BuildOptions, build_armature, build_mesh  # noqa: E402
from mfb.core.space import Space  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
dna_path, out_png = args[0], args[1]
lod = int(args[2]) if len(args) > 2 else 0

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

t0 = time.perf_counter()
dna = read_dna(dna_path)
space = Space.for_dna(dna)
root = bpy.data.collections.new(Path(dna_path).stem)
scene.collection.children.link(root)
arm = build_armature(dna, space, "rig", root)
mesh_ids = [int(i) for i in dna.definition.lod_meshes.for_lod(lod)]
objs = [build_mesh(dna, space, i, root, arm, BuildOptions()) for i in mesh_ids]
print(f"built {len(objs)} meshes + {len(arm.data.bones)} bones in {time.perf_counter() - t0:.1f}s")

# --- checks
for o in objs:
    src = dna.meshes[int(o["mfb_mesh_index"])]
    me = o.data
    assert len(me.vertices) == len(src.positions), o.name
    assert len(me.polygons) == len(src.faces), o.name
    print(f"  {o.name}: verts={len(me.vertices)} faces={len(me.polygons)} groups={len(o.vertex_groups)} "
          f"keys={len(me.shape_keys.key_blocks) if me.shape_keys else 0}")
head = objs[0]
lo, hi = Vector(head.bound_box[0]), Vector(head.bound_box[6])
print(f"head bounds (m): min={tuple(round(v, 3) for v in lo)} max={tuple(round(v, 3) for v in hi)}")
fh = arm.data.bones.get("FACIAL_C_FacialRoot") or arm.data.bones[0]
print(f"bone {fh.name} head={tuple(round(v, 3) for v in fh.head_local)}")

# --- render a front view with the armature deform applied (rest pose must match the mesh)
cam_data = bpy.data.cameras.new("cam"); cam_data.type = "ORTHO"
cam = bpy.data.objects.new("cam", cam_data); scene.collection.objects.link(cam)
centre = (lo + hi) / 2
size = max(hi.x - lo.x, hi.z - lo.z) * 1.15
cam_data.ortho_scale = size
cam.location = (centre.x, lo.y - 1.0, centre.z)
cam.rotation_euler = (math.radians(90), 0, 0)
scene.camera = cam
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "OBJECT"
scene.render.resolution_x = scene.render.resolution_y = 640
scene.render.filepath = out_png
arm.hide_render = True
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(Path(out_png).with_suffix(".blend")))
print("rendered", out_png)
