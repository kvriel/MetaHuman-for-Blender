"""Headless test of the live body RigLogic (RBF correctives, twist/swing) and face neck correctives.

blender -b --factory-startup --python tests/blender_body_rig_test.py -- <archetype_folder> <out_prefix>
Poses joints exactly at named RBF targets (e.g. calf_l_back_90, upperarm_l_fwd_110), checks that
the matching pose wins, and renders so the bend direction can be compared with the pose name.
"""
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Quaternion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import rig  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble, scan_folder  # noqa: E402

folder, prefix = sys.argv[sys.argv.index("--") + 1:][:2]
reset_scene()
mfb.register()
scan = scan_folder(folder)
res = assemble(AssembleOptions(head_dna=scan.head, body_dna=scan.body, name="B", materials=False))
arm = res["armatures"]["body"]
body_rt = rig.runtime(arm, "body")
head_rt = rig.runtime(arm, "head")


def check(label, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


def set_target(rt, pose_name):
    """Put every driver joint of the solver holding `pose_name` at that pose's target quaternion."""
    dna = rt.dna
    for si, s in enumerate(dna.rbf.solvers):
        names = [dna.rbf.poses[p].name for p in s.pose_indices]
        if pose_name in names:
            k = len(s.raw_control_indices)
            tgt = s.raw_control_values.reshape(-1, k)[names.index(pose_name)]
            for qi in range(k // 4):
                joint = dna.definition.raw_control_names[s.raw_control_indices[qi * 4]].rpartition(".")[0]
                x, y, z, w = tgt[qi * 4:qi * 4 + 4]
                pb = arm.pose.bones[joint]
                pb.rotation_mode = "QUATERNION"
                pb.rotation_quaternion = Quaternion((w, x, y, z))
            return si, names.index(pose_name)
    raise KeyError(pose_name)


def corrective_motion(rt):
    d = rt.dna.definition
    driven = np.nonzero(rt.rig.driven_joints())[0]
    moved = 0
    for j in driven:
        pb = arm.pose.bones.get(d.joint_names[j])
        if pb and (pb.location.length > 1e-5 or abs(pb.rotation_euler.x) + abs(pb.rotation_euler.y)
                   + abs(pb.rotation_euler.z) > 1e-4):
            moved += 1
    return moved


rig.update_rig(arm, force=True)
check("neutral pose: no correctives move", corrective_motion(body_rt) == 0)

for pose in ("calf_l_back_90", "upperarm_l_fwd_110", "upperarm_l_out_85"):
    for pb in arm.pose.bones:
        pb.rotation_quaternion = (1, 0, 0, 0); pb.rotation_mode = pb.rotation_mode
    si, pi = set_target(body_rt, pose)
    bpy.context.view_layer.update()
    rig.update_rig(arm, force=True)
    w = body_rt.rig.rbf.last_weights[si]
    check(f"body pose {pose} wins its solver", int(np.argmax(w)) == pi and w[pi] > 0.95,
          f"weight {w[pi]:.2f}, {corrective_motion(body_rt)} corrective/twist joints moved")

# twist: turn the left hand around its twist axis; forearm twist joints should share the turn
for pb in arm.pose.bones:
    pb.rotation_quaternion = (1, 0, 0, 0)
hand = arm.pose.bones["hand_l"]
hand.rotation_mode = "QUATERNION"
hand.rotation_quaternion = Quaternion((1, 0, 0), math.radians(60))
bpy.context.view_layer.update()
rig.update_rig(arm, force=True)
tw = [math.degrees(arm.pose.bones[n].rotation_euler.to_quaternion().angle)
      for n in ("lowerarm_twist_01_l", "lowerarm_twist_02_l")]
check("hand twist spreads to forearm twist joints", all(0.5 < t < 60 for t in tw),
      f"twist joints turned {tw[0]:.1f}° and {tw[1]:.1f}° for a 60° hand twist")

# face neck correctives: head turned to a neck RBF target
for pb in arm.pose.bones:
    pb.rotation_quaternion = (1, 0, 0, 0)
si, pi = set_target(head_rt, "turnLeft_65")
bpy.context.view_layer.update()
rig.update_rig(arm, force=True)
w = head_rt.rig.rbf.last_weights[si]
check("face neck corrective turnLeft_65 wins", int(np.argmax(w)) == pi and w[pi] > 0.95, f"weight {w[pi]:.2f}")
yaw_dir = (arm.pose.bones["head"].matrix.to_3x3() @ arm.data.bones["head"].matrix_local.to_3x3().inverted())
fwd = yaw_dir @ __import__("mathutils").Vector((0, -1, 0))
check("turnLeft_65 turns the face toward the character's left (+X)", fwd.x > 0.5,
      f"face forward now {tuple(round(v, 2) for v in fwd)}")

# render the arm pose so the bend can be seen
for pb in arm.pose.bones:
    pb.rotation_quaternion = (1, 0, 0, 0)
set_target(body_rt, "upperarm_l_fwd_110"); set_target(body_rt, "calf_l_back_90")
bpy.context.view_layer.update(); rig.update_rig(arm, force=True)
scene = bpy.context.scene
cam_data = bpy.data.cameras.new("cam"); cam_data.type = "ORTHO"; cam_data.ortho_scale = 2.1
cam = bpy.data.objects.new("cam", cam_data); scene.collection.objects.link(cam); scene.camera = cam
scene.render.engine = "BLENDER_WORKBENCH"; scene.display.shading.light = "STUDIO"
scene.render.resolution_x, scene.render.resolution_y = 512, 640
for o in bpy.data.objects:
    if o.type in {"ARMATURE", "FONT", "EMPTY"} or "_faceboard" in o.name or any(
            k in o.name for k in ("eyeshell", "eyeEdge", "saliva", "cartilage")):
        o.hide_render = True
for view, loc, rot in (("front", (0, -4, 0.95), (90, 0, 0)), ("side", (4, 0, 0.95), (90, 0, 90))):
    cam.location = loc; cam.rotation_euler = tuple(math.radians(a) for a in rot)
    scene.render.filepath = f"{prefix}_{view}.png"
    bpy.ops.render.render(write_still=True)
print("done")
