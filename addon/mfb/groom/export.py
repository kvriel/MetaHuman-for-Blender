"""Export Blender hair (Curves objects) as an Alembic groom for Unreal Engine.

Follows Epic's "Alembic for Grooms" schema (version 1.5) as read by UE 5.7's
AlembicHairTranslator:
  - curve shape from the Alembic ICurves schema; width (diameter) from Blender's radius
  - per-strand groom_group_id, groom_group_name, groom_guide, groom_id, groom_root_uv (uniform scope);
    groom_group_name is the group's name, or its ID when it has none (as Epic's exporter does)
  - per-vertex groom_color (vertex scope), when the hair has a 'color' attribute
  - groom-level groom_version_major/minor, groom_tool, groom_properties as user
    properties on the first Alembic xform

Blender's own Alembic exporter does the writing: point-domain attributes become
vertex-scope geom params and curve-domain attributes become uniform-scope geom params.
We build temporary Curves objects that already hold Unreal's units and attributes,
export only those, then remove them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

GROOM_VERSION = (1, 5)
TOOL_NAME = "MetaHuman for Blender - Groom Exporter"
DEFAULT_RADIUS_M = 0.00005          # used when the hair has no radius attribute (0.1 mm wide)


@dataclass
class GroomEntry:
    obj: bpy.types.Object
    group_id: int = 0
    group_name: str = ""
    guides: bool = False


@dataclass
class ExportOptions:
    up_axis: str = "Y"              # "Y": Unreal's default groom import settings; "Z": keep Blender's Z-up
    scale: float = 100.0            # Blender metres -> Unreal centimetres
    root_uv: bool = True
    color: bool = True
    strand_ids: bool = True
    default_radius: float = DEFAULT_RADIUS_M
    frame: int | None = None


@dataclass
class ExportReport:
    filepath: str
    grooms: list[dict] = field(default_factory=list)
    strands: int = 0
    points: int = 0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- reading hair


def _attr(curves, name, domain=None):
    a = curves.attributes.get(name)
    if a is None or (domain and a.domain != domain):
        return None
    return a


def _read(a, width):
    n = len(a.data)
    out = np.zeros(n * width, np.float32)
    key = "value" if width == 1 and a.data_type in {"FLOAT", "INT", "BOOLEAN", "INT8"} else \
        ("color" if a.data_type in {"FLOAT_COLOR", "BYTE_COLOR"} else "vector")
    if a.data_type in {"INT", "INT8", "BOOLEAN"}:
        tmp = np.zeros(n, np.int64 if a.data_type != "BOOLEAN" else bool)
        a.data.foreach_get("value", tmp)
        return tmp.astype(np.float32)
    if key == "color":
        out = np.zeros(n * 4, np.float32)
        a.data.foreach_get("color", out)
        return out.reshape(n, 4)
    a.data.foreach_get(key, out)
    return out.reshape(n, width) if width > 1 else out


def surface_root_uvs(curves_obj: bpy.types.Object, roots_world: np.ndarray, depsgraph) -> np.ndarray | None:
    """Root UVs by projecting each strand root onto the hair's surface mesh (nearest point)."""
    surf = curves_obj.data.surface
    if surf is None or surf.type != "MESH":
        return None
    surf_eval = surf.evaluated_get(depsgraph)
    me = surf_eval.to_mesh()
    try:
        uv_name = curves_obj.data.surface_uv_map or (me.uv_layers.active.name if me.uv_layers.active else "")
        uv_layer = me.uv_layers.get(uv_name) if uv_name else None
        if uv_layer is None:
            return None
        me.calc_loop_triangles()
        verts = [v.co.copy() for v in me.vertices]
        tris = [tuple(lt.vertices) for lt in me.loop_triangles]
        tri_loops = [tuple(lt.loops) for lt in me.loop_triangles]
        if not tris:
            return None
        tree = BVHTree.FromPolygons(verts, tris)
        to_local = surf_eval.matrix_world.inverted()
        uvs = np.zeros((len(roots_world), 2), np.float32)
        for i, p in enumerate(roots_world):
            loc, _n, idx, _d = tree.find_nearest(to_local @ Vector(p))
            if idx is None:
                continue
            tri = tris[idx]
            w = poly_3d_calc([verts[k] for k in tri], loc)
            uv = sum((Vector(uv_layer.data[l].uv) * wk for l, wk in zip(tri_loops[idx], w)), Vector((0.0, 0.0)))
            uvs[i] = (uv.x, uv.y)
        return uvs
    finally:
        surf_eval.to_mesh_clear()


