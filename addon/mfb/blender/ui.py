"""Character Assembler UI: settings, operators and the sidebar panel."""
from __future__ import annotations

import time
from pathlib import Path

import bpy
import bpy.utils.previews
from bpy.props import (BoolProperty, BoolVectorProperty, CollectionProperty, EnumProperty,
                       IntProperty, PointerProperty, StringProperty)

from ..dna.reader import DNAError, read_dna
from . import assemble as asm
from . import rig
from .menu import _prefs

LOD_COUNT = 8
THUMB_SCALE = 5.0           # thumbnail size in icon units (about 100 px)

_previews = None            # bpy.utils.previews collection holding the character pictures
_library = {"dir": None, "entries": [], "items": [], "icons": {}}


class MFB_MeshItem(bpy.types.PropertyGroup):
    name: StringProperty()
    role: StringProperty()
    lod: IntProperty()
    use: BoolProperty(default=True)


def _user_icon() -> int:
    return bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items["USER"].value


def library_entries(s, refresh: bool = False) -> list[asm.LibraryEntry]:
    """Characters in the MetaHumans Directory, cached per folder with their pictures loaded as icons."""
    directory = bpy.path.abspath(s.library) if s.library else ""
    if refresh or directory != _library["dir"]:
        entries = asm.scan_library(directory) if directory else []
        if _previews is not None:
            _previews.clear()
        icons = {}
        for e in entries:
            if e.thumbnail and _previews is not None:
                try:
                    icons[e.path] = _previews.load(e.path, e.thumbnail, "IMAGE").icon_id
                except (KeyError, RuntimeError):
                    pass
        # no icon on the menu items themselves: the grid draws the picture above each name
        items = [(e.path, e.name, e.path, i) for i, e in enumerate(entries)]
        _library.update(dir=directory, entries=entries, items=items, icons=icons)
    return _library["entries"]


def _character_items(self, context):
    library_entries(self)
    return _library["items"]


def _get_library(self) -> str:
    prefs = _prefs()
    return prefs.library if prefs is not None and hasattr(prefs, "library") else self.get("library_dir", "")


def _set_library(self, value: str) -> None:
    prefs = _prefs()
    if prefs is not None and hasattr(prefs, "library"):
        prefs.library = value
    self["library_dir"] = value


def _library_update(self, context):
    entries = library_entries(self, refresh=True)
    if entries:
        self.character = entries[0].path
        select_character(self, entries[0].path)


def _character_update(self, context):
    if self.character:
        select_character(self, self.character)


def _parts_update(self, context):
    refresh_meshes(self)


def select_character(s, path: str) -> None:
    """Fill the head DNA, body DNA, textures and name from a character folder."""
    entry = next((e for e in _library["entries"] if e.path == path), None)
    scan = asm.scan_folder(path)
    s.head_dna, s.body_dna, s.textures = scan.head, scan.body, scan.textures
    s.name = entry.name if entry else Path(path).name
    refresh_meshes(s)


def refresh_meshes(s) -> str:
    """Rebuild the mesh list for the chosen parts and LODs; returns an error message or ''."""
    keep = {m.name: m.use for m in s.meshes}
    s.meshes.clear()
    for role, path in (("head", s.head_dna if s.use_head else ""), ("body", s.body_dna if s.use_body else "")):
        if not path:
            continue
        try:
            dna = read_dna(bpy.path.abspath(path), geometry=False)
        except (OSError, DNAError) as exc:
            return str(exc)
        d = dna.definition
        for lod in range(dna.descriptor.lod_count):
            if lod < LOD_COUNT and s.lods[lod]:
                for mi in d.lod_meshes.for_lod(lod):
                    item = s.meshes.add()
                    item.name, item.role, item.lod = d.mesh_names[int(mi)], role, lod
                    item.use = keep.get(item.name, True)
    return ""


