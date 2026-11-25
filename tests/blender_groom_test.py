"""Headless test of the Groom Exporter.

"C:\\Program Files\\Blender Foundation\\Blender 5.3\\blender.exe" -b --factory-startup \
    --python tests/blender_groom_test.py -- <out_dir>

Builds a scalp mesh with UVs and two hair Curves objects (strands + guides), exports them,
then checks the Alembic file two ways:
  1. a small read-only Ogawa/Alembic reader below (property names, scope, POD type, values)
  2. Blender's own Alembic importer (libAlembic) for curve/point counts and the up axis
Also checks presets and the list operators.
"""
import math
import struct
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _testutil import reset_scene  # noqa: E402

OUT = (Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else ROOT / "tests/out/groom").resolve()
OUT.mkdir(parents=True, exist_ok=True)
results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}{(' - ' + str(detail)) if detail != '' else ''}")


# --------------------------------------------------------------------------- minimal Alembic (Ogawa) reader
# Read-only, for verification. Layout per the open Alembic/Ogawa format.

POD = {0: "bool", 1: "uint8", 2: "int8", 3: "uint16", 4: "int16", 5: "uint32", 6: "int32", 7: "uint64",
       8: "int64", 9: "float16", 10: "float32", 11: "float64", 12: "string", 13: "wstring"}
NP = {"bool": "<u1", "uint8": "<u1", "int8": "<i1", "uint16": "<u2", "int16": "<i2", "uint32": "<u4",
      "int32": "<i4", "uint64": "<u8", "int64": "<i8", "float32": "<f4", "float64": "<f8"}
DATA_BIT = 0x8000000000000000


class Ogawa:
    def __init__(self, path):
        self.b = Path(path).read_bytes()
        assert self.b[:5] == b"Ogawa", "not an Ogawa file"
        self.root = struct.unpack_from("<Q", self.b, 8)[0]

    def group(self, pos):
        if pos == 0:
            return []
        n = struct.unpack_from("<Q", self.b, pos)[0]
        return list(struct.unpack_from(f"<{n}Q", self.b, pos + 8))

    def data(self, ref):
        pos = ref & ~DATA_BIT
        if pos == 0:
            return b""
        size = struct.unpack_from("<Q", self.b, pos)[0]
        return self.b[pos + 8:pos + 8 + size]


def _read_sized(buf, off, hint):
    fmt = {0: "<B", 1: "<H", 2: "<I"}[hint]
    return struct.unpack_from(fmt, buf, off)[0], off + struct.calcsize(fmt)


class Alembic:
    def __init__(self, path):
        self.o = Ogawa(path)
        top = self.o.group(self.o.root)
        self.indexed_md = [""]
        md = self.o.data(top[5]) if len(top) > 5 else b""
        off = 0
        while off < len(md):
            n = md[off]; off += 1
            self.indexed_md.append(md[off:off + n].decode()); off += n
        self.top_object = top[2]

    def _md(self, idx, buf, off, hint):
        if idx == 0xFF:
            n, off = _read_sized(buf, off, hint)
            return buf[off:off + n].decode(), off + n
        return (self.indexed_md[idx] if idx < len(self.indexed_md) else ""), off

    def property_headers(self, compound_ref):
        kids = self.o.group(compound_ref)
        if not kids:
            return []
        buf = self.o.data(kids[-1])
        off, out, i = 0, [], 0
        while off < len(buf):
            info = struct.unpack_from("<I", buf, off)[0]; off += 4
            ptype = info & 0x3
            hint = (info & 0xC) >> 2
            p = {"type": ("compound", "scalar", "array", "array")[ptype], "ref": kids[i]}
            if ptype:
                p["pod"] = POD.get((info & 0xF0) >> 4, "?")
                p["extent"] = (info & 0xFF000) >> 12
                p["samples"], off = _read_sized(buf, off, hint)
                if info & 0x200:
                    _, off = _read_sized(buf, off, hint)
                    _, off = _read_sized(buf, off, hint)
                if info & 0x100:
                    _, off = _read_sized(buf, off, hint)
            n, off = _read_sized(buf, off, hint)
            p["name"] = buf[off:off + n].decode(); off += n
            p["meta"], off = self._md((info & 0xFF00000) >> 20, buf, off, hint)
            out.append(p)
            i += 1
        return out

    def values(self, prop):
        kids = self.o.group(prop["ref"])
        raw = self.o.data(kids[0])[16:]          # skip the 16-byte sample key
        if prop["pod"] == "string":
            return [s.decode() for s in raw.split(b"\0") if s]
        arr = np.frombuffer(raw, dtype=NP[prop["pod"]])
        return arr.reshape(-1, prop["extent"]) if prop["extent"] > 1 else arr

    def objects(self, obj_ref=None, path=""):
        """Yield (path, object group ref) for every object."""
        obj_ref = self.top_object if obj_ref is None else obj_ref
        kids = self.o.group(obj_ref)
        if len(kids) < 2:
            return
        hdr = self.o.data(kids[-1])
        off, names = 0, []
        while off + 4 <= len(hdr) - 32:
            n = struct.unpack_from("<I", hdr, off)[0]; off += 4
            names.append(hdr[off:off + n].decode()); off += n
            mdi = hdr[off]; off += 1
            if mdi == 0xFF:
                m = struct.unpack_from("<I", hdr, off)[0]; off += 4 + m
        for name, child in zip(names, kids[1:-1]):
            yield f"{path}/{name}", child
            yield from self.objects(child, f"{path}/{name}")

    def find(self, compound_ref, *names):
        props = self.property_headers(compound_ref)
        for nm in names:
            hit = next((p for p in props if p["name"] == nm), None)
            if hit is None:
                return None
            props = self.property_headers(hit["ref"]) if hit["type"] == "compound" else None
            last = hit
        return last

    def top_props(self, obj_ref):
        return self.o.group(obj_ref)[0]


