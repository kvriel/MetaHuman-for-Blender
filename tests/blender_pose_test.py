"""Headless test of the Pose Editor operators.

blender -b --factory-startup --python tests/blender_pose_test.py -- <archetype_folder> <out_dir>
"""
import math
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Quaternion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import pose_ui, rig  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble, scan_folder  # noqa: E402
from mfb.core import dna_edit as E  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402
from mfb.dna.writer import write_dna  # noqa: E402

folder, out = sys.argv[sys.argv.index("--") + 1:][:2]
out = Path(out); out.mkdir(parents=True, exist_ok=True)
reset_scene()
bpy.ops.wm.save_as_mainfile(filepath=str(out / "pose_test.blend"))     # working copies go next to it
mfb.register()
scan = scan_folder(folder)
res = assemble(AssembleOptions(head_dna=scan.head, body_dna=scan.body, name="P", materials=False))
arm = res["armatures"]["body"]
st = bpy.context.scene.mfb_pose
results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


def select(name):
    st.solver_index = [s.name for s in st.solvers].index(name)


def select_pose(name):
    st.pose_index = [p.name for p in st.poses].index(name)


def select_bones(*names, active=None):
    for pb in arm.pose.bones:
        if hasattr(pb, "select"):
            pb.select = pb.name in names
        else:
            pb.bone.select = pb.name in names
    if active:
        arm.data.bones.active = arm.data.bones[active]


def reset_pose():
    for pb in arm.pose.bones:
        pb.rotation_quaternion = (1, 0, 0, 0); pb.rotation_euler = (0, 0, 0); pb.location = (0, 0, 0)
    bpy.context.view_layer.update()


def dna():
    return pose_ui.working_dna(arm, "body")