@dataclass
class _HairData:
    sizes: np.ndarray
    positions: np.ndarray       # world space, metres
    radius: np.ndarray          # per point, metres (world scale applied)
    root_uv: np.ndarray | None
    color: np.ndarray | None    # (P, 3) or (C, 3)
    color_domain: str = "POINT"


def read_hair(obj: bpy.types.Object, depsgraph, opts: ExportOptions, report: ExportReport) -> _HairData:
    ev = obj.evaluated_get(depsgraph)
    c = ev.data
    n_curves, n_points = len(c.curves), len(c.points)
    offsets = np.zeros(n_curves + 1, np.int32)
    if n_curves:
        c.curve_offset_data.foreach_get("value", offsets)
    sizes = np.diff(offsets).astype(np.int32)
    local = np.zeros(n_points * 3, np.float32)
    c.position_data.foreach_get("vector", local)
    local = local.reshape(-1, 3)
    mw = np.array(ev.matrix_world, dtype=np.float64)
    world = local @ mw[:3, :3].T + mw[:3, 3]
    obj_scale = float(np.cbrt(abs(np.linalg.det(mw[:3, :3])))) or 1.0

    ra = _attr(c, "radius", "POINT")
    if ra is not None:
        radius = _read(ra, 1) * obj_scale
    else:
        radius = np.full(n_points, opts.default_radius, np.float32)
        report.warnings.append(f"{obj.name}: no radius attribute, using {opts.default_radius * 1000:.2f} mm")

    root_uv = None
    if opts.root_uv and n_curves:
        sa = _attr(c, "surface_uv_coordinate", "CURVE")
        if sa is not None:
            root_uv = _read(sa, 2)
        else:
            starts = np.concatenate(([0], np.cumsum(sizes)[:-1])).astype(np.intp)
            root_uv = surface_root_uvs(obj, world[starts], depsgraph)
            if root_uv is None:
                root_uv = np.zeros((n_curves, 2), np.float32)
                report.warnings.append(f"{obj.name}: no surface with UVs, root UVs set to 0 "
                                       "(Unreal falls back to its own spherical mapping only when the attribute is absent)")

    color, cdom = None, "POINT"
    if opts.color:
        ca = c.attributes.get("color")
        if ca is not None and ca.domain in {"POINT", "CURVE"}:
            if ca.data_type in {"FLOAT_COLOR", "BYTE_COLOR"}:
                color = _read(ca, 4)[:, :3]
            elif ca.data_type == "FLOAT_VECTOR":
                color = _read(ca, 3)
            cdom = ca.domain
    return _HairData(sizes, world, radius, root_uv, color, cdom)


# --------------------------------------------------------------------------- export


# Blender's Alembic exporter converts Blender's Z-up to Alembic's Y-up: positions AND every
# float3 vector attribute go through (x, y, z) -> (x, z, -y). Feeding it data multiplied by the
# inverse (x, y, z) -> (x, -z, y) cancels that conversion where we need the raw values.
_TO_YUP_INV = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], np.float64)


def _undo_yup(v: np.ndarray) -> np.ndarray:
    return np.asarray(v, np.float64) @ _TO_YUP_INV.T


def _make_export_object(name: str, hair: _HairData, entry: GroomEntry, opts: ExportOptions,
                        first_id: int, collection, groom_props: dict) -> bpy.types.Object:
    cd = bpy.data.hair_curves.new(name)
    cd.add_curves(hair.sizes.tolist())
    # Unreal reads strands as polylines. Blender writes its default Catmull-Rom curves with a
    # knot vector whose length Unreal's hair importer rejects, so export linear (poly) curves.
    cd.set_types(type="POLY")
    pos = hair.positions * opts.scale
    if opts.up_axis == "Z":            # keep Blender's Z-up in the file
        pos = _undo_yup(pos)
    cd.position_data.foreach_set("vector", pos.astype(np.float32).ravel())
    r = cd.attributes.get("radius") or cd.attributes.new("radius", "FLOAT", "POINT")
    r.data.foreach_set("value", (hair.radius * opts.scale).astype(np.float32))   # exported as width = 2r
    n = len(hair.sizes)
    gid = cd.attributes.new("groom_group_id", "INT", "CURVE")
    gid.data.foreach_set("value", np.full(n, entry.group_id, np.int32))
    gname = cd.attributes.new("groom_group_name", "STRING", "CURVE")
    label = (entry.group_name or str(entry.group_id)).encode("utf-8")
    for item in gname.data:                      # string attributes have no foreach_set
        item.value = label
    guide = cd.attributes.new("groom_guide", "INT", "CURVE")
    guide.data.foreach_set("value", np.full(n, 1 if entry.guides else 0, np.int32))
    if opts.strand_ids:
        sid = cd.attributes.new("groom_id", "INT", "CURVE")
        sid.data.foreach_set("value", np.arange(first_id, first_id + n, dtype=np.int32))
    if hair.root_uv is not None:
        uv = cd.attributes.new("groom_root_uv", "FLOAT2", "CURVE")
        uv.data.foreach_set("vector", hair.root_uv.astype(np.float32).ravel())
    if hair.color is not None:
        col = cd.attributes.new("groom_color", "FLOAT_VECTOR", hair.color_domain)
        col.data.foreach_set("vector", _undo_yup(hair.color).astype(np.float32).ravel())   # colours are not axes
    obj = bpy.data.objects.new(name, cd)
    for k, v in groom_props.items():
        obj[k] = v
    collection.objects.link(obj)
    return obj