# --------------------------------------------------------------------------- scene


def make_scalp():
    """A 20 x 20 cm curved patch, UV = (x, y) mapped to [0, 1]."""
    n = 11
    verts, uvs_v = [], []
    for j in range(n):
        for i in range(n):
            u, v = i / (n - 1), j / (n - 1)
            x, y = -0.1 + 0.2 * u, -0.1 + 0.2 * v
            verts.append((x, y, 1.6 + 0.2 * math.cos(x * 4) * math.cos(y * 4)))
            uvs_v.append((u, v))
    faces = [(j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i) for j in range(n - 1) for i in range(n - 1)]
    me = bpy.data.meshes.new("scalp")
    me.from_pydata(verts, [], faces)
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for li in poly.loop_indices:
            uv.data[li].uv = uvs_v[me.loops[li].vertex_index]
    ob = bpy.data.objects.new("scalp", me)
    bpy.context.scene.collection.objects.link(ob)
    return ob, verts, uvs_v


def make_hair(name, roots, points, radius, color=None, surface=None):
    cd = bpy.data.hair_curves.new(name)
    cd.add_curves([points] * len(roots))
    pos = []
    for r in roots:
        for k in range(points):
            pos.append((r[0] + 0.002 * k, r[1], r[2] + 0.01 * k))
    cd.position_data.foreach_set("vector", np.array(pos, np.float32).ravel())
    ra = cd.attributes.get("radius") or cd.attributes.new("radius", "FLOAT", "POINT")
    ra.data.foreach_set("value", np.full(len(pos), radius, np.float32))
    if color is not None:
        ca = cd.attributes.new("color", "FLOAT_COLOR", "POINT")
        ca.data.foreach_set("color", np.tile(np.array(color + (1.0,), np.float32), len(pos)))
    ob = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(ob)
    if surface is not None:
        cd.surface = surface
        cd.surface_uv_map = "UVMap"
    return ob


reset_scene()
import mfb.groom as groom  # noqa: E402
groom.register()
from mfb.groom import export as gexport, presets  # noqa: E402

scalp, sverts, suvs = make_scalp()
root_idx = [12, 30, 56, 77, 98]                  # vertices on the scalp grid
roots = [sverts[i] for i in root_idx]
strands = make_hair("hair_strands", roots, 6, 0.000035, color=(0.31, 0.17, 0.09), surface=scalp)
guides = make_hair("hair_guides", roots[:3], 4, 0.0002, surface=scalp)
expected_uv = np.array([suvs[i] for i in root_idx], np.float32)

