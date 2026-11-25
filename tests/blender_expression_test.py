"""Headless test of the Expression Editor.

blender -b --factory-startup --python tests/blender_expression_test.py -- <head_with_blendshapes.dna> <out_dir>
"""
import json
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import expression_ui as XU, pose_ui, rig  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble  # noqa: E402
from mfb.core import dna_edit as E, expression as X  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402

head_path, out = sys.argv[sys.argv.index("--") + 1:][:2]
out = Path(out); out.mkdir(parents=True, exist_ok=True)
reset_scene()
bpy.ops.wm.save_as_mainfile(filepath=str(out / "expr_test.blend"))
mfb.register()
res = assemble(AssembleOptions(head_dna=head_path, name="X", materials=False))
arm = res["armatures"]["head"]
st = bpy.context.scene.mfb_expr
ctx = bpy.context
results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


def pick(name):
    st.expr_index = [e.name for e in st.expressions].index(name)


def dna():
    return pose_ui.working_dna(arm, "head")


try:
    st.armature = arm
    bpy.ops.mfb.expr_refresh()
    n_psd = sum(e.is_psd for e in st.expressions)
    check("expressions listed", len(st.expressions) > 700, f"{len(st.expressions) - n_psd} expressions, {n_psd} correctives")
    head = XU._head_mesh(arm)
    pick("jawOpen"); bpy.ops.mfb.expr_preview()
    check("preview shows the expression", abs(arm.pose.bones["FACIAL_C_Jaw"].rotation_euler.x) > 0.2)

    # --- sculpt jawOpen: push the lower face 3 mm forward in the posed shape
    st.mode = "EXPRESSION"
    bpy.ops.mfb.expr_sculpt()
    sculpt = next(o for o in bpy.context.scene.objects if "mfb_sculpt_for" in o)
    co = np.zeros(len(sculpt.data.vertices) * 3, np.float32); sculpt.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    region = co[:, 2] < np.percentile(co[:, 2], 25)
    co[region, 1] -= 0.003
    sculpt.data.vertices.foreach_set("co", co.ravel()); sculpt.data.update()
    graph = X.expression_graph(dna())
    jaw = next(c for c, e in graph.items() if e.name == "jawOpen")
    down = [c for c in X.downstream(graph, jaw)][:3]
    lock = down[0]
    st.expressions[[e.control for e in st.expressions].index(lock)].locked = True
    mi = int(head["mfb_mesh_index"])
    before = {c: X.target_delta(dna(), mi, X.channels_for(dna(), c)[0]).copy() for c in down if X.channels_for(dna(), c)}
    st.propagation, st.strength = "KEEP", 1.0
    bpy.ops.mfb.expr_apply_sculpt()
    bpy.ops.mfb.expr_preview()
    now = XU._evaluated_coords(ctx, head)
    err = np.abs(now - co).max() * 1000
    check("sculpted expression is reproduced by the rig (corrective shape)", err < 0.05, f"max error {err:.3f} mm")
    after = {c: X.target_delta(dna(), mi, X.channels_for(dna(), c)[0]) for c in before}
    changed = [c for c in before if not np.allclose(before[c], after[c])]
    check("propagation changed unlocked correctives, not the locked one",
          lock not in changed and len(changed) == len(before) - (lock in before), f"{len(changed)} changed")

    # --- branch, previews
    pick("jawOpen")
    bpy.ops.mfb.expr_branch(direction="DOWN")
    check("downstream branch marked", sum(e.marked for e in st.expressions) == len(X.downstream(graph, jaw)) + 1)
    bpy.ops.mfb.expr_previews()
    n_prev = len(bpy.data.collections["MFB_Previews"].objects)
    bpy.ops.mfb.expr_delete_previews()
    check("preview sculpt meshes", n_prev == min(24, len(X.downstream(graph, jaw))))

    # --- neutral: raise the whole face 1 mm, joints follow
    bpy.ops.mfb.expr_neutral()
    st.mode = "NEUTRAL"
    basis = head.data.shape_keys.key_blocks[0]
    b = np.zeros(len(basis.data) * 3, np.float32); basis.data.foreach_get("co", b)
    b = b.reshape(-1, 3); b[:, 2] += 0.001
    basis.data.foreach_set("co", b.ravel()); head.data.update()
    root_before = arm.data.bones["FACIAL_C_FacialRoot"].head_local.copy()
    bpy.ops.mfb.expr_apply_neutral()
    moved = (arm.data.bones["FACIAL_C_FacialRoot"].head_local - root_before).z * 1000
    lod1 = dna().meshes[dna().definition.mesh_names.index("head_lod1_mesh")]
    lod1_orig = read_dna(head_path, meshes={dna().definition.mesh_names.index("head_lod1_mesh")})
    lod_shift = (lod1.positions - lod1_orig.meshes[dna().definition.mesh_names.index("head_lod1_mesh")].positions)[:, 1].mean()
    check("neutral reshape stored, joints matched, LODs carried", abs(moved - 1.0) < 0.05 and abs(lod_shift - 0.1) < 0.01,
          f"face root moved {moved:.3f} mm, LOD1 moved {lod_shift * 10:.3f} mm")

    # --- skin weights, copy joints
    st.mode = "SKIN"
    vg = head.vertex_groups[0]
    vg.add([0], 0.25, "ADD")
    bpy.ops.mfb.expr_apply_skin()
    check("skin weights stored", dna().meshes[mi].skin_weights[0].sum() > 0.99)
    st.mode = "JOINTS"
    pick("jawOpen"); st.copy_target = "jawLeft"
    bpy.ops.mfb.expr_copy_joints()
    a = E.joint_column(dna(), jaw)
    b2 = E.joint_column(dna(), next(c for c, e in graph.items() if e.name == "jawLeft"))
    keys = set(a) | set(b2)
    z = np.zeros(9)
    check("copy expression joints", all(np.allclose(a.get(j, z), b2.get(j, z), atol=1e-5) for j in keys),
          f"{len(keys)} joints compared")
    bpy.ops.mfb.expr_match_joints()
    check("run joints matching", "Joints matching" in st.status)

    # --- helpers
    bpy.ops.mfb.expr_bookmark(action="PRESET_EYES")
    n_eyes = len(st.bookmarks)
    bpy.ops.mfb.expr_bookmark(action="PRESET_FULL")
    st.bookmark_index = 0
    bpy.ops.mfb.expr_bookmark(action="APPLY")
    applied = arm.pose.bones[rig.FACE_ROOT][st.bookmarks[0].name]
    bpy.ops.mfb.expr_bookmark_file(export=True, filepath=str(out / "bm.json"))
    st.bookmarks.clear()
    bpy.ops.mfb.expr_bookmark_file(export=False, filepath=str(out / "bm.json"))
    check("bookmarks: presets, apply, export, import", n_eyes > 10 and applied == 1.0 and len(st.bookmarks) > n_eyes)
    bpy.ops.mfb.expr_rom()
    check("range-of-motion keys", bpy.context.scene.frame_end > 400, f"{bpy.context.scene.frame_end} frames")
    bpy.ops.mfb.expr_settings_file(save=True, filepath=str(out / "settings.json"))
    st.propagation = "CARRY"
    bpy.ops.mfb.expr_settings_file(save=False, filepath=str(out / "settings.json"))
    check("settings save and load", st.propagation == "KEEP" and json.loads((out / "settings.json").read_text())["locked"])
    st.file_path = str(out / "edited_head.dna")
    bpy.ops.mfb.expr_save_dna()
    back = read_dna(out / "edited_head.dna", meshes={mi})
    check("save DNA from memory", np.allclose(back.meshes[mi].positions, dna().meshes[mi].positions))
    for up in ("Y", "Z"):
        st.file_path, st.export_up = str(out / f"head_{up}.fbx"), up
        bpy.ops.mfb.expr_export_fbx()
    check("export FBX Y-up and Z-up", (out / "head_Y.fbx").stat().st_size > 1e6 and (out / "head_Z.fbx").exists())
except Exception:
    traceback.print_exc()
    results.append(False)
print(f"RESULT {sum(results)}/{len(results)} passed")