def _safe_name(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in s) or "groom"


def export_grooms(context, entries: list[GroomEntry], filepath: str, opts: ExportOptions) -> ExportReport:
    if not entries:
        raise ValueError("No grooms chosen for export")
    # Unreal's groom importer treats a file with a guides-only group as a guide cache and creates
    # no groom asset, so guides must share a group ID with the hair they guide.
    strand_groups = {e.group_id for e in entries if not e.guides}
    lonely = sorted({e.group_id for e in entries if e.guides} - strand_groups)
    if lonely:
        raise ValueError(f"Group ID {', '.join(map(str, lonely))} has only guide curves. Unreal cannot import "
                         "guides on their own: give the guides the same group ID as the hair they guide")
    report = ExportReport(filepath)
    scene = context.scene
    if opts.frame is not None:
        scene.frame_set(opts.frame)
    depsgraph = context.evaluated_depsgraph_get()
    groom_props = {
        "groom_version_major": GROOM_VERSION[0],
        "groom_version_minor": GROOM_VERSION[1],
        "groom_tool": f"{TOOL_NAME} (Blender {bpy.app.version_string})",
        "groom_properties": json.dumps({"up_axis": opts.up_axis, "scale": opts.scale,
                                        "groups": [(e.group_id, e.group_name) for e in entries]}),
    }

    temp_coll = bpy.data.collections.new("_mfb_groom_export")
    scene.collection.children.link(temp_coll)
    view_layer = context.view_layer
    prev_sel = [o for o in view_layer.objects if o.select_get()]
    prev_active = view_layer.objects.active
    made: list[bpy.types.Object] = []
    try:
        next_id = 0
        for e in entries:
            if e.obj is None or e.obj.type != "CURVES":
                report.warnings.append(f"Skipped {getattr(e.obj, 'name', '?')}: not a hair Curves object")
                continue
            hair = read_hair(e.obj, depsgraph, opts, report)
            if not len(hair.sizes):
                report.warnings.append(f"Skipped {e.obj.name}: no strands")
                continue
            label = _safe_name(e.group_name or e.obj.name) + ("_guides" if e.guides else "")
            made.append(_make_export_object(label, hair, e, opts, next_id, temp_coll, groom_props))
            next_id += len(hair.sizes)
            report.grooms.append({"object": e.obj.name, "group_id": e.group_id, "group_name": e.group_name,
                                  "guides": e.guides, "strands": int(len(hair.sizes)),
                                  "points": int(hair.sizes.sum())})
            report.strands += int(len(hair.sizes))
            report.points += int(hair.sizes.sum())
        if not made:
            raise ValueError("Nothing to export: " + "; ".join(report.warnings))
        for o in view_layer.objects:
            o.select_set(False)
        for o in made:
            o.select_set(True)
        view_layer.objects.active = made[0]
        frame = scene.frame_current
        from ..blender.ctx import window_override
        with bpy.context.temp_override(**window_override()):      # works from scripts and timers too
            result = bpy.ops.wm.alembic_export(
                filepath=filepath, check_existing=False, selected=True, start=frame, end=frame,
                export_custom_properties=True, uvs=False, normals=False, vcolors=False, orcos=False,
                face_sets=False, export_hair=False, export_particles=False, curves_as_mesh=False,
                flatten=False, global_scale=1.0, as_background_job=False)
        if result != {"FINISHED"}:
            raise RuntimeError(f"Alembic export failed: {result}")
    finally:
        prev_names = {o.name for o in prev_sel}
        active_name = prev_active.name if prev_active else None
        for o in made:
            data = o.data
            bpy.data.objects.remove(o)
            bpy.data.hair_curves.remove(data)
        bpy.data.collections.remove(temp_coll)
        for o in view_layer.objects:
            if o is not None:
                o.select_set(o.name in prev_names)
        view_layer.objects.active = view_layer.objects.get(active_name) if active_name else None
    return report
