"""Export to Unreal Engine: DNA files, the character as FBX, and the face animation curves.

Face animation: Unreal's MetaHuman face reads animation curves named after the raw controls
(CTRL_expressions.jawOpen -> CTRL_expressions_jawOpen). Blender 5.3's FBX exporter does not
carry animated shape-key weights reliably, so the curves are baked to a JSON file, and an
Unreal editor script (ue/mfb_import_face_curves.py, written next to it) adds them to an
Animation Sequence.
"""
from __future__ import annotations

import json
import shutil

from pathlib import Path

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from ..dna.reader import read_dna
from ..dna.writer import write_dna
from . import rig



def ue_curve_name(raw_name: str) -> str:
    return raw_name.replace(".", "_")


def morph_target_name(mesh_name: str, channel_name: str) -> str:
    """Unreal's RigLogic finds a DNA blend shape's morph target by this name (DNAToSkelMeshMap)."""
    return f"{mesh_name}__{channel_name}"


def _shape_names(obj, cache: dict) -> dict[str, str]:
    """{shape key name: Unreal morph target name} for the keys that come from a DNA blend shape."""
    from .build import PROP_DNA_PATH, PROP_MESH_INDEX, SHAPE_CHANNELS
    channels = obj.get(SHAPE_CHANNELS)
    path = obj.get(PROP_DNA_PATH)
    if not channels or not path:
        return {}
    if path not in cache:
        try:
            cache[path] = read_dna(path, geometry=False, blend_shapes=False).definition
        except Exception:
            cache[path] = None
    d = cache[path]
    if d is None:
        return {}
    mi = obj.get(PROP_MESH_INDEX, -1)
    mesh_name = d.mesh_names[mi] if 0 <= mi < len(d.mesh_names) else obj.data.name.split(".")[0]
    return {key: morph_target_name(mesh_name, d.blend_shape_channel_names[ch])
            for key, ch in channels.items() if 0 <= ch < len(d.blend_shape_channel_names)}


def export_character_fbx(path: Path, armature, meshes) -> None:
    """Write the character as Unreal expects it: data in centimetres, no scale on any node, and the
    armature node named "Armature" (Unreal's FBX importers drop that wrapper, so the DNA's own root
    joint becomes the root bone). Works on temporary copies, so the scene is left untouched.

    Shape keys from DNA blend shapes become morph targets named "<mesh>__<channel>", the names
    Unreal's RigLogic looks for. Many are longer than Blender's 63-byte limit, so the copies get
    short placeholder names and the full names are written into the FBX file afterwards.
    """
    from ..core.fbx_names import rename_objects
    dna_cache: dict = {}
    full_names: dict[str, str] = {}
    from mathutils import Matrix

    from .ctx import window_override
    scene = bpy.context.scene
    unit_keep = scene.unit_settings.scale_length
    cm_per_unit = 100.0 * unit_keep                                  # 100 for metres, 1 for centimetres
    scale = Matrix.Scale(cm_per_unit, 4)
    coll = bpy.data.collections.new("_mfb_unreal_export")
    scene.collection.children.link(coll)
    taken = bpy.data.objects.get("Armature")
    if taken is not None:
        taken.name = "Armature_mfb_hold"
    copies, datas = [], []
    try:
        arm = armature.copy()
        arm.data = armature.data.copy()
        datas.append(arm.data)
        arm.animation_data_clear()
        for pb in arm.pose.bones:                                    # rest pose, no live-rig constraints
            for c in list(pb.constraints):
                pb.constraints.remove(c)
            pb.matrix_basis = Matrix()
        arm.data.transform(scale)
        arm.matrix_world = Matrix.LocRotScale(armature.matrix_world.translation * cm_per_unit,
                                              armature.matrix_world.to_quaternion(), None)
        arm.name = "Armature"
        for key in list(arm.keys()):
            del arm[key]
        coll.objects.link(arm)
        copies.append(arm)
        for src in meshes:
            ob = src.copy()
            ob.data = src.data.copy()
            datas.append(ob.data)
            ob.animation_data_clear()
            if ob.data.shape_keys:
                ob.data.shape_keys.animation_data_clear()
                names = _shape_names(src, dna_cache)
                for kb in ob.data.shape_keys.key_blocks[1:]:
                    if kb.name in names:
                        placeholder = f"mfbmorph{len(full_names):05d}"
                        full_names[placeholder] = names[kb.name]
                        kb.name = placeholder
            ob.data.transform(src.matrix_world @ Matrix(), shape_keys=True)    # bake object transform
            ob.data.transform(scale, shape_keys=True)
            ob.parent = arm
            ob.matrix_parent_inverse = arm.matrix_world.inverted()
            ob.matrix_basis = Matrix()
            for m in ob.modifiers:
                if m.type == "ARMATURE":
                    m.object = arm
            coll.objects.link(ob)
            copies.append(ob)
        bpy.context.view_layer.update()
        # the copies are in centimetres; tell the exporter so (1 unit = 1 cm), so the file's unit is cm
        # and no conversion scale lands on the root bone
        scene.unit_settings.scale_length = 0.01
        with bpy.context.temp_override(**window_override(), selected_objects=copies, active_object=arm):
            bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, object_types={"ARMATURE", "MESH"},
                                     apply_unit_scale=True, global_scale=1.0, apply_scale_options="FBX_SCALE_NONE",
                                     add_leaf_bones=False, primary_bone_axis="Y", secondary_bone_axis="X",
                                     use_armature_deform_only=False, mesh_smooth_type="FACE", bake_anim=False,
                                     use_mesh_modifiers=False)
        if full_names:
            rename_objects(path, full_names)
    finally:
        scene.unit_settings.scale_length = unit_keep
        for ob in copies:
            bpy.data.objects.remove(ob)
        for d in datas:
            (bpy.data.armatures if isinstance(d, bpy.types.Armature) else bpy.data.meshes).remove(d)
        bpy.data.collections.remove(coll)
        if taken is not None:
            taken.name = "Armature"


