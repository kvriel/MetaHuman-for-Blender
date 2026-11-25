"""Runs inside Unreal Editor: import FBX scale variants with both importers, and the grooms.

UnrealEditor.exe <project> -ExecutePythonScript="tests/ue_scale_test.py <scale_dir> <groom_dir> --quit"
"""
import sys
import traceback
from pathlib import Path

import unreal

args = [a for a in sys.argv[1:] if not a.startswith("--")]
SCALE, GROOMS = Path(args[0]), Path(args[1])
OUT = SCALE / "ue_scale_results.txt"
OUT.write_text("", encoding="utf-8")
tools = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary


def log(msg):
    unreal.log(f"[MFBTEST] {msg}")
    with OUT.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run_import(path, dest, options=None):
    if eal.does_directory_exist(dest):
        eal.delete_directory(dest)
    task = unreal.AssetImportTask()
    for k, v in (("filename", str(path)), ("destination_path", dest), ("automated", True), ("save", False),
                 ("replace_existing", True)):
        task.set_editor_property(k, v)
    if options is not None:
        task.set_editor_property("options", options)
    tools.import_asset_tasks([task])
    return [eal.load_asset(p) for p in task.get_editor_property("imported_object_paths") or []]


def legacy_options():
    ui = unreal.FbxImportUI()
    ui.set_editor_property("import_mesh", True)
    ui.set_editor_property("import_as_skeletal", True)
    ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
    ui.set_editor_property("import_animations", False)
    ui.set_editor_property("import_materials", False)
    ui.set_editor_property("import_textures", False)
    ui.set_editor_property("create_physics_asset", False)
    return ui


ONLY = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), "")
for fbx in sorted(p for p in SCALE.glob("*.fbx") if p.stem.startswith(ONLY)):
    for importer, opts in (("interchange", None), ("FbxImportUI", "legacy"), ("classic (FBX flag off)", "classic")):
        try:
            if opts == "classic":
                unreal.SystemLibrary.execute_console_command(None, "Interchange.FeatureFlags.Import.FBX False")
            objs = run_import(fbx, f"/Game/Scale/{fbx.stem}_{importer.split()[0]}",
                              legacy_options() if opts else None)
            if opts == "classic":
                unreal.SystemLibrary.execute_console_command(None, "Interchange.FeatureFlags.Import.FBX True")
            skms = [o for o in objs if isinstance(o, unreal.SkeletalMesh)]
            if not skms:
                log(f"{fbx.stem:12} {importer:20} no skeletal mesh ({[type(o).__name__ for o in objs]})")
                continue
            b = skms[0].get_bounds()
            h = 2 * b.box_extent.z
            feet = b.origin.z - b.box_extent.z
            bones = len(skms[0].get_editor_property("skeleton").get_editor_property("bone_tree"))
            extra = ""
            try:
                skel = skms[0].get_editor_property("skeleton")
                pose = unreal.AnimPoseExtensions.get_reference_pose(skel)
                names = list(unreal.AnimPoseExtensions.get_bone_names(pose))
                root = names[0]
                rt = unreal.AnimPoseExtensions.get_bone_pose(pose, root, unreal.AnimPoseSpaces.LOCAL)
                pel = unreal.AnimPoseExtensions.get_bone_pose(pose, "pelvis", unreal.AnimPoseSpaces.WORLD)
                root_scale = rt.scale3d.x
                pelvis_z = pel.translation.z
                extra = f", root bone '{root}' scale {root_scale:.2f}, pelvis z {pelvis_z:.1f}"
                ok_pose = abs(root_scale - 1) < 1e-3 and 80 < pelvis_z < 110 and root == "root"
            except Exception as exc:
                extra, ok_pose = f", pose not readable ({exc})", False
            ok = 120 < h < 220 and ok_pose     # body only (no head): about 131 cm
            log(f"[{'PASS' if ok else 'FAIL'}] {fbx.stem:12} {importer:20} height {h:8.2f} cm, feet z {feet:7.2f}, "
                f"{bones} bones{extra}")
        except Exception:
            log(f"[FAIL] {fbx.stem} {importer}: {traceback.format_exc()}")

for abc in sorted(GROOMS.glob("groom_*.abc")) if "--grooms" in sys.argv else []:
    try:
        objs = run_import(abc, f"/Game/Grooms/{abc.stem}")
        grooms = [o for o in objs if isinstance(o, unreal.GroomAsset)]
        if not grooms:
            log(f"[FAIL] {abc.name}: no groom asset")
            continue
        info = list(grooms[0].get_editor_property("hair_groups_info"))
        rows = [(gi.get_editor_property("group_id"), gi.get_editor_property("num_curves"),
                 gi.get_editor_property("num_guides")) for gi in info]
        ok = len(rows) == 1 and rows[0][1] == 5 and rows[0][2] == 3
        log(f"[{'PASS' if ok else 'FAIL'}] {abc.name}: groom asset, groups (id, strands, guides) {rows}")
    except Exception:
        log(f"[FAIL] {abc.name}: {traceback.format_exc()}")
log("ALL DONE")
if "--quit" in sys.argv:
    unreal.SystemLibrary.quit_editor()

