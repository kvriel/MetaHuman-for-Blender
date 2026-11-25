"""Export a character for the Unreal import check: head with blend shapes + body, 30 frames of
face animation, via the add-on's Export to Unreal. Also copies the test grooms.

blender -b --factory-startup --python tests/ue_make_assets.py -- <head.dna> <body.dna> <groom_dir> <out_dir>
"""
import shutil
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
import mfb  # noqa: E402
from mfb.blender import rig  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble  # noqa: E402

head, body, groom_dir, out = sys.argv[sys.argv.index("--") + 1:][:4]
out = Path(out)
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
mfb.register()
res = assemble(AssembleOptions(head_dna=head, body_dna=body, name="MFB", materials=False))
arm = res["armatures"]["head"]
board = bpy.data.objects[arm[rig.PROP_BOARD_NAME]]
u = board["mfb_board_unit"]
sc = bpy.context.scene
sc.frame_start, sc.frame_end = 1, 30
for f, jaw, smile in ((1, 0.0, 0.0), (15, 1.0, 0.0), (30, 0.0, 1.0)):
    sc.frame_set(f)
    board.pose.bones["CTRL_C_jaw"].location.y = jaw * u
    for side in "LR":
        board.pose.bones[f"CTRL_{side}_mouth_cornerPull"].location.y = smile * u
    for name in ("CTRL_C_jaw", "CTRL_L_mouth_cornerPull", "CTRL_R_mouth_cornerPull"):
        board.pose.bones[name].keyframe_insert("location", index=1, frame=f)
print("export:", bpy.ops.mfb.export_unreal(directory=str(out), armature=arm.name))
for abc in Path(groom_dir).glob("*.abc"):
    shutil.copy(abc, out / abc.name)
for p in sorted(out.iterdir()):
    print(f"FILE {p.name} {p.stat().st_size}")