# list operators
s = bpy.context.scene.mfb_groom
bpy.ops.mfb.groom_refresh()
check("refresh lists the hair objects", sorted(it.obj.name for it in s.items) == ["hair_guides", "hair_strands"])
check("guides detected from the name", next(it for it in s.items if it.obj.name == "hair_guides").guides)
bpy.ops.mfb.groom_select(state=False)
check("Clear Selection excludes all", not any(it.include for it in s.items))
bpy.ops.mfb.groom_select(state=True)
check("Select All includes all", all(it.include for it in s.items))
bpy.ops.mfb.groom_set_group(group_id=7, group_name="hair")
check("Set Group ID on included grooms", all(it.group_id == 7 and it.group_name == "hair" for it in s.items))
s.index = [it.obj.name for it in s.items].index("hair_strands")
bpy.ops.mfb.groom_toggle_guides(); bpy.ops.mfb.groom_toggle_guides()
check("Toggle Guides flips and flips back", not s.items[s.index].guides)
next(it for it in s.items if it.obj.name == "hair_guides").group_id = 3

# presets
preset = OUT / "groom_preset.json"
s.up_axis = "Y"
bpy.ops.mfb.groom_preset_save(filepath=str(preset))
for it in s.items:
    it.group_id = 0
s.up_axis = "Z"
bpy.ops.mfb.groom_preset_load(filepath=str(preset))
ids = {it.obj.name: it.group_id for it in s.items}
check("preset save/open restores groups and options", ids == {"hair_strands": 7, "hair_guides": 3} and s.up_axis == "Y",
      ids)

# guides alone in a group: Unreal would import no groom, so the exporter refuses
bad = OUT / "groom_guides_alone.abc"
try:
    bpy.ops.mfb.groom_export(filepath=str(bad))
    refused = False
except RuntimeError as exc:
    refused = "only guide curves" in str(exc)
check("export refuses guides without hair in their group", refused and not bad.exists())
next(it for it in s.items if it.obj.name == "hair_guides").group_id = 7      # guides belong to the hair group

# --- export Y-up (default)
abc = OUT / "groom_yup.abc"
r = bpy.ops.mfb.groom_export(filepath=str(abc))
check("export operator finishes", r == {"FINISHED"} and abc.exists())

a = Alembic(abc)
objs = dict(a.objects())
curves = {p: ref for p, ref in objs.items() if p.count("/") == 2}
check("one xform + ICurves per groom", len(curves) == 2, sorted(curves))

geom_by_obj = {}
for path, ref in curves.items():
    geom = a.find(a.top_props(ref), ".geom")
    geom_by_obj[path.split("/")[1]] = geom
strands_geom = geom_by_obj.get("hair") or next(g for k, g in geom_by_obj.items() if "guides" not in k)
guides_geom = next(g for k, g in geom_by_obj.items() if "guides" in k)


def arb(geom, name):
    return a.find(geom["ref"], ".arbGeomParams", name)


for label, geom, n_curves, n_pts, gid, is_guide in (("strands", strands_geom, 5, 30, 7, 0),
                                                     ("guides", guides_geom, 3, 12, 7, 1)):
    nv = a.find(geom["ref"], "nVertices")
    check(f"{label}: curve count", len(a.values(nv)) == n_curves, len(a.values(nv)))
    P = a.find(geom["ref"], "P")
    check(f"{label}: point count", len(a.values(P)) == n_pts)
    g = arb(geom, "groom_group_id")
    check(f"{label}: groom_group_id int32 uniform = {gid}",
          g and g["pod"] == "int32" and "geoScope=uni" in g["meta"] and set(a.values(g).tolist()) == {gid},
          (g or {}).get("meta"))
    gn = arb(geom, "groom_group_name")
    gnv = [str(v) for v in a.values(gn)] if gn else []
    check(f"{label}: groom_group_name string uniform = 'hair'",
          gn and gn["pod"] == "string" and "geoScope=uni" in gn["meta"] and len(gnv) == n_curves
          and set(gnv) == {"hair"}, f"{(gn or {}).get('pod')} {(gn or {}).get('meta')} {gnv[:2]}")
    gd = arb(geom, "groom_guide")
    check(f"{label}: groom_guide uniform = {is_guide}",
          gd and "geoScope=uni" in gd["meta"] and set(a.values(gd).tolist()) == {is_guide})
    uv = arb(geom, "groom_root_uv")
    vals = a.values(uv) if uv else None
    want = expected_uv[:n_curves]
    check(f"{label}: groom_root_uv float32[2] uniform matches surface UVs",
          uv and uv["pod"] == "float32" and uv["extent"] == 2 and "geoScope=uni" in uv["meta"]
          and np.allclose(vals, want, atol=1e-3), None if vals is None else np.round(vals[:2], 3).tolist())
    sid = arb(geom, "groom_id")
    check(f"{label}: groom_id per strand", sid and len(a.values(sid)) == n_curves)
    w = a.find(geom["ref"], "width") or a.find(geom["ref"], ".widths")
    wv = a.values(w) if w else None
    radius = 0.000035 if label == "strands" else 0.0002
    check(f"{label}: width is diameter in cm (2 x radius x 100)",
          wv is not None and np.allclose(wv, 2 * radius * 100, rtol=1e-4), None if wv is None else float(wv[0]))

