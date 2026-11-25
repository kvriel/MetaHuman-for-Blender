"""Write the archetype body as FBX with different scale settings, to see which import at the right
size in Unreal with both importers (Interchange and legacy FbxImportUI).

blender -b --factory-startup --python tests/ue_scale_variants.py -- <SKM_Body.dna> <out_dir>
"""
import shutil
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
import mfb  # noqa: E402
from mfb.blender.assemble import AssembleOptions, assemble  # noqa: E402

body, out = sys.argv[sys.argv.index("--") + 1:][:2]
out = Path(out); out.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
mfb.register()
res = assemble(AssembleOptions(body_dna=body, name="Body", materials=False, body_rig=False))
arm = res["armatures"]["body"]
objs = [arm, *[o for o in arm.children if o.type == "MESH"]]
base = dict(use_selection=True, object_types={"ARMATURE", "MESH"}, add_leaf_bones=False, primary_bone_axis="Y",
            secondary_bone_axis="X", use_armature_deform_only=False, mesh_smooth_type="FACE", bake_anim=False)
variants = {
    "A_units": dict(apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS"),     # current
    "B_all": dict(apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL"),
    "C_none": dict(apply_unit_scale=True, apply_scale_options="FBX_SCALE_NONE"),
    "D_none_bake": dict(apply_unit_scale=True, apply_scale_options="FBX_SCALE_NONE", bake_space_transform=True),
}
for name, extra in variants.items():
    with bpy.context.temp_override(selected_objects=objs, active_object=arm):
        bpy.ops.export_scene.fbx(filepath=str(out / f"{name}.fbx"), **base, **extra)
    print("WROTE", name)

# E: the add-on's own Export to Unreal character FBX (centimetre copy, "Armature" wrapper node)
from mfb.blender import export_ue  # noqa: E402
n_obj, n_mesh, n_arm = len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.armatures)
arm_name = arm.name
export_ue.export_character_fbx(out / "E_addon_body.fbx", arm, [o for o in arm.children if o.type == "MESH"])
unchanged = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.armatures)) == (n_obj, n_mesh, n_arm) \
    and arm.name == arm_name and abs(arm.data.bones["pelvis"].head_local.z - 0.87) < 0.05
print("SCENE UNCHANGED" if unchanged else "SCENE CHANGED", n_obj, len(bpy.data.objects), arm.data.bones["pelvis"].head_local.z)
print("WROTE E_addon_body")
face = body.replace("SKM_Body.dna", "SKM_Face.dna")
res2 = assemble(AssembleOptions(head_dna=face, body_dna=body, name="Full", materials=False, face_board=False,
                                body_rig=False))
arm2 = res2["armatures"]["head"]
export_ue.export_character_fbx(out / "E_addon_full.fbx", arm2, [o for o in arm2.children if o.type == "MESH"])
print("WROTE E_addon_full")