class MFB_AssemblerSettings(bpy.types.PropertyGroup):
    library: StringProperty(name="MetaHumans Directory", subtype="DIR_PATH", get=_get_library, set=_set_library,
                            update=_library_update,
                            description="Folder with your exported MetaHumans, one sub-folder per character "
                                        "(head.dna, body.dna, Maps, <name>.png). Remembered between sessions")
    filter: StringProperty(name="Filter", options={"TEXTEDIT_UPDATE"},
                           description="Show only characters whose name contains this text")
    character: EnumProperty(name="Character", items=_character_items, update=_character_update,
                            description="Character to assemble")
    use_head: BoolProperty(name="Head", default=True, update=_parts_update, description="Build the head")
    use_body: BoolProperty(name="Body", default=True, update=_parts_update, description="Build the body")
    use_textures: BoolProperty(name="Textures", default=True,
                               description="Use the character's textures in the materials")
    show_advanced: BoolProperty(name="Advanced", default=False)
    head_dna: StringProperty(name="Head DNA", subtype="FILE_PATH",
                             description="DNA file with the face rig (MetaHuman 5.6 or later)")
    body_dna: StringProperty(name="Body DNA", subtype="FILE_PATH", description="DNA file with the body rig")
    textures: StringProperty(name="Textures", subtype="DIR_PATH", description="Folder with the character's textures")
    name: StringProperty(name="Name", description="Character name; empty uses the folder name")
    lods: BoolVectorProperty(name="LODs", size=LOD_COUNT, default=(True,) + (False,) * (LOD_COUNT - 1),
                             description="Levels of detail to build; each gets its own collection")
    combine_skeletons: BoolProperty(name="Combine Skeletons", default=False,
                                    description="One armature for head and body. Off: two armatures, "
                                                "the head following the body")
    blend_shapes: BoolProperty(name="Blend Shapes", default=True, description="Build shape keys")
    face_board: BoolProperty(name="Face Board", default=True, description="Create the face board and live RigLogic")
    new_scene: BoolProperty(name="New Scene", default=False, description="Build the character in a new scene")
    materials: BoolProperty(name="Materials", default=True,
                            description="Skin, eye, teeth and lash materials with wrinkle maps, "
                                        "using textures found in the textures folder")
    lights: BoolProperty(name="Light Rig", default=False, description="Add key, fill and rim area lights")
    units: EnumProperty(name="Units", items=(("M", "Metres", "Blender default; 1 unit = 1 m"),
                                             ("CM", "Centimetres", "1 unit = 1 cm, like Unreal")),
                        default="M")
    filter_meshes: BoolProperty(name="Choose Meshes", default=False, description="Pick individual meshes to build")
    meshes: CollectionProperty(type=MFB_MeshItem)
    meshes_index: IntProperty()


def _settings(context) -> MFB_AssemblerSettings:
    return context.scene.mfb_assembler


class MFB_OT_load_folder(bpy.types.Operator):
    """Find the head DNA, body DNA and textures in a MetaHuman export folder"""
    bl_idname = "mfb.load_folder"
    bl_label = "Load MetaHuman Folder"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        s = _settings(context)
        scan = asm.scan_folder(self.directory)
        if not scan.head and not scan.body:
            self.report({"ERROR"}, f"No DNA files found in {self.directory}")
            return {"CANCELLED"}
        s.head_dna, s.body_dna, s.textures = scan.head, scan.body, scan.textures
        s.name = Path(self.directory.rstrip("/\\")).name
        refresh_meshes(s)
        self.report({"INFO"}, f"Found head: {bool(scan.head)}, body: {bool(scan.body)}, "
                              f"textures: {scan.textures or 'none'}")
        return {"FINISHED"}


class MFB_OT_refresh_meshes(bpy.types.Operator):
    """List the meshes in the chosen DNA files and LODs"""
    bl_idname = "mfb.refresh_meshes"
    bl_label = "Refresh Meshes"

    def execute(self, context):
        error = refresh_meshes(_settings(context))
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        return {"FINISHED"}


class MFB_OT_refresh_library(bpy.types.Operator):
    """Look for characters in the MetaHumans Directory again"""
    bl_idname = "mfb.refresh_library"
    bl_label = "Refresh Characters"

    def execute(self, context):
        s = _settings(context)
        entries = library_entries(s, refresh=True)
        if entries and s.character not in {e.path for e in entries}:
            s.character = entries[0].path
            select_character(s, entries[0].path)
        self.report({"INFO"}, f"{len(entries)} characters found")
        return {"FINISHED"}


