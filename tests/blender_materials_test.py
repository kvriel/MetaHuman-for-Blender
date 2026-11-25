"""Headless test of materials + wrinkle maps with stand-in textures.

blender -b --factory-startup --python tests/blender_materials_test.py -- <head.dna> <masks_dir> <out_prefix>
cm1 = red, cm2 = green, cm3 = blue, so the render shows which wrinkle layer is active where.
"""
import shutil
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import rig  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble  # noqa: E402

head, masks_dir, prefix = sys.argv[sys.argv.index("--") + 1:][:3]
reset_scene()
mfb.register()

tex = Path(prefix).parent / "stand_in_textures"
shutil.rmtree(tex, ignore_errors=True); tex.mkdir(parents=True)
for name, rgba in (("head_color", (0.62, 0.45, 0.38, 1)), ("head_cm1_color", (0.9, 0.05, 0.05, 1)),
                   ("head_cm2_color", (0.05, 0.8, 0.05, 1)), ("head_cm3_color", (0.05, 0.1, 0.9, 1)),
                   ("head_normal", (0.5, 0.5, 1, 1)), ("head_wm1_normal", (0.5, 0.5, 1, 1)),
                   ("head_wm2_normal", (0.5, 0.5, 1, 1)), ("head_wm3_normal", (0.5, 0.5, 1, 1)),
                   ("teeth_color", (0.9, 0.88, 0.8, 1)), ("eyeLeft_color", (0.3, 0.45, 0.6, 1)),
                   ("eyeRight_color", (0.3, 0.45, 0.6, 1))):
    img = bpy.data.images.new(name, 64, 64, alpha=True)
    img.pixels = list(rgba) * (64 * 64)
    img.filepath_raw = str(tex / f"{name}.png"); img.file_format = "PNG"; img.save()
for m in Path(masks_dir).glob("*.tga"):
    shutil.copy(m, tex / m.name)

res = assemble(AssembleOptions(head_dna=head, textures=str(tex), name="Mat", lights=True))
mats = res["materials"]
head_mat = mats["head"]
n_attr = sum(1 for n in head_mat.node_tree.nodes if n.type == "ATTRIBUTE")
n_mask = sum(1 for n in head_mat.node_tree.nodes if n.type == "TEX_IMAGE" and "msk" in (n.image.name if n.image else ""))
print(f"materials: {sorted(mats)}; head: {n_attr} wrinkle attributes, {n_mask} mask textures, "
      f"{len(head_mat.node_tree.nodes)} nodes; lights: {len(res.get('lights', []))}")

arm = res["armatures"]["head"]
board = bpy.data.objects[arm[rig.PROP_BOARD_NAME]]
u = board["mfb_board_unit"]
for ctrl in ("CTRL_L_brow_raiseIn", "CTRL_R_brow_raiseIn", "CTRL_L_mouth_cornerPull", "CTRL_R_mouth_cornerPull",
             "CTRL_L_nose", "CTRL_R_nose"):
    if ctrl in board.pose.bones:
        board.pose.bones[ctrl].location.y = u
rig._on_update()
head_obj = next(o for o in res["meshes"] if o.data.name.startswith("head_lod0"))
on = {k: round(v, 2) for k, v in head_obj.items() if k.startswith("wm_") and v > 0}
print("wrinkle weights on head mesh:", on)

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
cam_data = bpy.data.cameras.new("cam"); cam_data.type = "ORTHO"; cam_data.ortho_scale = 0.34
cam = bpy.data.objects.new("cam", cam_data); scene.collection.objects.link(cam); scene.camera = cam
cam.location = (0, -2, 1.5); cam.rotation_euler = (1.5708, 0, 0)
scene.render.resolution_x = scene.render.resolution_y = 512
for o in bpy.data.objects:
    if o.type in {"ARMATURE", "FONT", "EMPTY"} or "_faceboard" in o.name:
        o.hide_render = True
scene.render.filepath = f"{prefix}.png"
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=f"{prefix}.blend")
print("done")