try:
    st.armature = arm; st.kind = "body"
    check("unpack", bpy.ops.mfb.pose_unpack() == {"FINISHED"} and len(st.solvers) == 72,
          f"{len(st.solvers)} solvers, {len(st.twists)} twist/swing setups")

    # --- edit an existing pose
    select("upperarm_l_UERBFSolver"); select_pose("upperarm_l_out_85")
    check("drivers and driven joints listed", [d.name for d in st.drivers] == ["upperarm_l"] and len(st.driven) > 0,
          f"driven: {[d.name for d in st.driven][:4]}")
    bpy.ops.mfb.pose_edit()
    joint = st.driven[0].name
    pb = arm.pose.bones[joint]
    before = pb.location.copy()
    pb.location.z += 0.01                                   # nudge 1 cm
    check("live rig paused while editing", arm.get(rig.PROP_EDITING) == "body")
    bpy.ops.mfb.pose_update()
    bpy.ops.mfb.pose_finish()
    bpy.context.view_layer.update(); rig.update_rig(arm, force=True)
    after = arm.pose.bones[joint].location
    check("updated pose reproduces the edit through RigLogic", abs((after - before).z - 0.01) < 1e-4,
          f"{joint} z moved {(after - before).z * 100:.2f} cm")
    check("working copy written next to the .blend", arm[rig.PROP_BODY_DNA].endswith("_edit.dna")
          and Path(arm[rig.PROP_BODY_DNA]).exists())

    # --- new solver + pose
    reset_pose()
    select_bones("lowerarm_l", active="lowerarm_l")
    st.new_name = "lowerarm_l_test_UERBFSolver"
    bpy.ops.mfb.pose_new_solver()
    select("lowerarm_l_test_UERBFSolver")
    lower = arm.pose.bones["lowerarm_l"]; lower.rotation_mode = "QUATERNION"
    lower.rotation_quaternion = Quaternion((0, 0, 1), math.radians(70))
    target_bone = "lowerarm_correctiveRoot_l"
    arm.pose.bones[target_bone].location = (0, 0.02, 0)
    select_bones(target_bone)
    st.new_name = "lowerarm_l_bend_70"
    arm[rig.PROP_EDITING] = "body"                          # hold the rig while posing by hand
    bpy.ops.mfb.pose_create()
    del arm[rig.PROP_EDITING]
    reset_pose(); rig.update_rig(arm, force=True)
    rest_loc = arm.pose.bones[target_bone].location.copy()
    lower.rotation_quaternion = Quaternion((0, 0, 1), math.radians(70))
    bpy.context.view_layer.update(); rig.update_rig(arm, force=True)
    moved = (arm.pose.bones[target_bone].location - rest_loc).length
    check("new solver + pose drive the corrective", abs(moved - 0.02) < 2e-3, f"moved {moved * 100:.2f} cm")

    # --- mirror, in-betweens, copy/paste, multiplier, mute, rename, delete
    select("upperarm_l_UERBFSolver"); select_pose("upperarm_l_out_85")
    bpy.ops.mfb.pose_mirror()
    names = [p.name for p in dna().rbf.poses]
    r_si = E.solver_index(dna(), "upperarm_r_UERBFSolver")
    check("mirror makes upperarm_r_out_85 on the right solver", "upperarm_r_out_85" in names and
          names.index("upperarm_r_out_85") in [int(p) for p in dna().rbf.solvers[r_si].pose_indices])
    select("upperarm_l_UERBFSolver"); select_pose("upperarm_l_out_85")
    bpy.ops.mfb.pose_inbetweens()
    check("in-between generated", "upperarm_l_out_85_ib50" in [p.name for p in dna().rbf.poses])
    select_pose("upperarm_l_out_85"); bpy.ops.mfb.pose_copy()
    select_pose("upperarm_l_back_45"); bpy.ops.mfb.pose_paste()
    a = E.joint_column(dna(), E.pose_control(dna(), [p.name for p in dna().rbf.poses].index("upperarm_l_out_85")))
    b = E.joint_column(dna(), E.pose_control(dna(), [p.name for p in dna().rbf.poses].index("upperarm_l_back_45")))
    check("copy/paste driven transforms", set(a) == set(b) and all(np.allclose(a[j], b[j]) for j in a))
    bpy.ops.mfb.pose_scale(value=0.5)
    check("multiplier", abs(dna().rbf.poses[st.poses[st.pose_index].index].scale - 0.5) < 1e-6)
    bpy.ops.mfb.pose_mute()
    check("mute solver", "upperarm_l_UERBFSolver" in list(arm.get(rig.PROP_MUTED, [])))
    bpy.ops.mfb.pose_mute()
    select_pose("upperarm_l_out_85_ib50"); st.new_name = "upperarm_l_out_42"
    bpy.ops.mfb.pose_rename()
    check("rename", "upperarm_l_out_42" in [p.name for p in dna().rbf.poses])
    n_before = len(dna().rbf.poses)
    select_pose("upperarm_l_out_42"); bpy.ops.mfb.pose_delete()
    check("delete pose", len(dna().rbf.poses) == n_before - 1)

    # --- twist setup, find/replace, validation, export, upgrade
    select_bones("upperarm_twist_01_l", "upperarm_twist_02_l", "upperarm_l", active="upperarm_l")
    n_tw = len(dna().twist_swing.twists)
    bpy.ops.mfb.twist_default(swing=False)
    check("default twist setup", len(dna().twist_swing.twists) == n_tw + 1, st.twists[-1].name)
    st.search, st.replace, st.use_regex = r"_test_", "_t_", True
    bpy.ops.mfb.pose_find_replace()
    check("find and replace", "lowerarm_l_t_UERBFSolver" in [s.name for s in dna().rbf.solvers])
    bpy.ops.mfb.pose_validate(target="RIGLOGIC")
    bpy.ops.mfb.pose_validate(target="MHC")
    errors = [m.text for m in st.log if m.level == "ERROR"]
    check("validation: no errors", not errors, "; ".join(errors)[:200])
    st.export_path = str(out / "exported_body.dna")
    bpy.ops.mfb.pose_export(geometry=True)
    back = read_dna(out / "exported_body.dna")
    check("export writes the edits", "lowerarm_l_bend_70" in [p.name for p in back.rbf.poses]
          and len(back.meshes) == 4)
    st.selected_only = True
    select("lowerarm_l_t_UERBFSolver")
    st.export_path = str(out / "exported_selected.dna")
    bpy.ops.mfb.pose_export(geometry=False)
    sel = read_dna(out / "exported_selected.dna")
    original = read_dna(scan.body)
    check("export selected solver onto the original", "lowerarm_l_bend_70" in [p.name for p in sel.rbf.poses]
          and "upperarm_r_out_85" in [p.name for p in sel.rbf.poses]
          and len(sel.rbf.solvers) == len(original.rbf.solvers) + 1)
    old = read_dna(scan.body, geometry=False); old.version = (2, 3)
    old.layer_order = [x for x in old.layer_order if x[0] in ("desc", "defn", "bhvr", "geom", "mlbh")]
    write_dna(old, out / "old.dna")
    bpy.ops.mfb.dna_upgrade(filepath=str(out / "old.dna"))
    check("upgrade DNA file", read_dna(out / "old_v25.dna", geometry=False).version == (2, 5))
except Exception:
    traceback.print_exc()
    results.append(False)
print(f"RESULT {sum(results)}/{len(results)} passed")
