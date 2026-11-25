"""Groom Exporter panel: list hair objects, set groups and guides, export, presets."""
from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import export as gexport
from . import presets


def _is_hair(_self, obj):
    return obj.type == "CURVES"


class MFB_GroomItem(bpy.types.PropertyGroup):
    name: StringProperty()
    obj: PointerProperty(type=bpy.types.Object, poll=_is_hair, name="Hair")
    include: BoolProperty(name="Export", default=True, description="Include this hair in the export")
    group_id: IntProperty(name="Group ID", default=0, min=0,
                          description="Unreal groom group (each group can get its own material)")
    group_name: StringProperty(name="Group Name", description="Name for this group, kept in presets and "
                                                             "in the exported object name")
    guides: BoolProperty(name="Guides", default=False,
                         description="Export these curves as simulation guides (groom_guide = 1)")


class MFB_GroomSettings(bpy.types.PropertyGroup):
    items: CollectionProperty(type=MFB_GroomItem)
    index: IntProperty()
    filepath: StringProperty(name="File", subtype="FILE_PATH", default="",
                             description="Last exported Alembic file")
    up_axis: EnumProperty(
        name="Up Axis",
        items=(("Y", "Y Up", "Alembic's usual Y-up. Matches Unreal's default groom import settings "
                             "(Rotation X 90, Scale Y -1)"),
               ("Z", "Z Up", "Keep Blender's Z-up. In Unreal set the groom import Rotation to 0")),
        default="Y")
    unit_scale: FloatProperty(name="Scale", default=100.0, min=0.0001,
                              description="Multiplier from Blender units to the file. 100 turns metres "
                                          "into Unreal's centimetres")
    export_root_uv: BoolProperty(name="Root UVs", default=True,
                                 description="groom_root_uv from the hair's surface UVs")
    export_color: BoolProperty(name="Colour", default=True,
                               description="groom_color from a 'color' attribute on the hair, if present")
    export_ids: BoolProperty(name="Strand IDs", default=True, description="groom_id per strand")
    default_radius_mm: FloatProperty(name="Default Width (mm)", default=0.1, min=0.0,
                                     description="Strand width when the hair has no radius attribute")


def _settings(context) -> MFB_GroomSettings:
    return context.scene.mfb_groom


def refresh(context) -> None:
    s = _settings(context)
    hair = [o for o in context.scene.objects if o.type == "CURVES"]
    keep = {it.obj.name: (it.include, it.group_id, it.group_name, it.guides) for it in s.items if it.obj}
    s.items.clear()
    for o in hair:
        it = s.items.add()
        it.obj, it.name = o, o.name
        if o.name in keep:
            it.include, it.group_id, it.group_name, it.guides = keep[o.name]
        else:
            it.group_name = o.name
            it.guides = "guide" in o.name.lower()
    s.index = min(s.index, max(len(s.items) - 1, 0))


class MFB_OT_groom_refresh(bpy.types.Operator):
    """Find the hair (Curves) objects in the scene"""
    bl_idname = "mfb.groom_refresh"
    bl_label = "Refresh"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        refresh(context)
        self.report({"INFO"}, f"{len(_settings(context).items)} hair objects")
        return {"FINISHED"}


class MFB_OT_groom_select(bpy.types.Operator):
    """Include every hair object in the export, or none"""
    bl_idname = "mfb.groom_select"
    bl_label = "Select"
    bl_options = {"REGISTER", "UNDO"}

    state: BoolProperty(default=True)

    def execute(self, context):
        for it in _settings(context).items:
            it.include = self.state
            if it.obj and it.obj.name in context.view_layer.objects:
                it.obj.select_set(self.state)
        return {"FINISHED"}


class MFB_OT_groom_set_group(bpy.types.Operator):
    """Set the group ID (and name) of every included hair object"""
    bl_idname = "mfb.groom_set_group"
    bl_label = "Set Group ID"
    bl_options = {"REGISTER", "UNDO"}

    group_id: IntProperty(name="Group ID", default=0, min=0)
    group_name: StringProperty(name="Group Name")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        n = 0
        for it in _settings(context).items:
            if it.include:
                it.group_id = self.group_id
                if self.group_name:
                    it.group_name = self.group_name
                n += 1
        self.report({"INFO"}, f"Group {self.group_id} set on {n} hair objects")
        return {"FINISHED"}


class MFB_OT_groom_toggle_guides(bpy.types.Operator):
    """Toggle 'export as guides' on the active hair in the list"""
    bl_idname = "mfb.groom_toggle_guides"
    bl_label = "Toggle Guides"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = _settings(context)
        if not s.items:
            return {"CANCELLED"}
        it = s.items[s.index]
        it.guides = not it.guides
        return {"FINISHED"}