def bake_face_curves(arm, frame_start: int, frame_end: int) -> dict:
    """Raw face control values on every frame, with Unreal curve names."""
    rt = rig.runtime(arm, "head")
    names = [n for n in rt.dna.definition.raw_control_names if n.startswith("CTRL_expressions.")]
    root = arm.pose.bones.get(rig.FACE_ROOT)
    scene = bpy.context.scene
    keep = scene.frame_current
    curves = {ue_curve_name(n): [] for n in names}
    for f in range(frame_start, frame_end + 1):
        scene.frame_set(f)
        rig.update_rig(arm, force=True)
        for n in names:
            curves[ue_curve_name(n)].append(round(float(root.get(rig.raw_attr_name(n), 0.0)) if root else 0.0, 6))
    scene.frame_set(keep)
    fps = scene.render.fps / scene.render.fps_base
    return {"fps": fps, "frame_start": frame_start, "frame_end": frame_end,
            "times": [round((f - frame_start) / fps, 6) for f in range(frame_start, frame_end + 1)],
            "curves": curves}


UE_SCRIPT = Path(__file__).resolve().parents[1] / "ue" / "mfb_import_face_curves.py"


class MFB_OT_export_unreal(bpy.types.Operator):
    """Export DNA, the character FBX and the face animation FBX for Unreal Engine"""
    bl_idname = "mfb.export_unreal"
    bl_label = "Export to Unreal"
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH")
    armature: StringProperty()
    character: BoolProperty(name="Character FBX", default=True)
    animation: BoolProperty(name="Face Animation Curves", default=True)
    dna: BoolProperty(name="DNA Files", default=True)
    frame_start: IntProperty(name="Start", default=-1)
    frame_end: IntProperty(name="End", default=-1)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        arm = bpy.data.objects.get(self.armature) or next(
            (o for o in bpy.data.objects if o.type == "ARMATURE" and o.get(rig.PROP_RIG)), None)
        if arm is None:
            self.report({"ERROR"}, "No assembled character found")
            return {"CANCELLED"}
        out = Path(bpy.path.abspath(self.directory))
        out.mkdir(parents=True, exist_ok=True)
        name = arm.name.removesuffix("_head_rig").removesuffix("_body_rig").removesuffix("_rig")
        written = []
        if self.dna:
            for kind, prop in (("head", "mfb_head_dna"), ("body", rig.PROP_BODY_DNA)):
                if arm.get(prop):
                    p = out / f"{name}_{kind}.dna"
                    write_dna(read_dna(arm[prop]), p)
                    written.append(p.name)
        meshes = [o for o in arm.children if o.type == "MESH"]
        if self.character:
            p = out / f"{name}.fbx"
            export_character_fbx(p, arm, meshes)
            written.append(p.name)
        if self.animation and arm.get("mfb_head_dna"):
            scene = context.scene
            fs = self.frame_start if self.frame_start >= 0 else scene.frame_start
            fe = self.frame_end if self.frame_end >= 0 else scene.frame_end
            data = bake_face_curves(arm, fs, fe)
            p = out / f"{name}_face_curves.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            shutil.copy(UE_SCRIPT, out / UE_SCRIPT.name)
            written += [p.name, UE_SCRIPT.name]
        self.report({"INFO"}, f"Wrote {', '.join(written)} to {out}")
        return {"FINISHED"}


class MFB_PT_export(bpy.types.Panel):
    bl_label = "Export to Unreal"
    bl_idname = "MFB_PT_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="Writes DNA, character FBX (cm), face")
        layout.label(text="curves (JSON) and an Unreal import script.")
        layout.operator("mfb.export_unreal", icon="EXPORT")


CLASSES = (MFB_OT_export_unreal, MFB_PT_export)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
