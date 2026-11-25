"""Headless test: face-root attributes, keys, raw drive mode, eye aim and follow head.

blender -b --factory-startup --python tests/blender_face_features_test.py -- <head.dna>
"""
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import rig  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble  # noqa: E402

head = sys.argv[sys.argv.index("--") + 1]
reset_scene()
mfb.register()
res = assemble(AssembleOptions(head_dna=head, name="T"))
arm = res["armatures"]["head"]
root = arm.pose.bones[rig.FACE_ROOT]
floats = [k for k in root.keys() if isinstance(root[k], float)]
def fcurves_of(obj):
    ad = obj.animation_data
    if not ad or not ad.action:
        return []
    from bpy_extras import anim_utils
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return list(bag.fcurves) if bag else []


fcurves = fcurves_of(arm)
print(f"face root attributes: {len(floats)} (e.g. {floats[:3]}), keyed curves: {len(fcurves)}")


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")


board = bpy.data.objects[arm[rig.PROP_BOARD_NAME]]
unit = board["mfb_board_unit"]

# face board layout: a knob for every board control, no two knob ranges overlapping, one labels object
from mfb.core import board_layout  # noqa: E402
from mfb.dna.reader import read_dna as _read  # noqa: E402
_gui = _read(rig.head_dna_path(arm), geometry=False).definition.gui_control_names
_controls = {g.rpartition(".")[0] for g in _gui}
_ranges = {}
for _pb in board.pose.bones:
    _lim = _pb.constraints[0]
    _ranges[_pb.name] = {0: (_lim.min_x / unit, _lim.max_x / unit), 1: (_lim.min_y / unit, _lim.max_y / unit)}
_lay = board_layout.layout(_ranges)
check(f"face board has a knob for each of the {len(_controls)} board controls",
      {pb.name for pb in board.pose.bones} == _controls)
check("no two knob ranges overlap on the board", not board_layout.overlaps(_lay, _ranges))
_children = [o for o in bpy.data.objects if o.parent == board]
check("board drawing and labels are two objects (one parent line each)", sorted(o.type for o in _children) == ["MESH", "MESH"])

board.pose.bones["CTRL_C_jaw"].location.y = unit
rig._on_update()
check("board -> raw attribute jawOpen = 1", abs(root["jawOpen"] - 1.0) < 1e-5)
check("wrinkle attribute on face root", root.get("head_cm1_color_head_wm1_jawOpen", 0) > 0.99
      or any(v > 0.99 for k, v in root.items() if "jawOpen" in k and k != "jawOpen"))
jaw = arm.pose.bones["FACIAL_C_Jaw"]
jaw_rot_board = tuple(jaw.rotation_euler)

# raw drive mode: animate the attribute directly
board.pose.bones["CTRL_C_jaw"].location.y = 0
arm[rig.PROP_DRIVE] = "RAW"
root["jawOpen"] = 1.0
rig._on_update()
check("raw mode reproduces the board pose", np.allclose(tuple(jaw.rotation_euler), jaw_rot_board, atol=1e-5))
arm[rig.PROP_DRIVE] = "BOARD"
rig._on_update()
check("back to board mode resets jaw", abs(jaw.rotation_euler.x) < 1e-6)

# eye aim: target 25 cm to the character's left and 10 cm up, 35 cm in front
aim = bpy.data.objects[arm[rig.PROP_AIM_NAME]]
aim[rig.PROP_AIM_SWITCH] = 1.0
aim.location.x += 0.10
aim.location.z += 0.06
bpy.context.view_layer.update()
rig._on_update()
bpy.context.view_layer.update()
for side, bone in (("L", "FACIAL_L_Eye"), ("R", "FACIAL_R_Eye")):
    pb = arm.pose.bones[bone]
    rest = pb.bone.matrix_local.to_3x3()
    pose = pb.matrix.to_3x3()
    fwd = (pose @ rest.inverted() @ Vector((0, -1, 0))).normalized()
    eye_pos = arm.matrix_world @ pb.head
    target = next(c for c in aim.children if c.name.startswith(f"CTRL_{side}_eyeAim")).matrix_world.translation
    want = (target - eye_pos).normalized()
    err = math.degrees(fwd.angle(want))
    print(f"  {side} eye: forward {tuple(round(v, 3) for v in fwd)}, error to target {err:.1f}°")
    check(f"{side} eye looks at its aim target (within 6°)", err < 6.0)

# follow head: rotate the head bone, board and aim should move with it
board_before = board.matrix_world.translation.copy()
arm.pose.bones["head"].rotation_euler = (0.0, 0.0, math.radians(30)) if "head" in arm.pose.bones else (0, 0, 0)
bpy.context.view_layer.update()
moved = (board.matrix_world.translation - board_before).length
check(f"face board follows head ({moved * 100:.1f} cm moved)", moved > 0.02)
board["follow_head"] = 0.0
board.update_tag()
bpy.context.view_layer.update()
print("  influence now", board.constraints["mfb_follow_head"].influence)
check("follow_head = 0 leaves the board in place",
      (board.matrix_world.translation - board_before).length < 1e-4)

print("key board:", rig.key_face_board(arm, frame=10), "channels keyed")