class MFB_OT_assemble(bpy.types.Operator):
    """Build the character: skeleton, meshes, shape keys, skin weights and face board"""
    bl_idname = "mfb.assemble"
    bl_label = "Assemble"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        return context.mode == "OBJECT" and bool((s.use_head and s.head_dna) or (s.use_body and s.body_dna))

    def execute(self, context):
        s = _settings(context)
        lods = {i for i in range(LOD_COUNT) if s.lods[i]} or {0}
        opts = asm.AssembleOptions(
            head_dna=bpy.path.abspath(s.head_dna) if s.use_head and s.head_dna else "",
            body_dna=bpy.path.abspath(s.body_dna) if s.use_body and s.body_dna else "",
            textures=bpy.path.abspath(s.textures) if s.use_textures and s.textures else "",
            name=s.name, lods=lods,
            mesh_filter={m.name for m in s.meshes if m.use} if s.filter_meshes and len(s.meshes) else None,
            combine_skeletons=s.combine_skeletons, blend_shapes=s.blend_shapes, face_board=s.face_board,
            new_scene=s.new_scene, metres=s.units == "M", materials=s.materials, lights=s.lights)
        t = time.perf_counter()
        try:
            result = asm.assemble(opts)
        except (OSError, DNAError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Assembled {result['collection'].name}: {len(result['meshes'])} meshes "
                              f"in {time.perf_counter() - t:.1f}s")
        return {"FINISHED"}


class MFB_OT_reset_face(bpy.types.Operator):
    """Move every face board control back to neutral"""
    bl_idname = "mfb.reset_face"
    bl_label = "Reset Face"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for obj in bpy.data.objects:
            if obj.type == "ARMATURE" and obj.get(rig.PROP_RIG):
                board = bpy.data.objects.get(obj.get(rig.PROP_BOARD_NAME, ""))
                if board:
                    for pb in board.pose.bones:
                        pb.location = (0.0, 0.0, 0.0)
                rig.update_rig(obj, force=True)
        return {"FINISHED"}


class MFB_OT_key_face(bpy.types.Operator):
    """Insert keys at the current frame for the face board and eye aim (or the raw attributes in Raw mode)"""
    bl_idname = "mfb.key_face"
    bl_label = "Key Face"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        count = 0
        for obj in bpy.data.objects:
            if obj.type == "ARMATURE" and obj.get(rig.PROP_RIG):
                if obj.get(rig.PROP_DRIVE, "BOARD") == "RAW":
                    count += rig.key_face_root(obj)
                else:
                    count += rig.key_face_board(obj)
        self.report({"INFO"}, f"Keyed {count} channels at frame {context.scene.frame_current}")
        return {"FINISHED"}


class MFB_OT_set_drive(bpy.types.Operator):
    """Choose what drives the face: the face board, or the raw control attributes on the face root"""
    bl_idname = "mfb.set_drive"
    bl_label = "Set Face Drive"
    bl_options = {"REGISTER", "UNDO"}

    armature: StringProperty()
    mode: EnumProperty(items=(("BOARD", "Face Board", ""), ("RAW", "Raw Attributes", "")))

    def execute(self, context):
        arm = bpy.data.objects.get(self.armature)
        if arm is None:
            return {"CANCELLED"}
        arm[rig.PROP_DRIVE] = self.mode
        rig.update_rig(arm, force=True)
        return {"FINISHED"}


class MFB_UL_meshes(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "use", text="")
        row.label(text=item.name.replace("_mesh", ""), icon="MESH_DATA")


