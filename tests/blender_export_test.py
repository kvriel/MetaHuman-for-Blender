"""Headless test of Export to Unreal:
blender -b --factory-startup --python tests/blender_export_test.py -- <folder> <out> [template head .dna with blend shapes]
"""
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402
import mfb  # noqa: E402
from mfb.blender import rig  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble, scan_folder  # noqa: E402
from mfb.dna.reader import read_dna  # noqa: E402

folder, out, *rest = sys.argv[sys.argv.index("--") + 1:]
template = rest[0] if rest else ""
out = Path(out); out.mkdir(parents=True, exist_ok=True)
reset_scene()
mfb.register()
results = []


def check(label, cond, detail=""):
    results.append(bool(cond)); print(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


try:
    scan = scan_folder(folder)
    res = assemble(AssembleOptions(head_dna=scan.head, body_dna=scan.body, name="UE", materials=False))
    arm = res["armatures"]["head"]
    board = bpy.data.objects[arm[rig.PROP_BOARD_NAME]]
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 10
    jaw = board.pose.bones["CTRL_C_jaw"]
    sc.frame_set(1); jaw.location.y = 0; jaw.keyframe_insert("location", index=1, frame=1)
    sc.frame_set(10); jaw.location.y = board["mfb_board_unit"]; jaw.keyframe_insert("location", index=1, frame=10)
    r = bpy.ops.mfb.export_unreal(directory=str(out), armature=arm.name)
    files = sorted(p.name for p in out.iterdir() if p.suffix in (".fbx", ".dna", ".json", ".py"))
    check("export writes DNA, FBX, curves and the Unreal script", r == {"FINISHED"} and len(files) == 5, ", ".join(files))
    check("exported DNA reads back", read_dna(out / "UE_head.dna", geometry=False).definition.gui_control_names != [])
    import json
    data = json.loads((out / "UE_face_curves.json").read_text())
    jaw_c = data["curves"].get("CTRL_expressions_jawOpen", [])
    check("face curves baked per frame", len(data["times"]) == 10 and len(jaw_c) == 10 and abs(jaw_c[0]) < 0.02
          and abs(jaw_c[-1] - 1) < 0.02, f"{len(data['curves'])} curves, jawOpen {jaw_c[0]:.2f} -> {jaw_c[-1]:.2f}")
    import ast
    ast.parse((out / "mfb_import_face_curves.py").read_text())
    check("Unreal import script is valid Python", True)
    reset_scene()
    bpy.ops.import_scene.fbx(filepath=str(out / "UE.fbx"))
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    check("character FBX reads back", len(arms) == 1 and len(arms[0].data.bones) > 1000 and len(meshes) >= 9,
          f"{len(arms[0].data.bones) if arms else 0} bones, {len(meshes)} meshes")
    if template and Path(template).exists():
        # morph target names, on a head with blend shapes (the archetype face has none)
        from mfb.core.fbx_names import object_names
        reset_scene()
        res = assemble(AssembleOptions(head_dna=template, name="MT", materials=False))
        tout = out / "morph_names"
        tout.mkdir(exist_ok=True)
        r = bpy.ops.mfb.export_unreal(directory=str(tout), armature=res["armatures"]["head"].name,
                                      animation=False, dna=False)
        head_dna = read_dna(template)
        hd = head_dna.definition
        want = {f"{hd.mesh_names[mi]}__{hd.blend_shape_channel_names[t.channel]}"
                for mi in hd.lod_meshes.for_lod(0).tolist() for t in head_dna.meshes[mi].blend_shapes}
        channels = {n for n, c in object_names(tout / "MT.fbx") if c == "SubDeformer" and "__" in n}
        long = [n for n in want if len(n.encode()) > 63]
        check("FBX morph targets carry RigLogic's <mesh>__<channel> names, full length",
              r == {"FINISHED"} and want and channels == want,
              f"{len(channels)}/{len(want)} names, {len(long)} longer than Blender's 63 bytes, "
              f"missing {sorted(want - channels)[:2]}, extra {sorted(channels - want)[:2]}")
        keys = [k.name for o in bpy.data.objects if o.type == "MESH" and o.data.shape_keys
                for k in o.data.shape_keys.key_blocks]
        check("the scene's shape keys keep their names", keys and not any(k.startswith("mfbmorph") for k in keys))
except Exception:
    traceback.print_exc(); results.append(False)
print(f"RESULT {sum(results)}/{len(results)} passed")