col = arb(strands_geom, "groom_color")
check("strands: groom_color float32[3] per vertex",
      col and col["extent"] == 3 and "geoScope=vtx" in col["meta"]
      and np.allclose(a.values(col)[0], (0.31, 0.17, 0.09), atol=1e-4),
      None if not col else (col["meta"], col["extent"], a.values(col)[:1].tolist()))
check("guides: no groom_color (no colour attribute)", arb(guides_geom, "groom_color") is None)

first_xform = sorted(curves)[0].rsplit("/", 1)[0]
up = a.find(a.top_props(objs[first_xform]), ".xform", ".userProperties")
upn = {p["name"]: p for p in a.property_headers(up["ref"])} if up else {}
check("groom_version_major/minor = 1/5 on the first xform",
      "groom_version_major" in upn and int(a.values(upn["groom_version_major"])[0]) == 1
      and int(a.values(upn["groom_version_minor"])[0]) == 5, sorted(upn))
check("groom_tool written", "groom_tool" in upn and "Groom Exporter" in " ".join(a.values(upn["groom_tool"])))

# --- cross-check with Blender's own Alembic reader (libAlembic): counts and up axis
src_first_root = np.array(roots[0]) * 100.0


def reimport(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.alembic_import(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before and o.type == "CURVES"]
    bpy.context.view_layer.update()
    return new


new = reimport(abc)
counts = sorted((len(o.data.curves), len(o.data.points)) for o in new)
check("Blender's Alembic reader opens the file: curve/point counts", counts == [(3, 12), (5, 30)], counts)
hair_new = next(o for o in new if len(o.data.curves) == 5)
p0 = hair_new.matrix_world @ hair_new.data.points[0].position
check("Y-up file reads back at the source position, in cm", np.allclose(tuple(p0), src_first_root, atol=1e-3),
      tuple(round(v, 3) for v in p0))
for o in new:
    bpy.data.objects.remove(o)

# --- Z-up export: the file's own coordinates are Blender's (x, y, z) in cm
s.up_axis = "Z"
abc_z = OUT / "groom_zup.abc"
bpy.ops.mfb.groom_export(filepath=str(abc_z))
az = Alembic(abc_z)
zcurves = [ref for p, ref in az.objects() if p.count("/") == 2]
xf_ok, p_ok = True, False
for p, ref in az.objects():
    if p.count("/") == 1:
        vals = az.find(az.top_props(ref), ".xform", ".vals")
        if vals is not None:
            m = az.values(vals)
            xf_ok &= bool(np.allclose(np.asarray(m, float).ravel()[:16], np.eye(4).ravel(), atol=1e-5))
for ref in zcurves:
    P = az.find(az.find(az.top_props(ref), ".geom")["ref"], "P")
    pts = az.values(P)
    p_ok |= bool(np.any(np.all(np.isclose(pts, src_first_root, atol=1e-3), axis=1)))
check("Z-up: points stored as Blender x, y, z in cm with identity xform", p_ok and xf_ok)

s.up_axis = "Y"
print(f"\n{sum(results)}/{len(results)} checks passed")
groom.unregister()
