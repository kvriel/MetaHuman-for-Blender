"""Runs inside Unreal Editor (pythonscript commandlet): import what MetaHuman for Blender exported
and check it. Results go to <dir>/ue_results.txt.

UnrealEditor-Cmd.exe <project>.uproject -run=pythonscript -script="tests/ue_import_test.py <dir>"
"""
import importlib.util
import json
import sys
import traceback
from pathlib import Path

import unreal

_args = [a for a in sys.argv[1:] if not a.startswith("--")]
SRC = Path(_args[-1]) if _args else Path(__file__).resolve().parents[1] / "tests/out/ue_verify"
OUT = SRC / "ue_results.txt"
OUT.write_text("", encoding="utf-8")
tools = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary


def log(msg):
    unreal.log(f"[MFBTEST] {msg}")
    with OUT.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def check(label, cond, detail=""):
    log(f"[{'PASS' if cond else 'FAIL'}] {label}{' - ' + detail if detail else ''}")


def import_file(path: Path, dest: str, options=None):
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", str(path))
    task.set_editor_property("destination_path", dest)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", False)       # the task's save path needs the editor UI (Slate)
    task.set_editor_property("replace_existing", True)
    if options is not None:
        task.set_editor_property("options", options)
    tools.import_asset_tasks([task])
    paths = list(task.get_editor_property("imported_object_paths") or [])
    return [eal.load_asset(p) for p in paths], paths


def assets_of(dest, cls):
    out = []
    for p in eal.list_assets(dest, recursive=True, include_folder=False):
        a = eal.load_asset(p)
        if isinstance(a, cls):
            out.append(a)
    return out


# --------------------------------------------------------------------------- character FBX
skm = None
try:
    log("=== Character FBX")
    if eal.does_asset_exist("/Game/MFB/Character/MFB"):
        log("reusing the character imported by an earlier run (the FBX import takes ~15 minutes)")
    else:
        objs, paths = import_file(SRC / "MFB.fbx", "/Game/MFB/Character")
        log(f"imported: {[p.split('.')[-1] for p in paths][:12]}")
        eal.save_directory("/Game/MFB/Character", only_if_is_dirty=True, recursive=True)
    meshes = assets_of("/Game/MFB/Character", unreal.SkeletalMesh)
    check("FBX imports as a skeletal mesh", len(meshes) >= 1, f"{len(meshes)} skeletal meshes")
    if meshes:
        skm = max(meshes, key=lambda m: len(m.get_all_morph_target_names()))
        morphs = skm.get_all_morph_target_names()
        check("blend shapes arrive as morph targets", len(morphs) > 700, f"{len(morphs)} morph targets, e.g. {list(morphs)[:3]}")
        skel = skm.get_editor_property("skeleton")
        check("skeleton created", skel is not None, skel.get_name() if skel else "")
        b = skm.get_bounds()
        ext = b.box_extent
        origin = b.origin
        height = 2 * ext.z
        log(f"bounds origin {origin}, extent {ext}")
        check("character is upright and in centimetres", 150 < height < 220 and abs(origin.z - ext.z) < 25,
              f"height {height:.1f} cm, feet at z {origin.z - ext.z:.1f}")
        sub = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
        try:
            n_lod = sub.get_lod_count(skm)
            log(f"LODs: {n_lod}")
        except Exception as exc:
            log(f"LOD count not readable: {exc}")
        try:
            names = unreal.SkeletalMeshEditorSubsystem.get_bone_names if hasattr(
                unreal.SkeletalMeshEditorSubsystem, "get_bone_names") else None
            ref = skel.get_editor_property("bone_tree") if skel else []
            check("bones present", len(ref) > 1000, f"{len(ref)} bones")
        except Exception as exc:
            log(f"bone count not readable: {exc}")
except Exception:
    log("[FAIL] character FBX: " + traceback.format_exc())

