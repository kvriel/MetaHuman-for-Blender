"""Character Assembler: turn a head DNA and/or body DNA into a ready Blender character."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from ..core.space import Space
from ..dna.reader import DNA, read_dna
from . import materials, rig
from .build import (PROP_DNA_PATH, PROP_ROLE, BuildOptions, build_armature, build_combined_armature,
                    build_mesh, ensure_collection, follow_bones)

PROP_HEAD_DNA = "mfb_head_dna"
PROP_TEXTURES = "mfb_textures"
IMAGE_EXTS = {".png", ".tga", ".tif", ".tiff", ".exr", ".jpg", ".jpeg", ".dds"}


@dataclass
class AssembleOptions:
    head_dna: str = ""
    body_dna: str = ""
    textures: str = ""
    name: str = ""
    lods: set[int] = field(default_factory=lambda: {0})
    mesh_filter: set[str] | None = None          # mesh names; None = every mesh in the chosen LODs
    combine_skeletons: bool = True
    blend_shapes: bool = True
    face_board: bool = True
    new_scene: bool = False
    metres: bool = True
    materials: bool = True
    lights: bool = False
    body_rig: bool = True


@dataclass
class FolderScan:
    head: str = ""
    body: str = ""
    textures: str = ""
    found: list[str] = field(default_factory=list)


@dataclass
class LibraryEntry:
    name: str
    path: str
    thumbnail: str = ""


EXPORT_MANIFEST = "exportmanifest.json"


def _library_entry(folder: Path) -> LibraryEntry | None:
    """A character folder has DNA files or a MetaHuman Creator ExportManifest.json at its top level."""
    try:
        files = {f.name.lower(): f for f in folder.iterdir() if f.is_file()}
    except OSError:
        return None
    if EXPORT_MANIFEST not in files and not any(n.endswith(".dna") for n in files):
        return None
    name = folder.name
    if EXPORT_MANIFEST in files:
        try:
            name = json.loads(files[EXPORT_MANIFEST].read_text(encoding="utf-8")).get("metaHumanName") or name
        except (OSError, ValueError, AttributeError):
            pass
    thumb = next((files[c.lower()] for c in (f"{name}.png", f"{folder.name}.png", "thumbnail.png", "preview.png")
                  if c.lower() in files), None)
    return LibraryEntry(name, str(folder), str(thumb) if thumb else "")


def scan_library(directory: str | Path) -> list[LibraryEntry]:
    """The characters in a folder of exported MetaHumans (one sub-folder each), sorted by name.

    The picture is <name>.png next to the DNA files and the name comes
    from ExportManifest.json. The folder itself counts too, so a single character's folder also works.
    """
    root = Path(directory)
    try:
        subs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    entries = [e for e in (_library_entry(f) for f in [root, *subs]) if e]
    return sorted(entries, key=lambda e: e.name.lower())


def dna_role(dna: DNA) -> str:
    """'head' when the DNA has face controls, otherwise 'body'."""
    return "head" if dna.definition.gui_control_names else "body"


def scan_folder(folder: str | Path) -> FolderScan:
    """Find the head DNA, body DNA and textures folder in a MetaHuman export folder."""
    folder = Path(folder)
    scan = FolderScan()
    candidates: dict[str, list[tuple[int, str]]] = {"head": [], "body": []}
    for path in sorted(folder.rglob("*.dna")):
        try:
            dna = read_dna(path, geometry=False)
        except Exception:
            continue
        scan.found.append(str(path))
        role = dna_role(dna)
        # A body DNA whose meshes already include the head ("combined") is a last resort.
        penalty = int(any("combined" in m.lower() for m in dna.definition.mesh_names))
        candidates[role].append((penalty, str(path)))
    if candidates["head"]:
        scan.head = min(candidates["head"])[1]
    if candidates["body"]:
        scan.body = min(candidates["body"])[1]
    maps = folder / "Maps"                       # MetaHuman Creator's Export to DCC puts textures here
    if maps.is_dir() and any(f.suffix.lower() in IMAGE_EXTS for f in maps.rglob("*")):
        scan.textures = str(maps)
        return scan
    best, best_count = "", 0
    for sub in [folder, *[p for p in folder.rglob("*") if p.is_dir()]]:
        count = sum(1 for f in sub.iterdir() if f.suffix.lower() in IMAGE_EXTS)
        if count > best_count:
            best, best_count = str(sub), count
    scan.textures = best
    return scan


def _lod_collection(root: bpy.types.Collection, lod: int) -> bpy.types.Collection:
    coll = ensure_collection(f"{root.name}_LOD{lod}", root)
    coll["mfb_lod"] = lod
    return coll


def assemble(opts: AssembleOptions) -> dict:
    """Build the character. Returns the created objects by role."""
    if not opts.head_dna and not opts.body_dna:
        raise ValueError("Choose a head DNA, a body DNA, or both")
    head = read_dna(opts.head_dna, blend_shapes=opts.blend_shapes) if opts.head_dna else None
    body = read_dna(opts.body_dna, blend_shapes=opts.blend_shapes) if opts.body_dna else None
    for dna, want in ((head, "head"), (body, "body")):
        if dna is not None and dna_role(dna) != want:
            raise ValueError(f"{dna.path.name} looks like a {dna_role(dna)} DNA, not a {want} DNA")

    name = opts.name or (Path(opts.head_dna or opts.body_dna).parent.name or "MetaHuman")
    if opts.new_scene:
        scene = bpy.data.scenes.new(name)
        bpy.context.window.scene = scene
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0 if opts.metres else 0.01
    scene.unit_settings.length_unit = "METERS" if opts.metres else "CENTIMETERS"

    root = bpy.data.collections.new(name)
    scene.collection.children.link(root)
    root[PROP_TEXTURES] = opts.textures
    rig_coll = ensure_collection(f"{name}_Rig", root)

    spaces = {k: Space.for_dna(d, opts.metres) for k, d in (("head", head), ("body", body)) if d}
    result: dict = {"collection": root, "meshes": []}

    # skeletons
    if head and body and opts.combine_skeletons:
        arm = build_combined_armature([(body, spaces["body"]), (head, spaces["head"])], f"{name}_rig", rig_coll)
        arm[PROP_DNA_PATH] = str(body.path)
        arm[PROP_HEAD_DNA] = str(head.path)
        arm[PROP_ROLE] = "combined"
        armatures = {"head": arm, "body": arm}
    else:
        armatures = {}
        for role, dna in (("body", body), ("head", head)):
            if dna:
                a = build_armature(dna, spaces[role], f"{name}_{role}_rig", rig_coll)
                a[PROP_ROLE] = role
                if role == "head":
                    a[PROP_HEAD_DNA] = str(dna.path)
                armatures[role] = a
        if "head" in armatures and "body" in armatures:
            follow_bones(armatures["head"], armatures["body"])
    result["armatures"] = armatures

    # meshes, one collection per LOD
    build_opts = BuildOptions(lods=opts.lods, mesh_filter=opts.mesh_filter,
                              blend_shapes=opts.blend_shapes, metres=opts.metres)
    for role, dna in (("body", body), ("head", head)):
        if dna is None:
            continue
        d = dna.definition
        for lod in sorted(opts.lods):
            if lod >= dna.descriptor.lod_count:
                continue
            coll = _lod_collection(root, lod)
            for mi in d.lod_meshes.for_lod(lod):
                mname = d.mesh_names[int(mi)]
                if opts.mesh_filter is not None and mname not in opts.mesh_filter:
                    continue
                obj = build_mesh(dna, spaces[role], int(mi), coll, armatures[role], build_opts)
                obj[PROP_ROLE] = role
                result["meshes"].append(obj)
    # show only the lowest LOD chosen
    lowest = min(opts.lods)
    for child in root.children:
        if "mfb_lod" in child:
            layer = bpy.context.view_layer.layer_collection.children[root.name].children[child.name]
            layer.hide_viewport = child["mfb_lod"] != lowest

    # materials (textures found by name in the textures folder) and wrinkle maps
    if opts.materials:
        head_maps = head.definition.animated_map_names if head else []
        result["materials"] = materials.apply_materials(result["meshes"], opts.textures, name, head_maps)
    if opts.lights:
        target = armatures["head"].matrix_world @ armatures["head"].data.bones[0].head_local             if head else (0.0, 0.0, 1.5)
        face_root = armatures.get("head") and armatures["head"].data.bones.get(rig.FACE_ROOT)
        if face_root:
            target = armatures["head"].matrix_world @ face_root.head_local
        result["lights"] = materials.build_light_rig(ensure_collection(f"{name}_Lights", root), tuple(target))

    # body RigLogic: RBF correctives and twist/swing joints follow the body pose
    if body and opts.body_rig:
        rig.setup_body_rig(armatures["body"], str(body.path), lod=lowest)

    # face board + live RigLogic
    if head and opts.face_board:
        result["face_board"] = rig.setup_rig(armatures["head"], rig_coll, lod=lowest)
    return result