class MFB_PT_assembler(bpy.types.Panel):
    bl_label = "Character Assembler"
    bl_idname = "MFB_PT_assembler"
    bl_ui_units_x = 18          # width when opened as a floating panel from the menu
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="MetaHumans Directory:")
        col.prop(s, "library", text="")
        row = layout.row(align=True)
        row.prop(s, "filter", text="", icon="VIEWZOOM")
        row.operator("mfb.refresh_library", text="", icon="FILE_REFRESH")
        self.draw_characters(layout.box(), s)

        row = layout.row()
        for prop, path in (("use_head", s.head_dna), ("use_body", s.body_dna), ("use_textures", s.textures)):
            sub = row.row()
            sub.enabled = bool(path)
            sub.prop(s, prop)
        layout.prop(s, "combine_skeletons")

        header, body = layout.panel_prop(s, "show_advanced")
        header.label(text="Advanced")
        if body is not None:
            self.draw_advanced(body, s)

        row = layout.row()
        row.scale_y = 1.5
        row.operator("mfb.assemble", icon="ARMATURE_DATA")

    @staticmethod
    def draw_characters(box, s):
        if not s.library:
            box.label(text="Choose the folder with your exported MetaHumans", icon="INFO")
            return
        entries = library_entries(s)
        text = s.filter.strip().lower()
        shown = [e for e in entries if text in e.name.lower()]
        if not entries:
            box.label(text="No MetaHumans in this folder", icon="ERROR")
            return
        if not shown:
            box.label(text="No names match the filter", icon="VIEWZOOM")
            return
        icons = _library["icons"]
        grid = box.grid_flow(row_major=True, columns=0, even_columns=True, even_rows=True, align=False)
        for e in shown:
            cell = grid.column(align=True)
            cell.template_icon(icon_value=icons.get(e.path) or _user_icon(), scale=THUMB_SCALE)
            cell.prop_enum(s, "character", e.path, text=e.name)

    @staticmethod
    def draw_advanced(layout, s):
        layout.operator("mfb.load_folder", icon="FILE_FOLDER")
        col = layout.column(align=True)
        col.prop(s, "head_dna", text="Head")
        col.prop(s, "body_dna", text="Body")
        col.prop(s, "textures", text="Textures")
        layout.prop(s, "name")

        box = layout.box()
        box.label(text="Levels of detail")
        row = box.row(align=True)
        for i in range(LOD_COUNT):
            row.prop(s, "lods", index=i, text=str(i), toggle=True)
        box.prop(s, "filter_meshes")
        if s.filter_meshes:
            box.operator("mfb.refresh_meshes", icon="FILE_REFRESH")
            box.template_list("MFB_UL_meshes", "", s, "meshes", s, "meshes_index", rows=6)

        col = layout.column(heading="Build")
        col.prop(s, "blend_shapes")
        col.prop(s, "face_board")
        col.prop(s, "materials")
        col.prop(s, "lights")
        col.prop(s, "new_scene")
        layout.prop(s, "units", expand=True)


class MFB_PT_face(bpy.types.Panel):
    bl_label = "Face Rig"
    bl_idname = "MFB_PT_face"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MetaHuman"

    def draw(self, context):
        layout = self.layout
        rigs = [o for o in bpy.data.objects if o.type == "ARMATURE" and o.get(rig.PROP_RIG)]
        if not rigs:
            layout.label(text="No face rig in this file yet")
            return
        for arm in rigs:
            box = layout.box()
            row = box.row()
            row.label(text=arm.name, icon="ARMATURE_DATA")
            row.prop(arm, '["mfb_rig_lod"]', text="LOD")
            mode = arm.get(rig.PROP_DRIVE, "BOARD")
            row = box.row(align=True)
            for value, label in (("BOARD", "Face Board"), ("RAW", "Raw Attributes")):
                op = row.operator("mfb.set_drive", text=label, depress=mode == value)
                op.armature, op.mode = arm.name, value
            board = bpy.data.objects.get(arm.get(rig.PROP_BOARD_NAME, ""))
            aim = bpy.data.objects.get(arm.get(rig.PROP_AIM_NAME, ""))
            col = box.column(align=True)
            if aim is not None:
                col.prop(aim, f'["{rig.PROP_AIM_SWITCH}"]', text="Eyes Look at Aim", slider=True)
                col.prop(aim, f'["{rig.PROP_FOLLOW}"]', text="Aim Follows Head", slider=True)
            if board is not None:
                col.prop(board, f'["{rig.PROP_FOLLOW}"]', text="Board Follows Head", slider=True)
        row = layout.row(align=True)
        row.operator("mfb.key_face", icon="KEY_HLT")
        row.operator("mfb.reset_face", icon="LOOP_BACK")


CLASSES = (MFB_MeshItem, MFB_AssemblerSettings, MFB_OT_load_folder, MFB_OT_refresh_meshes, MFB_OT_refresh_library,
           MFB_OT_assemble,
           MFB_OT_reset_face, MFB_OT_key_face, MFB_OT_set_drive, MFB_UL_meshes, MFB_PT_assembler, MFB_PT_face)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    _library.update(dir=None, entries=[], items=[], icons={})
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mfb_assembler = PointerProperty(type=MFB_AssemblerSettings)


def unregister():
    global _previews
    del bpy.types.Scene.mfb_assembler
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
