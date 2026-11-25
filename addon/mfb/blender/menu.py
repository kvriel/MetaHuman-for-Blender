"""Top-bar "MetaHuman" menu, About, licence, update check, environment checks, preferences."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import bpy
from bpy.props import StringProperty

ADDON_DIR = Path(__file__).resolve().parents[1]
PACKAGE = __package__.rsplit(".", 1)[0]          # "mfb" or "bl_ext.<repo>.mfb"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
MIN_BLENDER = (4, 2, 0)
# add-ons known to register their own live MetaHuman/DNA rigs; running two live rigs on one armature fights
CONFLICTING_ADDONS = ("meta_human_dna", "character_dna", "metahuman_dna")

TOOLS = (("MFB_PT_assembler", "Character Assembler", "ARMATURE_DATA"),
         ("MFB_PT_expression", "Expression Editor", "SHAPEKEY_DATA"),
         ("MFB_PT_pose", "Pose Editor", "POSE_HLT"),
         ("MFB_PT_groom", "Groom Exporter", "CURVES_DATA"))


def addon_version() -> str:
    manifest = ADDON_DIR / "blender_manifest.toml"
    for line in manifest.read_text(encoding="utf-8").splitlines() if manifest.exists() else []:
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "0.0.0"


def environment_report() -> list[tuple[str, bool, str]]:
    """(check, ok, detail) rows: Blender version, numpy, bundled data, conflicting add-ons, platform."""
    rows = []
    rows.append(("Blender version", bpy.app.version >= MIN_BLENDER,
                 f"{bpy.app.version_string} (needs {'.'.join(map(str, MIN_BLENDER))} or later)"))
    try:
        import numpy
        rows.append(("numpy", True, numpy.__version__))
    except ImportError:
        rows.append(("numpy", False, "not found; Blender normally bundles it"))
    data = ADDON_DIR / "data" / "wrinkle_masks.json"
    rows.append(("Add-on data files", data.exists(), data.name))
    enabled = {a.module for a in bpy.context.preferences.addons}
    clash = sorted(m for m in enabled if any(k in m.lower() for k in CONFLICTING_ADDONS))
    rows.append(("No conflicting DNA add-ons", not clash,
                 ", ".join(clash) + " is enabled; disable it for characters built here" if clash else "none enabled"))
    rows.append(("Platform", True, f"{sys.platform}, Python {sys.version.split()[0]} (pure Python, no binaries)"))
    return rows


def _prefs():
    addon = bpy.context.preferences.addons.get(PACKAGE)
    return addon.preferences if addon else None


class MFB_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE

    update_url: StringProperty(
        name="Update URL",
        description="JSON file with {\"version\": \"x.y.z\", \"url\": \"...\"} describing the latest release. "
                    "Leave empty when the extension comes from a Blender extensions repository, "
                    "which updates through Get Extensions")
    library: StringProperty(
        name="MetaHumans Directory", subtype="DIR_PATH",
        description="Folder with your exported MetaHumans (one sub-folder per character); "
                    "the Character Assembler remembers it between sessions")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "library")
        layout.prop(self, "update_url")
        box = layout.box()
        box.label(text="Environment")
        for name, ok, detail in environment_report():
            row = box.row()
            row.label(text=name, icon="CHECKMARK" if ok else "ERROR")
            row.label(text=detail)


class MFB_OT_open_tool(bpy.types.Operator):
    """Open a MetaHuman for Blender tool as a floating panel"""
    bl_idname = "mfb.open_tool"
    bl_label = "Open Tool"

    panel: StringProperty()

    def execute(self, context):
        if not hasattr(bpy.types, self.panel):
            self.report({"ERROR"}, f"{self.panel} is not available")
            return {"CANCELLED"}
        bpy.ops.wm.call_panel("INVOKE_DEFAULT", name=self.panel, keep_open=True)
        return {"FINISHED"}


class MFB_OT_check_updates(bpy.types.Operator):
    """Look for a newer version of the add-on (needs Allow Online Access in Preferences > System)"""
    bl_idname = "mfb.check_updates"
    bl_label = "Check for Updates"

    def execute(self, context):
        current = addon_version()
        prefs = _prefs()
        url = prefs.update_url.strip() if prefs else ""
        if not url:
            self.report({"INFO"}, f"Version {current}. No update URL set: extensions installed from a "
                                  "repository update through Edit > Preferences > Get Extensions")
            return {"FINISHED"}
        if not bpy.app.online_access:
            self.report({"WARNING"}, "Online access is off (Preferences > System > Network). Nothing was checked")
            return {"CANCELLED"}
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                info = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read {url}: {exc}")
            return {"CANCELLED"}
        latest = str(info.get("version", ""))
        newer = _version_tuple(latest) > _version_tuple(current)
        self.report({"INFO"}, f"Version {latest} is available: {info.get('url', '')}" if newer
                    else f"Version {current} is up to date")
        return {"FINISHED"}


def _version_tuple(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


class MFB_OT_about(bpy.types.Operator):
    """Version, environment checks and where to find help"""
    bl_idname = "mfb.about"
    bl_label = "About MetaHuman for Blender"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"MetaHuman for Blender {addon_version()}", icon="INFO")
        layout.label(text="Assemble, rig, edit and export DNA-based digital humans in Blender.")
        col = layout.column(align=True)
        for name, ok, detail in environment_report():
            row = col.row()
            row.label(text=name, icon="CHECKMARK" if ok else "ERROR")
            row.label(text=detail)
        layout.separator()
        layout.operator("mfb.copy_about", icon="COPYDOWN")

    def execute(self, context):
        return {"FINISHED"}


class MFB_OT_copy_about(bpy.types.Operator):
    """Copy version and environment details to the clipboard, for support requests"""
    bl_idname = "mfb.copy_about"
    bl_label = "Copy Details"

    def execute(self, context):
        lines = [f"MetaHuman for Blender {addon_version()}"] + [
            f"{name}: {'ok' if ok else 'PROBLEM'} - {detail}" for name, ok, detail in environment_report()]
        context.window_manager.clipboard = "\n".join(lines)
        self.report({"INFO"}, "Details copied")
        return {"FINISHED"}


class MFB_OT_open_doc(bpy.types.Operator):
    """Open a document that ships with the add-on"""
    bl_idname = "mfb.open_doc"
    bl_label = "Open Document"

    doc: StringProperty()

    def execute(self, context):
        path = ADDON_DIR / self.doc
        if not path.exists():
            self.report({"ERROR"}, f"{self.doc} is missing from the add-on")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=str(path))
        return {"FINISHED"}


class MFB_MT_main(bpy.types.Menu):
    bl_idname = "MFB_MT_main"
    bl_label = "MetaHuman"

    def draw(self, context):
        layout = self.layout
        for panel, label, icon in TOOLS:
            op = layout.operator("mfb.open_tool", text=label, icon=icon)
            op.panel = panel
        layout.separator()
        layout.operator("mfb.open_doc", text="Documentation", icon="HELP").doc = "docs/GUIDE.md"
        layout.operator("wm.url_open", text="View Licence (GPL-3.0)", icon="TEXT").url = LICENSE_URL
        layout.operator("mfb.check_updates", icon="FILE_REFRESH")
        layout.separator()
        layout.operator("mfb.about", icon="INFO")
        layout.label(text=f"Version {addon_version()}")


def _draw_topbar(self, context):
    self.layout.menu("MFB_MT_main")


CLASSES = (MFB_AddonPreferences, MFB_OT_open_tool, MFB_OT_check_updates, MFB_OT_about, MFB_OT_copy_about,
           MFB_OT_open_doc, MFB_MT_main)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_editor_menus.append(_draw_topbar)
    problems = [f"{n}: {d}" for n, ok, d in environment_report() if not ok]
    if problems:
        print("[mfb] environment check:", "; ".join(problems))


def unregister():
    bpy.types.TOPBAR_MT_editor_menus.remove(_draw_topbar)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