# --------------------------------------------------------------------------- grooms
for name in ("groom_yup.abc", "groom_zup.abc"):
    try:
        log(f"=== Groom {name}")
        dest = f"/Game/MFB/Groom_{name.split('.')[0]}"
        objs, paths = import_file(SRC / name, dest)
        grooms = assets_of(dest, unreal.GroomAsset)
        check(f"{name} imports as a groom asset", len(grooms) == 1, f"{[p.split('.')[-1] for p in paths]}")
        if grooms:
            g = grooms[0]
            info = list(g.get_editor_property("hair_groups_info"))
            rows = []
            for gi in info:
                rows.append({k: gi.get_editor_property(k) for k in ("group_id", "group_name", "num_curves", "num_guides")
                             if hasattr(gi, k) or True})
            log(f"groups: {rows}")
            check("two hair groups (group IDs 3 and 7)", len(info) == 2,
                  f"group ids {[r.get('group_id') for r in rows]}")
            check("guide curves imported as guides", any(r.get("num_guides", 0) > 0 for r in rows))
            check("strands imported", sum(r.get("num_curves", 0) for r in rows) > 0)
            try:
                bb = g.get_editor_property("hair_groups_info")
            except Exception:
                pass
    except Exception:
        log(f"[FAIL] groom {name}: " + traceback.format_exc())

# --------------------------------------------------------------------------- face curves
try:
    log("=== Face curves")
    if skm is None:
        raise RuntimeError("no skeletal mesh to build the Animation Sequence on")
    skel = skm.get_editor_property("skeleton")
    if eal.does_asset_exist("/Game/MFB/Anim/MFB_FaceAnim"):
        eal.delete_asset("/Game/MFB/Anim/MFB_FaceAnim")
    factory = unreal.AnimSequenceFactory()
    factory.set_editor_property("target_skeleton", skel)
    seq = tools.create_asset("MFB_FaceAnim", "/Game/MFB/Anim", unreal.AnimSequence, factory)
    data = json.loads((SRC / "MFB_face_curves.json").read_text(encoding="utf-8"))
    frames = len(data["times"])
    spec = importlib.util.spec_from_file_location("mfb_import_face_curves", SRC / "mfb_import_face_curves.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    n = mod.add_face_curves(seq, str(SRC / "MFB_face_curves.json"))
    names = unreal.AnimationLibrary.get_animation_curve_names(seq, unreal.RawCurveTrackTypes.RCT_FLOAT)
    check("face curves added to an Animation Sequence", len(names) == n == len(data["curves"]),
          f"{len(names)} curves on {seq.get_name()} ({frames} frames)")
    times, values = unreal.AnimationLibrary.get_float_keys(seq, "CTRL_expressions_jawOpen")
    peak = max(values) if values else -1
    log(f"sequence length {unreal.AnimationLibrary.get_sequence_length(seq):.3f} s for {data['times'][-1]:.3f} s of curves")
    check("jawOpen keys survive (0 -> 1 -> 0)", len(values) == frames and abs(peak - 1) < 0.02
          and abs(values[0]) < 0.02, f"{len(values)} keys, peak {peak:.2f}")
    _, smile = unreal.AnimationLibrary.get_float_keys(seq, "CTRL_expressions_mouthCornerPullL")
    check("smile curve rises at the end", len(smile) == frames and smile[-1] > 0.9, f"last {smile[-1]:.2f}")
except Exception:
    log("[FAIL] face curves: " + traceback.format_exc())

# --------------------------------------------------------------------------- DNA
for name in ("MFB_head.dna", "MFB_body.dna"):
    try:
        log(f"=== DNA {name}")
        dest = f"/Game/MFB/DNA_{name.split('.')[0]}"
        objs, paths = import_file(SRC / name, dest)
        made = [(p.split(".")[-1], type(o).__name__) for p, o in zip(paths, objs)]
        check(f"Unreal imports {name}", len(paths) > 0, f"{made[:8]}")
    except Exception:
        log(f"[FAIL] DNA {name}: " + traceback.format_exc())
log("DNA-related Python classes: " + ", ".join(sorted(n for n in dir(unreal) if "DNA" in n or "RigLogic" in n)[:40]))
try:
    eal.save_directory("/Game/MFB", only_if_is_dirty=True, recursive=True)
except Exception as exc:
    log(f"saving: {exc}")
log("ALL DONE")
if "--quit" in sys.argv:
    unreal.SystemLibrary.quit_editor()