def entries_from_settings(s: MFB_GroomSettings) -> list[gexport.GroomEntry]:
    return [gexport.GroomEntry(it.obj, it.group_id, it.group_name, it.guides)
            for it in s.items if it.include and it.obj is not None]


def options_from_settings(s: MFB_GroomSettings) -> gexport.ExportOptions:
    return gexport.ExportOptions(up_axis=s.up_axis, scale=s.unit_scale, root_uv=s.export_root_uv,
                                 color=s.export_color, strand_ids=s.export_ids,
                                 default_radius=s.default_radius_mm / 1000.0)


class MFB_OT_groom_export(bpy.types.Operator, ExportHelper):
    """Export the included hair as an Alembic groom for Unreal Engine"""
    bl_idname = "mfb.groom_export"
    bl_label = "Export Groom"
    filename_ext = ".abc"
    filter_glob: StringProperty(default="*.abc", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = bpy.path.abspath(_settings(context).filepath or "//groom.abc")
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        s = _settings(context)
        if not s.items:
            refresh(context)
        s.filepath = self.filepath
        try:
            rep = gexport.export_grooms(context, entries_from_settings(s), self.filepath, options_from_settings(s))
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for w in rep.warnings:
            self.report({"WARNING"}, w)
        self.report({"INFO"}, f"Exported {rep.strands} strands in {len(rep.grooms)} grooms to "
                              f"{Path(self.filepath).name}")
        return {"FINISHED"}


class MFB_OT_groom_preset_save(bpy.types.Operator, ExportHelper):
    """Save the groom list settings and export options as a preset"""
    bl_idname = "mfb.groom_preset_save"
    bl_label = "Save Preset"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        presets.save_preset(_settings(context), self.filepath)
        self.report({"INFO"}, f"Saved {Path(self.filepath).name}")
        return {"FINISHED"}


class MFB_OT_groom_preset_load(bpy.types.Operator, ImportHelper):
    """Open a groom preset and apply it to the hair in this scene (matched by object name)"""
    bl_idname = "mfb.groom_preset_load"
    bl_label = "Open Preset"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        s = _settings(context)
        if not s.items:
            refresh(context)
        matched, missing = presets.load_preset(s, self.filepath, context.scene.objects)
        if missing:
            self.report({"WARNING"}, f"Not in this scene: {', '.join(missing)}")
        self.report({"INFO"}, f"Applied preset to {matched} hair objects")
        return {"FINISHED"}


class MFB_UL_grooms(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "include", text="")
        row.label(text=item.obj.name if item.obj else f"{item.name} (missing)", icon="CURVES_DATA")
        row.prop(item, "group_id", text="")
        row.prop(item, "guides", text="", icon="FORCE_CURVE" if item.guides else "STRANDS", toggle=True)


class MFB_PT_groom(bpy.types.Panel):
    bl_label = "Groom Exporter"
    bl_idname = "MFB_PT_groom"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        row = layout.row(align=True)
        row.operator("mfb.groom_preset_load", text="Open", icon="FILE_FOLDER")
        row.operator("mfb.groom_preset_save", text="Save", icon="FILE_TICK")
        row = layout.row(align=True)
        row.operator("mfb.groom_refresh", icon="FILE_REFRESH")
        op = row.operator("mfb.groom_select", text="All")
        op.state = True
        op = row.operator("mfb.groom_select", text="None")
        op.state = False
        layout.template_list("MFB_UL_grooms", "", s, "items", s, "index", rows=4)
        if s.items and 0 <= s.index < len(s.items):
            it = s.items[s.index]
            box = layout.box()
            box.prop(it, "group_id")
            box.prop(it, "group_name")
            box.prop(it, "guides")
        row = layout.row(align=True)
        row.operator("mfb.groom_set_group", icon="GROUP")
        row.operator("mfb.groom_toggle_guides", icon="FORCE_CURVE")
        col = layout.column(heading="Export")
        col.prop(s, "export_root_uv")
        col.prop(s, "export_color")
        col.prop(s, "export_ids")
        col.prop(s, "default_radius_mm")
        layout.prop(s, "up_axis", expand=True)
        layout.prop(s, "unit_scale")
        layout.prop(s, "filepath")
        layout.operator("mfb.groom_export", icon="EXPORT")


CLASSES = (MFB_GroomItem, MFB_GroomSettings, MFB_OT_groom_refresh, MFB_OT_groom_select, MFB_OT_groom_set_group,
           MFB_OT_groom_toggle_guides, MFB_OT_groom_export, MFB_OT_groom_preset_save, MFB_OT_groom_preset_load,
           MFB_UL_grooms, MFB_PT_groom)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mfb_groom = PointerProperty(type=MFB_GroomSettings)


def unregister():
    del bpy.types.Scene.mfb_groom
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
